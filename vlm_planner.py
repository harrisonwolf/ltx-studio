#!/usr/bin/env python
"""Persistent CONSULT daemon: a conversational creative director for the LTX studio.

Loads Qwen2.5-VL-7B (4-bit) ONCE and stays resident (fast turns), serving a chat over
JSON-lines on stdin/stdout. Runs in its own venv (transformers>=4.49 + bnb). All logs to
stderr; stdout carries ONLY protocol JSON (one object per line).

Protocol:
  -> {"messages":[{"role":"user"|"assistant","text":"..."}], "image":"path"|null}
  <- {"reply":"<plain text>", "config":{...dials...}}
  first line emitted after load: {"ready":true}
  send {"quit":true} to exit.
"""
import json
import gc
import re
import sys
import threading


def log(*a):
    print(*a, file=sys.stderr, flush=True)


import os  # noqa: E402
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")  # de-fragment the cap so the 8B fits
import torch  # noqa: E402
from gpu_budget import cap_vram; cap_vram()  # noqa: E402  -- leave ~12% VRAM free for the OS (always safe; the 8B is probe-gated)
from transformers import (Qwen3VLForConditionalGeneration, AutoProcessor,  # noqa: E402
                          BitsAndBytesConfig, TextIteratorStreamer)
from qwen_vl_utils import process_vision_info  # noqa: E402

MODEL_4B = "Qwen/Qwen3-VL-4B-Instruct"           # smaller fallback brain (fp16 source for CPU load)
QDIR_4B = "/home/wolve/video_gen/qwen3vl4b_nf4"   # 4B nf4 prequant (~2.7GB, fits alongside Windows VRAM)
QDIR_8B = "/home/wolve/video_gen/qwen3vl8b_nf4"   # 8B nf4 prequant — built but does NOT fit 8GB at inference; reserved for cloud/bigger GPU

try:
    from dials_help import dial_guide       # the studio's dial tooltips -> the director reads the SAME guidance
    _DIAL_GUIDE = dial_guide()
except Exception:
    _DIAL_GUIDE = "(dial guide unavailable)"

SYSTEM = (
    "You are the CREATIVE DIRECTOR and technical operator of a local AI video studio that runs two local "
    "video models - LTX-Video (fast) and Wan-VACE (slower, higher fidelity) - on a single 8GB GPU. The "
    "user tells you in plain language what they want to make; you "
    "help shape it - ask a brief clarifying question when it truly helps, suggest creative directions, "
    "and translate everything into a concrete, RUNNABLE configuration. Be warm, concise, and decisive. "
    "ALWAYS reply in English.\n\n"
    "HOW THE STUDIO WORKS (respect these):\n"
    "- Long videos are built by chaining short ~2-2.5s shots, so LENGTH is effectively free (it only adds "
    "shots and time) - don't be shy about duration. RESOLUTION is the hard limit, not length.\n"
    "- mode 'single' = one short clip (a few seconds, no evolution). mode 'director' = a longer piece "
    "supervised by a vision model. HOW IT REALLY STEERS: only steadiness 'evolve' follows the DIRECTIVE "
    "shot-by-shot (and evolve REQUIRES a directive distinct from the prompt, or the engine runs plain "
    "hold); 'hold'/'balanced' check the frame every ~3rd shot and only rewrite the prompt when the scene "
    "has visibly drifted — the directive is NOT used in those modes. Never promise per-shot steering "
    "unless you are recommending evolve WITH a real directive.\n"
    "- backend = the video model: 'ltx' (fast, lighter - best for drafts + quick iteration) or 'wan' "
    "(Wan-VACE-1.3B - slower but clearly higher fidelity + better motion - best for finals / photoreal). "
    "Default 'ltx'; choose 'wan' when the user wants the best look or accepts a slower render. CRITICAL: "
    "cfg differs by backend - use cfg ~3 for ltx, ~5 for wan.\n"
    "- res must be just the width number, one of '512' (lightest, longest shots), '704' (balanced), or "
    "'768' (sharpest, heaviest, shortest shots).\n"
    "- steps ~30 is good, ~50 is best but slower (wan looks good at ~20-30). fps 24 is cinematic (wan "
    "renders at its native 16fps automatically regardless).\n"
    "- prompt = the opening shot description. directive = the overall arc the director steers toward "
    "(director mode only). anchors = a short comma-separated list of subject + style to keep in every shot "
    "(the style leash) - ALWAYS put the medium here (e.g. 'oil painting', 'photoreal', '35mm film') so it "
    "sticks across shots. cond_strength (0-1) = how tightly each shot holds the previous shot's tail "
    "(1.0 = tight/consistent, lower = looser; use 1.0 for hold, ~0.6-0.8 to let things evolve).\n"
    "- steadiness (director mode) = how faithful to stay shot-to-shot: 'hold' (DEFAULT) reproduces the "
    "user's ONE requested scene consistently with NO story; 'balanced' = same subject+setting, gentle "
    "variation; 'evolve' = let it journey/transform. Choose 'hold' UNLESS the user explicitly asks for "
    "change, progression, a story, or a journey.\n"
    "- The models render realistic / representational subjects well; TRULY ABSTRACT art renders loosely - "
    "if the user asks for abstract, gently say so and offer a more concrete framing.\n"
    "- ANATOMY: these are small models. When the subject is an animal or person, favor a framing where its "
    "HEAD/FACE is clearly visible (in profile or facing the viewer) and the subject is prominent and fairly "
    "close - heads turned away, tiny, or obscured tend to render as a blur or deformed. Don't hide the hard parts.\n"
    "- PERSONIFYING / CROSS-SPECIES: these small models CANNOT blend species subtly. Asking for 'human-like "
    "eyes/gaze/face/expression' or 'humanoid' on an animal makes the model fuse a human face into it -> a mutant. "
    "Convey personification ONLY through pose, action, props and framing (e.g. 'sitting upright at the table, "
    "holding the hot dog in both front paws, head cocked toward the camera'), NEVER through human facial/anatomical "
    "words. Never attach human-anatomy terms to a non-human subject. When you personify an animal, ADD 'human face, "
    "human eyes, humanoid features, fused human-animal' to n_prompt.\n"
    "- If the user gives a reference image, read its style and subject and fold them into prompt + anchors; "
    "you may set 'image' to that exact path to use it as the literal opening frame.\n\n"
    "DIAL GUIDE (the exact guidance the user sees as the studio's tooltips - follow it; the key on the "
    "left is the config field name to emit):\n"
    + _DIAL_GUIDE + "\n\n"
    "CRAFTING GOOD SETTINGS (be deliberate, not lazy):\n"
    "- Reason through the scene first - subject, best framing (head/face clearly visible), medium, mood - "
    "then set EVERY dial on purpose. Be thorough, but keep the prose to a few sentences.\n"
    "- anchors: a RICH list of 3-6 items = subject + medium + 1-2 style/lighting/mood cues (e.g. 'a lone "
    "raccoon, photoreal, 35mm film, soft dawn light, shallow depth of field'), NOT one or two bare words. "
    "Richer anchors = a more consistent long clip.\n"
    "- n_prompt (negative): ALWAYS keep the anti-artifact guard ('blurry, low quality, deformed, malformed "
    "anatomy, extra or missing limbs, mutated, watermark, text') and ADD anything specific to exclude for "
    "THIS scene (e.g. 'modern objects, cars' for a period piece). Don't leave it generic when the scene "
    "has obvious things to avoid.\n"
    "- prompt: concrete + vivid - subject + action + setting + style, head/face clearly framed.\n\n"
    "EVERY reply MUST end with a fenced JSON config block holding the FULL config (carry unchanged fields "
    "forward each turn). Use lowercase 'ltx'/'wan' for backend. Example:\n"
    "```json\n"
    "{\"mode\":\"director\",\"backend\":\"ltx\",\"prompt\":\"...\",\"directive\":\"...\",\"anchors\":\"...\","
    "\"n_prompt\":\"...\",\"steadiness\":\"hold\",\"cond_strength\":1.0,\"image\":\"\",\"res\":\"704\","
    "\"seconds\":12,\"seg\":2.2,\"steps\":30,\"cfg\":3,\"seed\":0,\"fps\":24}\n"
    "```\n"
    "Before the block, give a short, plain-language explanation of your choices and any tradeoffs. "
    "If you change something the user asked about, say why."
)

def _balanced(s, start):
    depth = 0
    for j in range(start, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[start:j + 1], j + 1
    return None, None


def load():
    """The CONSULT runs GPU-EXCLUSIVE and uses the 4B — it fits the 8GB board with the full ~12% OS
    safety buffer intact. (The 8B is built + on disk but does NOT fit 8GB at inference: weights + the
    rich director prompt + a reply need ~6.5GB, which can't load under the safe cap and OOMs even at an
    unsafe-thin buffer. It's reserved for the cloud-offload path / a bigger GPU.) CONSULT_DEVICE=cpu
    forces the 4B onto CPU so you can consult while a render holds the GPU. Returns (model, proc, label)."""
    cpu_only = os.environ.get("CONSULT_DEVICE") == "cpu"
    if not cpu_only and os.path.isdir(QDIR_4B):
        try:
            proc = AutoProcessor.from_pretrained(QDIR_4B, max_pixels=512 * 512)
            m = Qwen3VLForConditionalGeneration.from_pretrained(QDIR_4B, device_map="cuda:0", dtype=torch.bfloat16)
            log("planner: Qwen3-VL-4B (GPU)")
            return m, proc, "Qwen3-VL-4B (GPU)"
        except Exception as e:
            msg = str(e)[:120]
            e = None
            gc.collect()
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
            log("planner: 4B GPU load failed (%s); falling back to CPU" % msg)
    # CPU fallback: fp16 4B on CPU (nf4 needs CUDA). Slow but never OOMs / never touches the GPU -> lets
    # you consult while a render holds the GPU.
    proc = AutoProcessor.from_pretrained(MODEL_4B, max_pixels=512 * 512)
    m = Qwen3VLForConditionalGeneration.from_pretrained(MODEL_4B, device_map="cpu", dtype=torch.bfloat16)
    label = "Qwen3-VL-4B (CPU, slow)"
    log("planner: %s" % label)
    return m, proc, label


RAW_SYSTEM = ("You are Qwen2.5-VL, a helpful, knowledgeable AI assistant. Respond naturally and "
              "directly to whatever the user asks, in plain prose. If the user attaches an image, look at "
              "it carefully and discuss it. Do not talk about video configs or act as a director unless "
              "the user explicitly asks.")


def build_messages(history, image, raw=False):
    msgs = [{"role": "system", "content": RAW_SYSTEM if raw else SYSTEM}]
    for i, h in enumerate(history):
        if h.get("role") == "assistant":
            msgs.append({"role": "assistant", "content": h.get("text", "")})
        else:
            content = []
            if image and i == len(history) - 1:      # attach reference to the latest user turn
                content.append({"type": "image", "image": image})
            content.append({"type": "text", "text": h.get("text", "")})
            msgs.append({"role": "user", "content": content})
    return msgs


def parse(resp):
    """Extract the config JSON robustly: prefer a fenced ```json block, else the last
    balanced {...} object that parses. Returns (reply_text, config_dict)."""
    cfg, lo, hi = {}, None, None
    for m in reversed(list(re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", resp, re.S))):
        # LAST parseable fenced block wins: replies often QUOTE the old config before giving the update
        try:
            cfg, lo, hi = json.loads(m.group(1)), m.start(), m.end()
            break
        except Exception:
            cfg = {}
    if not cfg:                                  # fenceless / malformed fence -> scan for a JSON object
        st = resp.rfind("{")
        while st != -1:
            blk, end = _balanced(resp, st)
            if blk:
                try:
                    cfg, lo, hi = json.loads(blk), st, end
                    break
                except Exception:
                    pass
            st = resp.rfind("{", 0, st)
    reply = (resp[:lo] + resp[hi:]) if (cfg and lo is not None) else resp
    reply = re.sub(r"```(?:json)?", "", reply).strip()
    return reply.strip(), cfg


def _stream_generate(model, proc, inputs, gkw, raw):
    """C2: generate with a token streamer -> emit {"chunk": <text>} lines as tokens arrive, then a final
    {"reply"...}. Generation runs in a worker thread; on any error the streamer is ended so the reader
    unblocks and we still emit whatever text streamed."""
    streamer = TextIteratorStreamer(proc.tokenizer, skip_prompt=True, skip_special_tokens=True)
    err = [None]

    def _gen():
        try:
            with torch.no_grad():
                model.generate(**inputs, streamer=streamer, **gkw)
        except Exception as e:
            err[0] = str(e)[:300]
            log("stream gen error:", repr(e)[:160])
            try:
                streamer.end()
            except Exception:
                pass

    th = threading.Thread(target=_gen)
    th.start()
    pieces = []
    for piece in streamer:
        if piece:
            pieces.append(piece)
            print(json.dumps({"chunk": piece}), flush=True)
    th.join()
    out = "".join(pieces)
    if err[0] and not out.strip():           # a failed generate must surface as an ERROR, not a blank reply
        print(json.dumps({"error": err[0]}), flush=True)
        return
    if raw:
        print(json.dumps({"reply": out.strip()}), flush=True)
    else:
        reply, cfg = parse(out)
        print(json.dumps({"reply": reply, "config": cfg}), flush=True)


def main():
    model, proc, _label = load()
    try:
        _dev = next(model.parameters()).device          # cuda for GPU loads, cpu for the CPU fallback
    except Exception:
        _dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(json.dumps({"ready": True, "info": _label}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            print(json.dumps({"error": f"bad request: {e}"}), flush=True)
            continue
        if req.get("quit"):
            break
        try:
            raw = bool(req.get("raw"))                # raw=True -> plain chat with the model, no config
            image = req.get("image") or None
            messages = build_messages(req.get("messages", []), image, raw)
            text = proc.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            image_inputs, video_inputs = process_vision_info(messages)
            inputs = proc(text=[text], images=image_inputs, videos=video_inputs,
                          padding=True, return_tensors="pt").to(_dev)
            gkw = dict(max_new_tokens=1024, do_sample=True, temperature=0.7, top_p=0.9)
            if req.get("stream"):                     # C2: stream tokens to the UI as they generate
                _stream_generate(model, proc, inputs, gkw, raw)
                continue
            with torch.no_grad():
                gen = model.generate(**inputs, **gkw)
            out = proc.batch_decode(gen[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
            if raw:
                print(json.dumps({"reply": out.strip()}), flush=True)
            else:
                reply, cfg = parse(out)
                print(json.dumps({"reply": reply, "config": cfg}), flush=True)
        except Exception as e:
            log("generation error:", e)
            print(json.dumps({"error": str(e)[:300]}), flush=True)


if __name__ == "__main__":
    main()
