#!/usr/bin/env python
"""One-shot Qwen2.5-VL-7B 'director': look at a seam frame and DIRECT the next ~2s shot.

It reasons about (a) the overall vision/arc, (b) where we are in it (shot k of N),
(c) the story so far (recent beats), and (d) what is actually on screen now, then decides
what should CHANGE next and writes the prompt for that beat. Runs in its own venv
(transformers>=4.49 + bitsandbytes 4-bit), isolated from LTX's pinned env. Loads, infers
once, exits -> frees the GPU. Prints ONLY the final prompt to stdout; logs to stderr.

  director_venv/bin/python vlm_director7b.py --image seam.png --orig_prompt "..." \
      --directive "..." --anchors "..." --prev "..." --history "a || b" --seg 2 --total 6
"""
import argparse
import re
import sys


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def _ascii1(s, n=200):
    """One ASCII line, no ']]', truncated -> safe inside a [[...]] marker."""
    s = " ".join(str(s).split())
    return s.encode("ascii", "ignore").decode().replace("]]", ") ")[:n]


ap = argparse.ArgumentParser()
ap.add_argument("--image", default="")
ap.add_argument("--orig_prompt", default="")      # core concept / medium / style
ap.add_argument("--directive", default="")         # the overall vision / arc (metaprompt)
ap.add_argument("--anchors", default="")            # style leash, honor don't list
ap.add_argument("--prev", default="")               # the prompt that made the shot just rendered
ap.add_argument("--history", default="")            # recent beats, "a || b || c"
ap.add_argument("--seg", type=int, default=1)       # shot just rendered
ap.add_argument("--total", type=int, default=2)     # estimated total shots
ap.add_argument("--steadiness", default="hold", choices=["hold", "balanced", "evolve"])
ap.add_argument("--quant", default="4bit", choices=["4bit", "bf16"])
ap.add_argument("--max_new_tokens", type=int, default=160)
ap.add_argument("--fit_check", action="store_true",
                help="judge fit first; answer KEEP (reuse the previous prompt verbatim) unless the frame drifted")
ap.add_argument("--daemon", action="store_true",
                help="resident mode: load once on CPU, serve per-seam JSON requests on stdin (no GPU eviction)")
args = ap.parse_args()

import os  # noqa: E402
import torch  # noqa: E402
from gpu_budget import cap_vram  # noqa: E402
if not args.daemon:              # one-shot GPU path caps VRAM; the resident CPU daemon never touches the GPU
    cap_vram()
from transformers import Qwen3VLForConditionalGeneration, AutoProcessor  # noqa: E402
from qwen_vl_utils import process_vision_info  # noqa: E402

MODEL = "Qwen/Qwen3-VL-4B-Instruct"            # newer/smarter than Qwen2.5-VL-7B, at half the size
QDIR = "/home/wolve/video_gen/qwen3vl4b_nf4"   # Qwen3-VL-4B nf4 (~2.7GB); faster reload than the old 7B

SYSTEM_PRE = (
    "You are the DIRECTOR of a continuous AI-generated video built from short ~2-second shots that flow "
    "seamlessly into one another. Each turn you are shown the LAST FRAME of the shot just rendered, and you "
    "write the prompt for the NEXT shot. The look of that last frame already carries over, so never just "
    "re-describe it. Always stay in the SAME medium and style, and weave the style anchors in naturally "
    "(never a bare keyword list). This is a small model, so keep the subject's head/face clearly visible "
    "and prominent (profile or facing the viewer) - never frame it tiny, turned away, or obscured, which "
    "renders as a blur or deformed anatomy. If the subject is a personified animal, convey it through pose, "
    "action and props, NEVER through human eyes/face/'human-like gaze' - this small model fuses a human face "
    "into the animal and renders a mutant; keep human-anatomy words off a non-human subject.\n\n"
)

SYSTEM_BODY = {
    "hold": (
        "YOUR MODE IS HOLD (faithful). The user asked for ONE specific scene and wants it shown "
        "consistently — NOT a story. The next shot must depict the SAME subject, setting, framing, and "
        "style as the core concept, with only natural micro-motion (the subject keeps doing what it is "
        "doing). HARD RULES: introduce NO new subject, object, place, or event; do NOT transform, "
        "transition, zoom, cut, or change the time of day; invent NO narrative. Lean hard on the CORE "
        "CONCEPT and ANCHORS — keep making exactly this scene, consistently.\n\n"
    ),
    "balanced": (
        "YOUR MODE IS BALANCED. Keep the SAME subject and setting as the core concept — invent no new "
        "elements and no story — but allow gentle natural variation shot to shot: a slightly different "
        "angle, light, or pose, small natural movement. Clearly the same scene; vary it, don't transform it.\n\n"
    ),
    "evolve": (
        "YOUR MODE IS EVOLVE. Think like a film director advancing an arc. You are given the overall "
        "vision and which shot this is (shot k of N): early shots ESTABLISH, middle shots DEVELOP and "
        "TRANSFORM, late shots RESOLVE — push the piece deliberately along that arc, don't tread water. "
        "Decide the next beat: what enters, moves, transforms, or transitions over the next ~2 seconds. "
        "Favor motion and evolution; don't repeat the previous shot.\n\n"
    ),
}

SYSTEM_FOOT = (
    "Respond in EXACTLY this format and nothing else:\n"
    "PLAN: <one sentence - what this next shot shows / what (if anything) changes>\n"
    "PROMPT: <the prompt for the next shot: a vivid description in the established medium/style, under 35 "
    "words, no 'next shot' preamble, no quotes>"
)


def system_text():
    base = SYSTEM_PRE + SYSTEM_BODY.get(args.steadiness, SYSTEM_BODY["hold"])
    if args.fit_check:
        base += (
            "FIT CHECK FIRST: look hard at the last frame. If it STILL clearly shows the intended scene, "
            "subject, and style (anchors) and looks coherent (no broken anatomy, no lost subject), the current "
            "prompt is working — reply with EXACTLY one word on its own line: KEEP. ONLY if the shot has "
            "visibly DRIFTED (wrong or missing subject, the scene fell apart, broken anatomy, style lost) "
            "should you instead give a corrected shot in the format below.\n\n")
    return base + SYSTEM_FOOT


def arc_phase(seg, total):
    nxt = seg + 1
    frac = nxt / max(1, total)
    if total <= 1 or frac <= 0.34:
        where = "early - establish the world and mood"
    elif frac <= 0.7:
        where = "middle - develop and transform; this is where the most change should happen"
    elif frac < 1.0:
        where = "late - begin resolving toward a final image"
    else:
        where = "final shot - land the closing image"
    return f"shot {nxt} of {total} (~{int(frac * 100)}% through, {where})"


def user_text():
    s = args.steadiness
    parts = []
    if s == "evolve":
        if args.directive:
            parts.append(f"OVERALL VISION (the arc to move along): {args.directive}")
        if args.orig_prompt:
            parts.append(f"CORE MEDIUM & STYLE (never change the medium): {args.orig_prompt}")
        if args.anchors:
            parts.append(f"STYLE ANCHORS (honor, weave in naturally): {args.anchors}")
        parts.append(f"POSITION: {arc_phase(args.seg, args.total)}")
        if args.history:
            parts.append("STORY SO FAR (beats already shown - do not repeat, build on them): "
                         + " || ".join(h.strip() for h in args.history.split("||") if h.strip()))
        if args.prev:
            parts.append(f"PREVIOUS SHOT'S PROMPT: {args.prev}")
        parts.append("The image is the last frame of the shot just rendered. Direct the NEXT shot: "
                     "decide the next beat that advances the arc, then write its prompt.")
    else:  # hold / balanced — faithful, no story
        if args.orig_prompt:
            parts.append(f"THE ONE SCENE TO {'HOLD' if s == 'hold' else 'KEEP'}: {args.orig_prompt}")
        if args.anchors:
            parts.append(f"STYLE ANCHORS (must persist every shot): {args.anchors}")
        if args.prev:
            parts.append(f"PREVIOUS SHOT'S PROMPT (stay consistent with it): {args.prev}")
        if s == "hold":
            parts.append("The image is the last frame just rendered. Write the prompt for the next shot to "
                         "keep showing EXACTLY THIS SAME SCENE — same subject, setting, framing, style — with "
                         "only natural micro-motion. Add nothing, change nothing, tell no story.")
        else:
            parts.append("The image is the last frame just rendered. Write the prompt for the next shot: the "
                         "SAME scene and subject with gentle natural variation (angle/light/pose/small motion) "
                         "— invent no new elements and no story.")
    return "\n".join(parts)


def load(cpu=False):
    if cpu:                                              # resident daemon: fp16 on CPU (nf4 needs CUDA)
        proc = AutoProcessor.from_pretrained(MODEL, max_pixels=256 * 256)
        m = Qwen3VLForConditionalGeneration.from_pretrained(
            MODEL, torch_dtype=torch.bfloat16, device_map="cpu")
        log("director: fp16 on CPU (resident daemon — never touches the GPU)")
        return m, proc
    if args.quant == "4bit" and os.path.isdir(QDIR):     # fast path: pre-quantized 4-bit from disk
        try:
            proc = AutoProcessor.from_pretrained(QDIR, max_pixels=256 * 256)
            m = Qwen3VLForConditionalGeneration.from_pretrained(
                QDIR, device_map="cuda:0", torch_dtype=torch.bfloat16)
            log("director: loaded pre-quantized nf4 from %s" % QDIR)
            return m, proc
        except Exception as e:
            log("director: pre-quant load failed (%s); live-quantizing" % str(e)[:160])
    proc = AutoProcessor.from_pretrained(MODEL, max_pixels=256 * 256)
    if args.quant == "4bit":
        try:
            from transformers import BitsAndBytesConfig
            bnb = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
            m = Qwen3VLForConditionalGeneration.from_pretrained(
                MODEL, quantization_config=bnb, device_map="cuda:0", torch_dtype=torch.bfloat16)
            log("director: loaded 4-bit (nf4) on cuda")
            return m, proc
        except Exception as e:
            log("director: 4-bit load failed (%s); falling back to bf16+offload" % str(e)[:200])
    m = Qwen3VLForConditionalGeneration.from_pretrained(
        MODEL, torch_dtype=torch.bfloat16, device_map="auto",
        max_memory={0: "6GiB", "cpu": "44GiB"})
    log("director: loaded bf16 + CPU offload")
    return m, proc


def extract_prompt(resp):
    """Return (prompt, plan_text) from the PLAN/PROMPT response."""
    plan = re.search(r"PLAN:\s*(.+?)(?:\n|PROMPT:|$)", resp, flags=re.I | re.S)
    plan_txt = " ".join(plan.group(1).split()) if plan else ""
    if plan_txt:
        log("director PLAN> " + plan_txt[:200])
    m = re.search(r"PROMPT:\s*(.+)", resp, flags=re.I | re.S)
    s = m.group(1) if m else resp
    s = s.strip().splitlines()[0] if s.strip() else s
    s = re.sub(r"^\s*(next shot|in the next (frame|shot)|shot\s*\d+|prompt|plan)\s*[:\-,.]?\s*",
               "", s, flags=re.I).strip().strip('"').strip("'").strip()
    out = " ".join(s.split())
    if not plan_txt:                       # model skipped the "PLAN:" line -> salvage the reasoning head
        head = " ".join(re.split(r"PROMPT:", resp, flags=re.I)[0].split())
        plan_txt = head[:300] if head else "(no explicit plan)"
    return out, plan_txt


def run_once(model, proc, dev):
    """One director inference from the current global args -> result dict (no printing). Device-aware
    (cuda for the one-shot GPU path, cpu for the resident daemon)."""
    import time
    messages = [
        {"role": "system", "content": system_text()},
        {"role": "user", "content": [
            {"type": "image", "image": args.image},
            {"type": "text", "text": user_text()},
        ]},
    ]
    text = proc.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = proc(text=[text], images=image_inputs, videos=video_inputs,
                  padding=True, return_tensors="pt").to(dev)
    torch.manual_seed(args.seg)                              # vary per shot, reproducible per input
    _t1 = time.perf_counter()
    with torch.no_grad():
        gen = model.generate(**inputs, max_new_tokens=args.max_new_tokens,
                             do_sample=True, temperature=0.7, top_p=0.9)
    if str(dev).startswith("cuda"):
        torch.cuda.synchronize()                            # generate() is async; sync before timing infer
    infer_ms = int((time.perf_counter() - _t1) * 1000)
    resp = proc.batch_decode(gen[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
    out, plan_txt = extract_prompt(resp)
    if args.fit_check and re.match(r"\s*KEEP\b", resp, re.I):   # director judged the frame still fits
        out = (args.prev or out).strip()                       # -> reuse the previous prompt verbatim
        plan_txt = "frame still fits the scene — keeping the current prompt (no change)"
    if args.anchors:                                        # soft safety: only if style is wholly lost
        anchs = [a.strip() for a in args.anchors.split(",") if a.strip()]
        if anchs and not any(a.lower() in out.lower() for a in anchs):
            out = out.rstrip(". ") + ", " + ", ".join(anchs[:2])
    return {"seg": args.seg, "raw": resp, "plan": plan_txt, "prompt": out,
            "infer_ms": infer_ms, "system": system_text(), "user": user_text()}


def main():
    import time, json
    _t0 = time.perf_counter()
    model, proc = load()
    load_ms = int((time.perf_counter() - _t0) * 1000)
    r = run_once(model, proc, next(model.parameters()).device)
    r["load_ms"] = load_ms
    print("[[DIRECT_MS load=%d infer=%d]]" % (load_ms, r["infer_ms"]), flush=True)
    print("[[RAW]] " + json.dumps(r, ensure_ascii=True), flush=True)
    if r["plan"]:
        print("[[PLAN " + _ascii1(r["plan"], 400) + "]]", flush=True)   # marker first (studio captures it)
    print(r["prompt"])                                      # the prompt stays the LAST stdout line


def daemon():
    """Resident mode: load once on CPU, then serve per-seam JSON requests on stdin. Each request
    carries the per-seam fields (image/prev/seg/history); the constant args come from argv. Never
    touches the GPU, so the main process's video pipe is never evicted (no cold re-warm each shot)."""
    import time, json
    _t0 = time.perf_counter()
    model, proc = load(cpu=True)
    dev = next(model.parameters()).device
    print(json.dumps({"ready": True, "load_ms": int((time.perf_counter() - _t0) * 1000)}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            print(json.dumps({"error": "bad request"}), flush=True)
            continue
        if req.get("quit"):
            break
        for k in ("image", "prev", "history", "orig_prompt", "directive", "anchors", "steadiness"):
            if k in req:
                setattr(args, k, req[k])
        args.seg = int(req.get("seg", args.seg))
        args.total = int(req.get("total", args.total))
        try:
            r = run_once(model, proc, dev)
            r["load_ms"] = 0
            print(json.dumps(r, ensure_ascii=True), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)[:300]}), flush=True)


if __name__ == "__main__":
    daemon() if args.daemon else main()
