"""Single source of truth for the NEW RUN dial guidance.

Used BOTH by the studio's focus tooltips (studio.py: `from dials_help import DIALS as HELP`) AND by the
creative-director's system prompt (vlm_planner.py: `dial_guide()`), so the director understands the EXACT
same dials the user sees in the UI. Pure stdlib -> imports cleanly in either venv.
"""
import re

DIALS = {
    "mode": "[b]MODE[/b]\n[b]single[/b] — one clip from your PROMPT. If LENGTH needs more than one shot it\nauto-chains (same prompt, glued at the tails) — no LLM involved.\n[b]director[/b] — a vision model watches each shot's last frame and rewrites the\nprompt for the next, steering toward your DIRECTIVE. Use it when the video\nshould progress or tell a story.",
    "backend": "[b]BACKEND[/b]  (the video model)\n[b]LTX-2B[/b] — fast (~1s/step), lighter; best for quick drafts + iteration.\n[b]Wan-VACE-1.3B[/b] — slower (~7s/step + a slow decode) but clearly higher\nfidelity and better motion; renders at 480p / 16fps natively.\nGUIDANCE auto-retunes when you switch (LTX 3.0 / Wan 5.0).",
    "prompt": "[b]PROMPT[/b]  (the opening shot)\nBe concrete: subject + action + setting + style. Name the medium so it sticks\n('35mm film', 'oil painting', 'photoreal'). For an animal or person, keep the\nhead/face visible (profile or facing you) — turned-away or tiny faces blur.\nE.g. 'a red fox trotting through fresh snow at dawn, photoreal, side profile'.",
    "directive": "[b]DIRECTIVE[/b]  (director mode)\nThe arc the vision model steers toward, shot by shot. PROMPT is the start;\nDIRECTIVE is the destination.\nE.g. PROMPT 'a calm sea at dawn' + DIRECTIVE 'a storm builds, then clears to a\nrainbow'. Blank = just hold the opening scene.",
    "anchors": "[b]ANCHORS[/b]  (the style leash)\nSubject + style to keep in EVERY shot — fights drift over a long clip. Applies\nto any chained run; in director mode the VLM folds them into each rewrite too.\nPut the medium here so it persists across shots.\nE.g. 'brown horse, golden-hour, 35mm film' or 'watercolor, muted palette'.",
    "cond_strength": "[b]CONTINUITY[/b]  (0-1, chained runs)\nHow tightly each new shot holds the previous shot's last frames.\n[b]1.0[/b] — tightest; the scene/subject carries over (best for HOLD).\n[b]~0.6-0.8[/b] — looser; lets motion + scene evolve more between shots.\nRaise it if the subject drifts/morphs; lower it if a clip feels 'stuck'.",
    "steadiness": "[b]STEADINESS[/b]  (director mode)\nHow far the director may stray:\n[b]Hold[/b] — re-assert the exact same scene; checks every ~3rd shot, rewrites only on drift.\n[b]Balanced[/b] — like Hold, but when it does step in it allows gentle variation\n(angle/light/pose) instead of strict re-assertion.\n[b]Evolve[/b] — journey/transform toward the DIRECTIVE, re-planned EVERY shot.\nEvolve REQUIRES a DIRECTIVE distinct from the prompt (else the engine runs Hold).",
    "image": "[b]START IMAGE[/b]  (optional)\nA still to animate — shot 1 begins from this exact frame (image-to-video).\nPath relative to the studio, e.g. input/start.png. Must exist before you run;\nmatch it to your RESOLUTION's aspect ratio for best results.\nLeave blank to generate the opening from the PROMPT instead.",
    "res": "[b]RESOLUTION[/b]\nFrame size — the HARD limit on 8GB (length is cheap, resolution is not).\n512 = fast drafts, 704 = balanced, 768 = sharpest.\nBigger = sharper but a SHORTER max clip/shot (more seams + time);\npeak VRAM is capped, so it stays ~flat.\nWan renders ~480p natively and upscales low settings automatically, so 512-704\nis plenty for it.",
    "seconds": "[b]LENGTH s[/b]  (total length)\nTotal video length. Length is nearly free — it just adds more chained shots\n(and time). Exceed one SEGMENT and it auto-chains.\nE.g. 12s at 2.4s per segment is about 5-6 shots stitched together.",
    "seg": "[b]SEGMENT s[/b]  (shot length)\nSeconds the model makes in ONE pass — the unit a long video is chained from.\n[b]Shorter[/b] (1.5-2s): less VRAM, more seams (more drift risk).\n[b]Longer[/b] (2.5-3s): fewer seams, heavier per shot.\n~2-2.5s is the sweet spot. VRAM may cap it lower automatically.",
    "steps": "[b]STEPS[/b]  (quality vs time)\nDenoising iterations per shot. ~20 = fast draft, ~30 = good, ~40-50 = best\n(diminishing returns). Render time scales with steps.\nWan looks good at ~20-30; LTX likes ~30-40.",
    "cfg": "[b]GUIDANCE[/b]  (CFG — prompt adherence)\nHow hard the model sticks to your prompt. LTX ~3.0, Wan ~5.0 (auto-set when you\nswitch BACKEND; override freely).\nToo high = over-cooked / over-saturated; too low = loose, ignores the prompt.\nNudge down ~1 if colors look fried.",
    "seed": "[b]SEED[/b]\nThe random starting point. [b]Same seed + same settings = identical output.[/b]\nFix it to change ONE thing at a time (tweak the prompt, keep the seed) and\ncompare fairly. Change the number to roll a different variation. 0 is just a\nfixed seed like any other.",
    "fps": "[b]FPS[/b]  (playback framerate)\n24 = cinematic, 30 = smoother. Drives the LTX backend.\nNote: Wan always renders at its native 16fps (the right motion speed for that\nmodel), regardless of this field.",
    "n_prompt": "[b]NEG[/b]  (negative prompt)\nWhat to steer AWAY from. The default is a tuned quality guard (blurry, deformed,\nextra limbs, headless, etc.) — usually just leave it.\nAdd terms to fix a recurring problem, e.g. 'text, watermark, busy background'.",
    "name": "[b]NAME[/b]  (output file)\nSaves to outputs/NAME.mp4 (+ a frames folder). Spaces become dashes. Blank =\nauto 'job_HHMMSS'. If the name exists it auto-adds a suffix, so you never\noverwrite an earlier run.",
    "vram_reserve": "[b]RESERVE (GB)[/b]\nGB of the 8GB card reserved for desktop responsiveness while a run is active.\nHigher = smoother UI, lower = more VRAM headroom for Wan. ~1.0 is the sweet\nspot. On an idle desktop you can safely dial it down to 0.5 during heavy renders.",
    "sound_enabled": "[b]SOUND[/b]  (event alerts, master switch)\nFires on: a NEW row landing in the ARCHIVE done (run_done), and a STALL — the\nactive run making no observable progress for a while (run_stall).\nA stalled run is AUTO-SUSPENDED (graceful, then a ckpt-preserving kill after a\ngrace window) so the queue keeps moving; resumable from QUEUE/ARCHIVE. Tune via\nstudio_config.json sounds.{stall_secs:240, stall_action:suspend|alert, stall_grace_secs:180}.\nWAVs: sfx/run_done.wav / sfx/run_stall.wav. Banner + auto-suspend work even with sound off.",
    "cfg_rescale": "[b]GUIDANCE RESCALE[/b]  (advanced; opt-in, default off)\nTames over-cooked / over-saturated output when GUIDANCE > ~3. Rescales the\nguidance noise back toward the conditional prediction; higher = stronger\ncorrection. LTX-2B + Wan only — Wan-turbo ignores it. No-op at GUIDANCE ≤ 1.",
    "cfg_interval": "[b]GUIDANCE SCHEDULE[/b]  (advanced; opt-in, default off)\nApplies guidance only over part of the denoise instead of every step — faster,\nfewer artifacts. 'on' uses 0.0:0.5 (first half of denoising). Off-steps skip the\nuncond forward pass. LTX-2B + Wan only; Wan-turbo + GUIDANCE ≤ 1 ignore it.",
    "wan_ref_anchor": "[b]IDENTITY ANCHOR[/b]  (advanced; opt-in, default off)\nPins every shot in a chain to shot 1's opening frame as an anchor reference,\nstabilizing subject identity across a long video and fighting style/identity\ndrift. Wan / Wan-turbo only, chained (multi-shot) runs.",
    "blind_ab": "[b]⇄ BLIND A/B[/b]  (fair variable-isolation test)\nQueues TWO runs from the CURRENT form, identical except ONE variable you pick\n(A-value vs B-value), on ONE shared seed. Enqueue order + the A/B labels are\nrandomized, so the ARCHIVE hides which run is which (kind, variant, and the\nvaried value all read 'blind') until you press ↯ REVEAL. Rate them fairly with\n≷ RATE PAIR first, THEN reveal to see which setting won.",
}

_TAG = re.compile(r"\[/?[a-z][^\]]*\]")


def plain(text):
    """Strip Rich [tag] markup -> plain text (for feeding the language model)."""
    return _TAG.sub("", text or "")


def dial_guide():
    """Compact plain-text guide to every dial (markup stripped) for the director's system prompt. The key
    on the left is the EXACT config field name the director should emit."""
    lines = []
    for key, text in DIALS.items():
        g = re.sub(r"\s{2,}", " ", plain(text).replace("\n", " ")).strip()
        lines.append("- %s -> %s" % (key, g))
    return "\n".join(lines)
