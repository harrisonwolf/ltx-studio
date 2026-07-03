#!/usr/bin/env python
"""LTX-Video Studio - a Pip-Boy themed TUI (Fallout aesthetic) for local video generation.
Launch:  ./ltx-studio.sh   (or:  ./venv/bin/python ltx_studio.py)
Chains LTX generation -> (optional) AnimateDiff enhance suite (RIFE / upscale / face).
"""
import os, sys, glob, time, subprocess

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, RichLog, Select, Static, Switch

REPO = os.path.dirname(os.path.abspath(__file__))
FP_PY = sys.executable                                   # this venv (LTX)
AD_REPO = "/home/wolve/video_gen/AnimateDiff"
AD_PY = os.path.join(AD_REPO, "venv/bin/python")
NEG = "worst quality, blurry, distorted, jittery, low detail, watermark"

RES = {"512 x 320  (fast)": (512, 320), "704 x 480  (balanced)": (704, 480), "768 x 512  (sharp)": (768, 512)}

INFO_DEFAULT = ("[b]PIP-OS v7.1.0.8 :: READY[/b]\n\nCycle fields with TAB. Each entry shows its\ndossier here.\n\n"
                "Set MODE, enter a PROMPT, then EXECUTE\n(CTRL+R). Image-to-video animates a still.\n\n"
                "[dim]Vault-Tec: preparing the future, today.[/dim]")

HELP = {
    "mode": "[b]MODE[/b]\nTEXT-TO-VIDEO: generate from the prompt alone.\nIMAGE-TO-VIDEO: animate a starting still frame\n(set IMAGE below).",
    "prompt": "[b]PROMPT[/b]\nDescribe the scene + motion + style.\n[dim]'a lone wanderer walks a desert highway,\ndust, heat haze, cinematic, 35mm'[/dim]",
    "nprompt": "[b]NEGATIVE PROMPT[/b]\nQualities to suppress. The default kills common\nartifacts; add terms you keep seeing.",
    "image": "[b]START IMAGE[/b]\nPath to a still for IMAGE-TO-VIDEO\n(e.g. input/vault.png). Optional in DIRECTOR.",
    "directive": "[b]DIRECTIVE (director mode)[/b]\nThe overall vision / story arc. The VLM reads\neach seam frame and writes the next shot toward\nthis goal. LENGTH becomes the total; SEG is\nper-shot.",
    "anchors": "[b]ANCHORS (director mode)[/b]\nSubject + style words kept in EVERY shot for\nconsistency (e.g. 'red fox, snow, cinematic').\nThe anti-drift leash.",
    "seg": "[b]SEGMENT LENGTH (director)[/b]\nSeconds per shot before re-directing. 2-3s is\nthe sweet spot. Shorter = more director control\n+ more seams.",
    "res": "[b]RESOLUTION[/b]\n512x320 = fastest. 704x480 = balanced.\n768x512 = sharpest (more VRAM + time).\n[dim]auto-rounded to /32.[/dim]",
    "seconds": "[b]LENGTH (SEC)[/b]\nClip duration. Frames = seconds x FPS,\nauto-rounded to 8k+1. Longer = more time.",
    "fps": "[b]FRAMERATE[/b]\nPlayback FPS of the output. 24 is cinematic.",
    "steps": "[b]STEPS[/b]\nDenoising iterations. ~30 good, ~50 best.\nLinear time cost. ~1.2s/step at 512x320.",
    "cfg": "[b]GUIDANCE[/b]\nHow strongly it obeys the prompt. LTX likes\n~3.0. Higher = more literal, can over-cook.",
    "seed": "[b]SEED[/b]\nRandom seed. Same seed + settings = identical\noutput. Change for a new take.",
    "interp": "[b]INTERPOLATE (RIFE)[/b]\nPost-process: invent in-between frames for\nsmoother motion. 2x / 4x. Uses your AnimateDiff\nenhance suite.",
    "upscale": "[b]UPSCALE x4[/b]\nReal-ESRGAN super-resolution after generation.\nBig detail gain; adds time.",
    "upscaler": "[b]UPSCALER[/b]\nREALESRGAN = smoother. ULTRASHARP = crisper.\nUsed only when UPSCALE is on.",
    "face": "[b]FACE RESTORE[/b]\nGFPGAN face enhancement after upscaling.\nGreat for characters; no-op without faces.",
}


def fmt(s):
    s = int(round(s))
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def field(label, widget):
    return Horizontal(Label(label, classes="lbl"), widget, classes="row")


class LTXStudio(App):
    CSS = """
    Screen { background: #06120b; color: #34d977; }
    #topbar { height: 3; background: #0a1c10; color: #6dffab; border: heavy #1c7a42;
              content-align: center middle; text-style: bold; }
    #tabs { height: 1; color: #1f9a52; content-align: center middle; }
    #body { height: 1fr; }
    #form { width: 52; border: heavy #1c7a42; background: #08160d; padding: 0 1; }
    #right { width: 1fr; padding: 0 0 0 1; }
    .row { height: 3; }
    .lbl { width: 14; color: #1f9a52; content-align: left middle; }
    .sec { color: #6dffab; text-style: bold; border-bottom: dashed #1c7a42; margin: 1 0 0 0; }
    Input { border: tall #134a2a; background: #06120b; color: #7dffb8; }
    Input:focus { border: tall #6dffab; }
    Select { background: #06120b; color: #7dffb8; }
    Switch { background: #0a1c10; }
    #estimate { color: #ffcf5c; text-style: bold; padding: 1 0 0 0; }
    #presets { height: 3; margin-top: 1; }
    #presets Button { width: 1fr; margin: 0 1 0 0; border: tall #1c7a42;
                      background: #0a1c10; color: #7dffb8; }
    #go { width: 100%; margin-top: 1; border: heavy #2fae5f;
          background: #134a2a; color: #9dffce; text-style: bold; }
    #info { height: 15; border: heavy #1c7a42; background: #08160d; color: #34d977; padding: 0 1; }
    RichLog { border: heavy #1c7a42; background: #050d08; color: #34d977; }
    #status { height: 1; background: #0a1c10; color: #6dffab; }
    """
    BINDINGS = [("ctrl+c", "quit", "Power off"), ("ctrl+r", "run", "Execute")]

    def compose(self) -> ComposeResult:
        yield Static("VAULT-TEC  ☢  PIP-BOY 3000 MK IV     ::     L T X · V I D E O   T E R M I N A L", id="topbar")
        yield Static("  STAT  ◄  SPECIAL   INV   ▓ V.A.T.S ▓   DATA   MAP   RADIO  ►", id="tabs")
        with Horizontal(id="body"):
            with VerticalScroll(id="form"):
                yield Static("▌ GENERATION", classes="sec")
                yield field("MODE", Select([("text-to-video", "t2v"), ("image-to-video", "i2v"), ("DIRECTOR: long video", "director")], value="t2v", id="mode", allow_blank=False))
                yield field("PROMPT", Input(placeholder="opening shot: a lone wanderer on a desert road", id="prompt"))
                yield field("NEG", Input(value=NEG, id="nprompt"))
                yield field("IMAGE", Input(placeholder="input/start.png  (i2v / director start)", id="image"))
                yield field("DIRECTIVE", Input(placeholder="DIRECTOR: overall vision / story arc", id="directive"))
                yield field("ANCHORS", Input(placeholder="DIRECTOR: subject + style to keep", id="anchors"))
                yield field("SEG s", Input(value="3", id="seg"))
                yield field("RES", Select([(k, k) for k in RES], value="704 x 480  (balanced)", id="res", allow_blank=False))
                yield field("LENGTH s", Input(value="4", id="seconds"))
                yield field("FPS", Input(value="24", id="fps"))
                yield field("STEPS", Input(value="40", id="steps"))
                yield field("GUIDANCE", Input(value="3.0", id="cfg"))
                yield field("SEED", Input(value="0", id="seed"))
                yield Static("▌ ENHANCEMENT", classes="sec")
                yield field("INTERP", Select([("off", "1"), ("2x", "2"), ("4x", "4")], value="1", id="interp", allow_blank=False))
                yield field("UPSCALE x4", Switch(value=False, id="upscale"))
                yield field("UPSCALER", Select([("realesrgan", "realesrgan"), ("ultrasharp", "ultrasharp")], value="ultrasharp", id="upscaler", allow_blank=False))
                yield field("FACE FIX", Switch(value=False, id="face"))
                yield Static("▌ EXECUTE", classes="sec")
                yield Static("EST: --", id="estimate")
                with Horizontal(id="presets"):
                    yield Button("DRAFT", id="p_draft")
                    yield Button("QUALITY", id="p_quality")
                yield Button("▶  E X E C U T E   (ctrl+r)", id="go")
            with Vertical(id="right"):
                yield Static(INFO_DEFAULT, id="info")
                yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield Static("  HP ████████ 295/295    AP ██████ 90    RADS ☢ 0     │    TAB cycle  ·  CTRL+R execute  ·  CTRL+C power off", id="status")

    def v(self, wid):
        return self.query_one(f"#{wid}").value

    def on_mount(self) -> None:
        self.query_one("#info", Static).border_title = "◖ PIP-OS :: DOSSIER ◗"
        self.query_one("#log", RichLog).border_title = "◖ TERMINAL OUTPUT ◗"
        self.update_estimate()

    def on_descendant_focus(self) -> None:
        wid = getattr(self.focused, "id", None)
        self.query_one("#info", Static).update(HELP.get(wid, INFO_DEFAULT))

    def on_select_changed(self, e: Select.Changed) -> None:
        self.update_estimate()

    def on_input_changed(self, e: Input.Changed) -> None:
        self.update_estimate()

    def on_switch_changed(self, e: Switch.Changed) -> None:
        self.update_estimate()

    def update_estimate(self) -> None:
        try:
            est = self.query_one("#estimate", Static)
        except Exception:
            return
        try:
            W, H = RES[self.v("res")]
            seconds = float(self.v("seconds")); fps = int(self.v("fps"))
            steps = int(self.v("steps")); interp = int(self.v("interp"))
            mode = self.v("mode"); upscale = self.query_one("#upscale", Switch).value
            frames = (int(seconds * fps) // 8) * 8 + 1
            px = (W * H) / (512 * 320)
            gen = 150 + steps * 1.3 * px * (1.3 if mode == "i2v" else 1.0)
            enh = (frames * interp * 1.2 * px if upscale else 0.0) + (frames * (interp - 1) * 0.6 if interp > 1 else 0.0)
            # VRAM guard: LTX holds the whole clip at once, so frames*pixels is the limiter on 8GB
            budget = frames * W * H / 1e6
            warn = "   [b]!! TOO LONG -> WILL OOM, cut length/res[/b]" if budget > 45 else ("   !! may OOM on 8GB" if budget > 28 else "")
            est.update(f"EST: ~{fmt(gen + enh)}   ({frames} frames @ {W}x{H}){warn}")
        except Exception:
            est.update("EST: --")

    PRESETS = {
        "p_draft": {"res": "512 x 320  (fast)", "steps": "25", "interp": "1", "upscale": False, "face": False},
        "p_quality": {"res": "704 x 480  (balanced)", "steps": "50", "interp": "2", "upscale": True, "face": False},
    }

    def action_run(self):
        self._launch()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid in self.PRESETS:
            for k, val in self.PRESETS[bid].items():
                self.query_one(f"#{k}").value = val
            self.update_estimate()
            self.query_one(RichLog).write(f"[#6dffab]> preset loaded: {bid[2:]}[/#6dffab]")
        elif bid == "go":
            self._launch()

    def _launch(self):
        log = self.query_one(RichLog)
        prompt = (self.v("prompt") or "").strip()
        if not prompt:
            log.write("[#ffcf5c]> ERROR: enter a PROMPT first.[/#ffcf5c]"); return
        mode = self.v("mode"); image = (self.v("image") or "").strip()
        if mode == "i2v" and not image:
            log.write("[#ffcf5c]> ERROR: image-to-video needs an IMAGE path.[/#ffcf5c]"); return
        try:
            W, H = RES[self.v("res")]
            seconds = self.v("seconds"); fps = int(self.v("fps")); steps = self.v("steps")
            cfg = self.v("cfg"); seed = self.v("seed"); interp = int(self.v("interp"))
        except (ValueError, KeyError):
            log.write("[#ffcf5c]> ERROR: numeric fields must be numbers.[/#ffcf5c]"); return
        self.query_one("#go", Button).disabled = True
        self.run_pipeline(dict(prompt=prompt, nprompt=self.v("nprompt"), mode=mode, image=image,
                               directive=(self.v("directive") or "").strip(), anchors=(self.v("anchors") or "").strip(),
                               seg=self.v("seg"), W=W, H=H, seconds=seconds, fps=fps, steps=steps, cfg=cfg, seed=seed),
                          interp, self.query_one("#upscale", Switch).value, self.v("upscaler"),
                          self.query_one("#face", Switch).value)

    @work(thread=True, exclusive=True)
    def run_pipeline(self, p, interp, upscale, upscaler, face):
        log = self.query_one(RichLog)
        env = dict(os.environ, PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
        from rich.markup import escape
        job = time.strftime("%H%M%S")
        out = f"outputs/ltx_{job}.mp4"
        fdir = f"outputs/ltx_{job}_frames"

        def run(cmd, cwd):
            self.call_from_thread(log.write, f"[#1f9a52]$ {escape(' '.join(str(c) for c in cmd))}[/#1f9a52]")
            pr = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in pr.stdout:
                line = line.rstrip()
                if line and "vision_model" not in line:
                    self.call_from_thread(log.write, escape(line))
            return pr.wait()

        self.call_from_thread(log.write, f"[#9dffce]>>> GENERATING {p['mode']} {p['W']}x{p['H']} ...[/#9dffce]")
        if p["mode"] == "director":
            cmd = [FP_PY, "director.py", "--prompt", p["prompt"], "--n_prompt", p["nprompt"],
                   "--directive", (p["directive"] or p["prompt"]), "--anchors", p["anchors"], "--vlm",
                   "--total", p["seconds"], "--seg", p["seg"], "--fps", str(p["fps"]),
                   "--cfg", p["cfg"], "--seed", p["seed"], "--steps", p["steps"],
                   "--width", str(p["W"]), "--height", str(p["H"]), "--out", out]
            if p["image"]:
                cmd += ["--image", p["image"]]
        else:
            cmd = [FP_PY, "run_ltx.py", "--prompt", p["prompt"], "--n_prompt", p["nprompt"],
                   "--seconds", p["seconds"], "--fps", str(p["fps"]), "--steps", p["steps"],
                   "--cfg", p["cfg"], "--seed", p["seed"], "--width", str(p["W"]), "--height", str(p["H"]), "--out", out]
            if p["mode"] == "i2v":
                cmd += ["--image", p["image"]]
        if interp > 1 or upscale or face:
            cmd += ["--frames_dir", fdir]
        if run(cmd, REPO) != 0:
            self.call_from_thread(log.write, "[#ffcf5c]>>> generation FAILED (see above).[/#ffcf5c]")
            self.call_from_thread(self._done); return
        self.call_from_thread(log.write, f"[#9dffce]>>> SAVED {out}[/#9dffce]")

        if (interp > 1 or upscale or face) and os.path.isdir(os.path.join(REPO, fdir)):
            final = f"outputs/ltx_{job}_final.mp4"
            enh = [AD_PY, "-m", "scripts.enhance", "--frames", os.path.join(REPO, fdir),
                   "--interp", str(interp), "--out", os.path.join(REPO, final), "--fps", str(p["fps"] * interp),
                   "--upscaler", upscaler]
            if upscale:
                enh.append("--upscale")
            if face:
                enh.append("--face")
            self.call_from_thread(log.write, "[#9dffce]>>> ENHANCING (RIFE / upscale / face) ...[/#9dffce]")
            if run(enh, AD_REPO) == 0:
                self.call_from_thread(log.write, f"[b #9dffce]>>> DONE -> {final}[/b #9dffce]")
        else:
            self.call_from_thread(log.write, "[b #9dffce]>>> DONE[/b #9dffce]")
        self.call_from_thread(self._done)

    def _done(self):
        self.query_one("#go", Button).disabled = False


if __name__ == "__main__":
    LTXStudio().run()
