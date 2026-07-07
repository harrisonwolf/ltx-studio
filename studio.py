#!/usr/bin/env python
"""LTX STUDIO - Pip-Boy themed job-runner dashboard.
Tabs: NEW RUN (configure+queue) / QUEUE / LIVE (watch+pause/cancel) / ARCHIVE (inspect+enhance).
Backend: studio_core.JobManager (persistent runs, queue, pause/resume/cancel).
Launch: ./studio.sh   (or ./venv/bin/python studio.py)
"""
import os, sys, time, json, subprocess, threading, glob, re, shutil, random
import gpu_budget
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from rich.markup import escape
from textual.binding import Binding
from textual.widgets import (Button, Footer, Input, Label, RichLog, Select, Static, Switch, TextArea,
                             TabbedContent, TabPane, DataTable, ProgressBar, OptionList)
from textual.widgets.option_list import Option
from textual.theme import Theme
from textual.screen import ModalScreen
from PIL import Image
from rich.text import Text
from rich.style import Style
from studio_core import JobManager, REPO, ARCHIVED

from studio_themes import EXTRA_THEMES, SPAL, tmark

def _run_kind(job):
    """T11: classify a job's PURPOSE from its params, distinct from job.kind (single/chained/
    director/enhance, which is really the BACKEND shape). Returns (glyph, label)."""
    p = job.params or {}
    if job.kind == "enhance":
        return "▲", "enhance"
    if p.get("pair_id"):
        if p.get("pair_blind") and not p.get("pair_revealed"):
            return "⇄", "blind pair"        # hide the A/B variant until REVEAL
        variant = p.get("pair_variant") or "?"
        return "⇄", f"pair {variant}"
    if p.get("replicate_set_id"):
        return "×N", "replicate"
    if p.get("source_id"):        # generic fallback: some derived kind we don't special-case above
        return "⟲", "derived"
    return {"single": ("▭", "single"), "chained": ("▥", "chained"),
            "director": ("✦", "director")}.get(job.kind, ("·", job.kind or "run"))


def _status_glyph(status):
    return {"done": "[#9dffce]✓[/#9dffce]", "failed": "[#ff6d6d]✕[/#ff6d6d]",
            "cancelled": "[dim]■[/dim]", "interrupted": "[#ffcf5c]‖[/#ffcf5c]",
            "suspended": "[#ffcf5c]▽[/#ffcf5c]"}.get(status, "·")


def _dial_title(wid):
    """Display name for a form field, parsed from its HELP entry's leading [b]TITLE[/b]
    (single source of truth with the INFO panel); falls back to the widget id."""
    try:
        m = re.match(r"\[b\](.+?)\[/b\]", HELP.get(wid, ""))
        return (m.group(1).split("(")[0].strip() if m else (wid or "").upper()) or (wid or "").upper()
    except Exception:
        return (wid or "").upper()


def _sfx_options():
    """(label, filename) for every WAV in sfx/ — drop a file in the folder and it appears in the
    DONE/STALL sound pickers on the next launch. Never empty (Select needs >=1 option)."""
    try:
        fs = sorted(f for f in os.listdir(os.path.join(REPO, "sfx")) if f.lower().endswith(".wav"))
    except Exception:
        fs = []
    return [(f[:-4], f) for f in fs] or [("run_done", "run_done.wav")]


def _demark(s):
    """Neutralize [brackets] in USER text (prompts/titles) before it lands in Rich MARKUP strings —
    a prompt containing tag-like sequences would otherwise raise at render and panic the app.
    Substitutes visually-equal-width parens, so card/panel width math stays exact."""
    return str(s).replace("[", "(").replace("]", ")")

FP_PY = sys.executable
AD_REPO = "/home/wolve/video_gen/AnimateDiff"
AD_PY = os.path.join(AD_REPO, "venv/bin/python")
NEG = ("worst quality, inconsistent motion, blurry, jittery, distorted, low detail, "
       "deformed, malformed anatomy, missing or extra limbs, mutated, fused body, headless")
RES = {"512 x 320  fast": (512, 320), "704 x 480  balanced": (704, 480), "768 x 512  sharp": (768, 512)}
LTX_REPO_DEFAULT = "Lightricks/LTX-Video-0.9.5"   # the checkpoint every ltx-backend run pins (Q1); recorded per run

DIRECTOR_VENV_PY = "/home/wolve/video_gen/director_venv/bin/python"
PLANNER_SCRIPT = os.path.join(REPO, "vlm_planner.py")

from studio_config import STUDIO_CONFIG_PATH, load_studio_config, save_studio_config

def res_key(v):
    """Map a loose res token from the consultant ('512'/'704'/'768'/'704 x 480') to a RES key.
    Matches the FIRST number to an option's width (avoids '512' matching 768x512's height)."""
    s, digits = str(v or ""), ""
    for ch in s:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    for k in RES:
        if k.split()[0] == digits:
            return k
    return "704 x 480  balanced"

INFO = ("[b]PIP-OS v8 :: JOB CONTROL[/b]\nConfigure a run and QUEUE it. Watch it on the LIVE\n"
        "tab (pause / resume / cancel). Finished runs persist\nin ARCHIVE - inspect or enhance them anytime.\n\n"
        "[dim]Vault-Tec: directing the future, frame by frame.[/dim]")
try:
    from dials_help import DIALS as HELP   # NEW RUN dial tooltips — shared source of truth with the director (vlm_planner.py)
    from dials_help import plain as _plain_help   # strip Rich [tags] for plain-text Button tooltips
except Exception:
    HELP = {}
    def _plain_help(t):
        return t or ""

try:
    import field_visuals   # BF6-style visual tooltips for NEW RUN fields — additive, degrades to None
except Exception:
    field_visuals = None

try:
    import style_presets   # T27: named ANCHOR-word style presets — additive, degrades to None
except Exception:
    style_presets = None
try:
    import sounds   # event sound-effect harness (run_done, …) — additive, degrades to None
except Exception:
    sounds = None
try:
    import readout   # T22: global READOUT meter strip — additive, degrades to None
except Exception:
    readout = None


def plain_help(key):
    """Plain-text (markup-stripped) HELP blurb for a Button.tooltip, safe if the key is missing."""
    return _plain_help(HELP.get(key, ""))


def field(label, w, help_key=None):
    cls = "row tarow" if isinstance(w, TextArea) else "row"
    kids = [Label(label, classes="lbl"), w]
    if help_key:
        kids.append(Button("ⓘ", id=f"i_{help_key}", classes="infobtn"))
    return Horizontal(*kids, classes=cls)


EHELP = {
    "interp": "INTERP — frame interpolation (RIFE). Generates in-between frames so motion is smoother and "
              "the framerate rises: 2x doubles frames/fps, 4x quadruples. It's AI motion synthesis, not "
              "blending. Caveat: on very fast or occluded motion the guessed frames can warp. Output fps = your fps × interp.",
    "upscale": "UPSCALE — AI super-resolution (Real-ESRGAN / UltraSharp). 4x = sharpest + largest, heaviest "
               "on RAM. 2x = runs the 4x model then downscales — clean and ~1/4 the RAM of 4x. Off = native size. "
               "Caveat: the heaviest pass; a long 4x run can OOM — watch the RAM estimate above.",
    "face": "FACE — face restoration. GFPGAN: strong, fast, can over-beautify. CodeFormer: often better "
            "identity + tunable (non-commercial licence). Off: leave faces as-is. WARNING: both are trained on "
            "HUMAN faces — on an animal they skip it or push it toward a human face (mutation risk). Turn OFF for animals.",
    "deflicker": "DEFLICKER — temporal stabilization. Matches each frame's brightness/colour to a moving "
                 "average of its neighbours, removing the exposure/colour flicker common in AI video, without "
                 "blurring motion. Cheap, no model. Caveat: fixes global flicker, not fine texture shimmer. Good to leave on for chained clips.",
    "upmodel": "UPSCALE MODEL — which super-res network does the work. Real-ESRGAN: fast, smooth (default). "
               "DAT-2: realistic, best on diffusion/compression texture. HAT-L: highest fidelity but heaviest "
               "(~7GB VRAM; its tiles shrink automatically to fit 8GB). SwinIR: lighter transformer, low-VRAM. "
               "The transformer models (DAT-2/HAT/SwinIR) are sharper but several× slower per frame than Real-ESRGAN.",
    "interpeng": "INTERP ENGINE — how the in-between frames are synthesized. RIFE: fast, great on clean/anime "
                 "motion (default). FILM: purpose-built for LARGE motion + occlusions (fewer warp artifacts) but "
                 "~5-10× slower than RIFE. Reach for FILM when RIFE smears fast or messy motion; RIFE otherwise.",
    "restore": "RESTORE — video restoration via an AI upsampling pass (SeedVR2). SeedVR2-3B: fast, "
               "general-purpose. SeedVR2-7B: slower, higher fidelity. None: skip restoration. Restore can "
               "sometimes fix compression artifacts but may soften fine detail — preview on a test clip first.",
    "tilefeather": "FEATHER TILES — blend the upscaler's tile seams so no faint grid lines show where tiles "
                   "met. Costs nothing visible; leave on when a 4x/HAT upscale shows a checkerboard. Off = raw "
                   "tile boundaries (marginally faster).",
    "interpskip": "SKIP CUTS — on a hard cut or a static hold, hold the frame instead of morphing across it. "
                  "Stops the interpolator from inventing a smeared blend between two unrelated shots. Off = "
                  "interpolate every gap uniformly.",
}


def fmt(s):
    s = int(s or 0)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def _vidlen(j):
    """Output VIDEO length (seconds) — distinct from generation time. Renders store their target
    'seconds'; enhance carries the source's length forward (interp preserves duration)."""
    p = getattr(j, "params", None) or {}
    try:
        s = float(p.get("seconds") or 0)
        return fmt(int(round(s))) if s > 0 else "—"
    except Exception:
        return "—"


def _director_raw(job, seg=None):
    """Lazy-read <ckpt_dir>/director.jsonl. {seg:int -> rec}, or one rec if seg given; {}/None if absent."""
    cd = getattr(job, "ckpt_dir", None)
    cands = []
    if cd:
        cands.append(os.path.join(cd if os.path.isabs(cd) else os.path.join(REPO, cd), "director.jsonl"))
    cands.append(os.path.join(REPO, "runs", f"{job.id}_director.jsonl"))   # preserved after ckpt cleanup
    path = next((p for p in cands if os.path.exists(p)), None)
    if not path:
        return {} if seg is None else None
    recs = {}
    try:
        with open(path) as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    r = json.loads(ln)
                    recs[int(r.get("seg", -1))] = r
    except Exception:
        return {} if seg is None else None
    return recs if seg is None else recs.get(int(seg))


def _dir_cost_line(job, seg):
    """'director: load Xs + think Ys · shot gen Zs' for the shot at dir_ms key seg (1-based). '' if no timing."""
    dm = getattr(job, "dir_ms", None) or {}
    pair = dm.get(seg, dm.get(str(seg)))      # JSON-reloaded keys may be str
    if not pair:
        return ""
    load_s, think_s = pair[0] / 1000.0, pair[1] / 1000.0
    ssecs = getattr(job, "seg_secs", []) or []   # 0-based per completed shot
    shot = f"  ·  shot gen {fmt(int(ssecs[seg - 1]))}" if 0 < seg <= len(ssecs) else ""
    return f"[dim]director:[/dim] load {load_s:.0f}s + think {think_s:.1f}s{shot}"


import itertools
_slug_counter = itertools.count(1000)   # process-monotonic tail for auto-name uniqueness (never collides)


def slugify(s, maxlen=40):
    """name -> filesystem-safe slug: lowercase, spaces->_, keep [a-z0-9-_], strip, cap length."""
    s = (s or "").strip().lower().replace(" ", "_")
    s = "".join(ch for ch in s if ch.isalnum() or ch in "-_")
    return s.strip("-_")[:maxlen].strip("-_")


import preview_art
from preview_art import render_preview

def _filesize(path):
    try:
        n = os.path.getsize(path if os.path.isabs(path) else os.path.join(REPO, path))
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
            n /= 1024
        return f"{n:.1f} TB"
    except Exception:
        return "—"


def _win_path(linux_path):
    """WSL path -> Windows path via wslpath -w (for launchers). None if unavailable."""
    p = os.path.abspath(linux_path)
    if not shutil.which("wslpath"):
        return None
    try:
        return subprocess.run(["wslpath", "-w", p], capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:
        return None


def open_in_player(linux_path):
    """Open a file in the Windows default app (explorer.exe -> wslview -> powershell Start-Process).
    Non-blocking fire-and-forget, never raises. Returns True if a launcher was spawned.
    explorer.exe returns exit 1 even on success, so we never check the return code."""
    p = os.path.abspath(linux_path)
    win = _win_path(p)
    attempts = []
    if shutil.which("explorer.exe") and win:
        attempts.append(["explorer.exe", win])
    if shutil.which("wslview"):
        attempts.append(["wslview", p])
    if shutil.which("powershell.exe") and win:
        attempts.append(["powershell.exe", "-NoProfile", "-Command", "Start-Process", "-FilePath", win])
    for args in attempts:
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True)
            return True
        except Exception:
            continue
    return False


class ConsultDaemon:
    """App-owned persistent CONSULT daemon (Qwen2.5-VL-7B). Warmed in the background while the
    GPU is idle and killed when a run needs the GPU, so opening CONSULT is instant. The daemon
    also exits on its own when the studio closes its stdin (EOF), so it never orphans."""

    def __init__(self):
        self.proc = None
        self.ready = False
        self.info = ""
        self.last_error = ""         # why the last load died (surfaced in the UI instead of 'waking…' forever)
        self.cpu_mode = False        # True when warmed onto CPU (a render holds the GPU) -> tick() must NOT kill it
        self._lock = threading.Lock()
        self._last_warm = 0.0

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def warm(self, cpu=False):
        """Spawn + load in the background (idempotent, rate-limited so a failed load can't thrash).
        cpu=True forces the director onto CPU (no GPU) so it can run while a render holds the GPU."""
        with self._lock:
            if self.alive() or (time.time() - self._last_warm) < 15:
                return
            self._last_warm = time.time()
            self.ready = False
            self.info = ""
            self.last_error = ""
            self.cpu_mode = cpu
            try:
                try:                  # capture the daemon's load/error log so we're never blind again
                    errf = open(os.path.join(REPO, "consult_daemon.err"), "w")
                except Exception:
                    errf = subprocess.DEVNULL
                env = dict(os.environ, PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
                if cpu:
                    env["CONSULT_DEVICE"] = "cpu"
                self.proc = subprocess.Popen(
                    [DIRECTOR_VENV_PY, PLANNER_SCRIPT], cwd=REPO,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=errf, text=True, bufsize=1, env=env)
                if errf is not subprocess.DEVNULL:
                    errf.close()      # child keeps its own dup; parent doesn't need the handle
            except Exception:
                self.proc = None
                return
            threading.Thread(target=self._await_ready, daemon=True).start()

    def _read_json(self, tries=400):
        """Read the next JSON object from the daemon, skipping any stray non-JSON stdout
        (library banners, etc.) so the protocol can never wedge on an unexpected line."""
        for _ in range(tries):
            try:
                p = self.proc               # kill() may null it mid-read from another thread
                line = p.stdout.readline() if p else None
            except Exception:
                return None
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except Exception:
                continue
        return None

    def _await_ready(self):
        obj = self._read_json()
        self.ready = bool(obj and obj.get("ready"))
        self.info = (obj.get("info") or "") if obj else ""
        if obj is None:              # the daemon died during load -> surface WHY instead of 'waking…' forever
            try:
                lines = [l.strip() for l in open(os.path.join(REPO, "consult_daemon.err")).read().splitlines() if l.strip()]
                self.last_error = (lines[-1][-160:] if lines else "model failed to load")
            except Exception:
                self.last_error = "model failed to load (see consult_daemon.err)"

    def ask(self, history, image, raw=False):
        """Blocking request/response (call from a worker thread). raw=True -> plain chat, no config."""
        if not self.alive() or not self.ready:
            return {"error": "the model isn't ready yet"}
        try:
            self.proc.stdin.write(json.dumps({"messages": history, "image": image, "raw": raw}) + "\n")
            self.proc.stdin.flush()
            obj = self._read_json()
            return obj if obj is not None else {"error": "no response from the model"}
        except Exception as e:
            return {"error": str(e)}

    def ask_stream(self, history, image, on_chunk, raw=False):
        """Streaming request: calls on_chunk(text) per chunk as the reply generates; returns the final
        {"reply","config"} (or {"error"}). Any failure -> an error dict (the UI handles it). The daemon
        falls back to a single final object if it does not emit chunks, so this still works either way."""
        if not self.alive() or not self.ready:
            return {"error": "the model isn't ready yet"}
        try:
            self.proc.stdin.write(json.dumps({"messages": history, "image": image,
                                              "raw": raw, "stream": True}) + "\n")
            self.proc.stdin.flush()
            while True:
                obj = self._read_json()
                if obj is None:
                    return {"error": "no response from the model"}
                if "chunk" in obj:
                    try:
                        on_chunk(obj["chunk"])
                    except Exception:
                        pass
                    continue
                return obj
        except Exception as e:
            return {"error": str(e)}

    def kill(self):
        with self._lock:
            p, self.proc, self.ready = self.proc, None, False
            self.cpu_mode = False
            self._last_warm = 0.0      # an intentional kill -> a re-warm (reopen, or GPU->CPU on a render) isn't rate-limited
        if p:
            threading.Thread(target=self._reap, args=(p,), daemon=True).start()

    @staticmethod
    def _reap(p):
        """Terminate + REAP off the UI thread so the daemon can't become a zombie / leak pipes / hold VRAM."""
        try:
            p.stdin.write(json.dumps({"quit": True}) + "\n"); p.stdin.flush()
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=10)
        except Exception:
            try:
                p.kill(); p.wait(timeout=5)
            except Exception:
                pass
        for s in (p.stdin, p.stdout):
            try:
                s.close()
            except Exception:
                pass


def _copy_chat(app, history, status):
    """Copy a chat transcript to the system clipboard (OSC52, via Textual) -> RichLog text the terminal
    can't select is now copyable. Falls back to writing a .txt if the terminal/Textual can't copy.
    `status(markup)` reports the outcome in the screen's status line."""
    if not history:
        status("[#ffcf5c]nothing to copy yet.[/#ffcf5c]")
        return
    text = "\n\n".join((("you: " if m.get("role") == "user" else "model: ") + (m.get("text") or ""))
                       for m in history)
    try:
        app.copy_to_clipboard(text)
        status("[#9dffce]copied the conversation (%d messages) to your clipboard.[/#9dffce]" % len(history))
        return
    except Exception:
        pass
    try:
        import os, time
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs",
                         "chat_%s.txt" % time.strftime("%H%M%S"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        status("[#9dffce]clipboard unavailable — saved the transcript to %s[/#9dffce]" % p)
    except Exception as e:
        status("[#ff6d6d]copy failed: %s[/#ff6d6d]" % e)


class ConsultScreen(ModalScreen):
    """Conversational creative director: describe a vision, it proposes prompts + dials,
    refine in chat, then APPLY writes the config into the NEW RUN form. Talks to a resident
    Qwen2.5-VL-7B daemon (vlm_planner.py) in the isolated venv; killed on close."""
    DEFAULT_CSS = """
    ConsultScreen { align: center middle; background: $background 80%; }
    #consultbox { width: 86%; height: 88%; border: round $primary; background: $background; padding: 1 2; }
    #consulttitle { color: $accent; text-style: bold; height: 1; }
    #consultsub { color: $secondary; height: 1; margin: 0 0 1 0; }
    #chatlog { height: 1fr; border: round $border; background: $surface-deep; color: $foreground; }
    #cfgpreview { height: auto; min-height: 5; max-height: 7; color: $warning; border: round $border; background: $surface; padding: 0 1; margin: 1 0; }
    #consultstatus { height: 1; color: $success; }
    #streampreview { height: auto; max-height: 8; color: $text-bright; background: $surface-deep; border: round $border-strong; padding: 0 1; margin: 0 0 1 0; display: none; }
    #chatimg { border: tall $border-strong; background: $background; color: $text-bright; }
    #chatimg:focus { border: tall $accent; }
    #chatmsg { height: 4; border: tall $border-strong; background: $background; color: $text-bright; }
    #chatmsg:focus { border: tall $accent; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #applybtn { background: $border-strong; color: $success; text-style: bold; }
    """
    BINDINGS = [("escape", "close", "Close"), ("ctrl+y", "copy", "Copy chat"), ("ctrl+r", "reset", "Reset chat"), Binding("ctrl+enter", "send", "Send", priority=True)]

    def __init__(self):
        super().__init__()

    # history + the proposed config live on the APP so a closed-then-reopened consult remembers them (same session)
    @property
    def history(self):
        if getattr(self.app, "_consult_history", None) is None:
            self.app._consult_history = []
        return self.app._consult_history

    @property
    def cfg(self):
        return getattr(self.app, "_consult_cfg", None) or {}

    @cfg.setter
    def cfg(self, v):
        self.app._consult_cfg = v

    def compose(self) -> ComposeResult:
        with Vertical(id="consultbox"):
            yield Static("✎  CONSULT THE DIRECTOR", id="consulttitle")
            yield Static("describe your vision in plain words — it engineers the prompts + dials; refine, then APPLY", id="consultsub")
            yield RichLog(id="chatlog", markup=True, wrap=True)
            yield Static("", id="streampreview")
            yield Static("", id="cfgpreview")
            yield Static("[dim]waking the director…[/dim]", id="consultstatus")
            yield Input(placeholder="optional: reference image path (it reads the style)", id="chatimg")
            yield TextArea("", id="chatmsg", soft_wrap=True, tab_behavior="focus")
            with Horizontal(classes="crow"):
                yield Button("➤ SEND (Ctrl+Enter)", id="sendbtn")
                yield Button("✓ APPLY TO FORM", id="applybtn")
                yield Button("⎘ COPY", id="copybtn")
                yield Button("↺ RESET", id="resetbtn")
                yield Button("✕ CLOSE", id="closebtn")

    def on_mount(self):
        self.query_one("#chatlog", RichLog).border_title = "« DIRECTOR CONSULT »"
        self.query_one("#cfgpreview", Static).border_title = "▌ PROPOSED RUN — APPLY when happy"
        self.query_one("#sendbtn", Button).disabled = True
        self._repaint()
        self._refresh_ready()
        self.set_interval(0.4, self._refresh_ready)

    def _repaint(self):
        """Re-render the persisted conversation + proposed config when reopened in the same session."""
        log = self.query_one("#chatlog", RichLog)
        log.clear()
        for m in self.history:
            who = "[#6dffab]you ›[/#6dffab]" if m.get("role") == "user" else "[#9dffce]director ›[/#9dffce]"
            log.write("%s %s" % (who, m.get("text", "")))
        if self.cfg:
            self._show_cfg(self.cfg)

    def on_unmount(self):
        # Free the 7B director's ~5.6GB as soon as CONSULT closes; it reloads on next open.
        try:
            self.app.consult.kill()
        except Exception:
            pass

    def _refresh_ready(self):
        d = self.app.consult
        send = self.query_one("#sendbtn", Button)
        if d.ready:
            if send.disabled:
                send.disabled = False
                self._status("[#9dffce]ready — tell the director what you want.  [dim](%s)[/dim][/#9dffce]" % (getattr(d, "info", "") or "loaded"))
                self.query_one("#chatmsg", TextArea).focus()
        elif d.alive():
            self._status("[dim]waking the director… loading the model[/dim]")
        else:
            if getattr(d, "last_error", ""):
                self._status(f"[#ff6d6d]director failed to load: {escape(d.last_error)} — retrying…[/#ff6d6d]")
            busy = self.app.mgr.active() is not None
            if busy:
                self._status("[#ffcf5c]a render is using the GPU — director runs on CPU (~2-3 min per reply). Pause/cancel the run, or wait, for full-speed GPU.[/#ffcf5c]")
            else:
                self._status("[dim]loading the director… (~30s; its VRAM is freed when you close CONSULT)[/dim]")
            self.app.consult.warm(cpu=busy)

    def _status(self, msg):
        self.query_one("#consultstatus", Static).update(msg)

    def on_input_submitted(self, e: Input.Submitted):
        if e.input.id in ("chatmsg", "chatimg"):   # chatmsg is a TextArea now; Enter arrives from the image Input
            self._send()

    def action_send(self):
        self._send()

    def on_button_pressed(self, e: Button.Pressed):
        if e.button.id == "sendbtn":
            self._send()
        elif e.button.id == "applybtn":
            self._apply()
        elif e.button.id == "copybtn":
            self.action_copy()
        elif e.button.id == "resetbtn":
            self.action_reset()
        elif e.button.id == "closebtn":
            self.action_close()

    def _send(self):
        if getattr(self, "_inflight", False):   # a reply is streaming; a 2nd reader would corrupt the JSON framing
            return
        if not self.app.consult.ready:
            return
        msg = (self.query_one("#chatmsg", TextArea).text or "").strip()
        if not msg:
            return
        img = (self.query_one("#chatimg", Input).value or "").strip()
        log = self.query_one("#chatlog", RichLog)
        log.write(f"[#6dffab]you ›[/#6dffab] {msg}")
        if img:
            log.write(f"[dim]   (reference image: {img})[/dim]")
        self.history.append({"role": "user", "text": msg})
        self.query_one("#chatmsg", TextArea).text = ""
        self.query_one("#sendbtn", Button).disabled = True
        self._status("[dim]the director is thinking…[/dim]")
        self._inflight = True
        self._stream_buf = ""
        sp = self.query_one("#streampreview", Static)
        sp.display = True
        sp.update("[#9dffce]director ›[/#9dffce] [dim]…[/dim]")
        self._ask(list(self.history), img or None)

    @work(thread=True)
    def _ask(self, history, image):
        resp = self.app.consult.ask_stream(
            history, image, lambda piece: self.app.call_from_thread(self._on_chunk, piece))
        self.app.call_from_thread(self._reply, resp)

    def _on_chunk(self, piece):
        if not self.is_mounted:      # screen closed mid-stream: dropping a chunk beats crashing the worker
            return
        self._stream_buf = getattr(self, "_stream_buf", "") + piece
        raw = self._stream_buf.split("```")[0].strip()             # hide the trailing JSON config block
        body = ("… " if len(raw) > 600 else "") + escape(raw[-600:])   # show the TAIL so the newest text stays in view
        self.query_one("#streampreview", Static).update("[#9dffce]director ›[/#9dffce] %s[dim]▌[/dim]" % body)

    def _reply(self, resp):
        self._inflight = False
        if not self.is_mounted:      # screen closed mid-reply: persist the answer, skip the dead widgets
            try:
                if resp and resp.get("reply"):
                    self.history.append({"role": "assistant", "text": resp.get("reply", "")})
            except Exception:
                pass
            return
        self.query_one("#sendbtn", Button).disabled = False
        sp = self.query_one("#streampreview", Static)
        sp.display = False
        sp.update("")
        if resp.get("error"):
            self._status(f"[#ff6d6d]error: {resp['error']}[/#ff6d6d]")
            return
        reply, cfg = resp.get("reply", ""), (resp.get("config") or {})
        self.query_one("#chatlog", RichLog).write(f"[#9dffce]director ›[/#9dffce] {reply}")
        self.history.append({"role": "assistant", "text": reply})
        if cfg:
            self.cfg = cfg
            self._show_cfg(cfg)
            self._status("[#9dffce]ready — press APPLY to load this into the form, or keep refining.[/#9dffce]")
        else:
            self._status("[#ffcf5c]ready — no config parsed this turn; ask again or APPLY the last one.[/#ffcf5c]")

    def _show_cfg(self, c):
        self.query_one("#cfgpreview", Static).update(
            f"[b]proposed[/b]  mode={c.get('mode','?')}  res={c.get('res','?')}  {c.get('seconds','?')}s  "
            f"seg={c.get('seg','?')}  steps={c.get('steps','?')}  cfg={c.get('cfg','?')}  seed={c.get('seed','?')}\n"
            f"prompt: {str(c.get('prompt',''))[:84]}\n"
            f"directive: {str(c.get('directive',''))[:84]}\n"
            f"anchors: {str(c.get('anchors',''))[:84]}")

    def _apply(self):
        c = self.cfg
        if not c:
            self._status("[#ffcf5c]no config to apply yet — describe what you want first.[/#ffcf5c]")
            return
        self.app._apply_config(c)        # shared form-fill (also used by ⧉ CLONE)
        try:
            self.app.query_one("#newinfo", Static).update(
                "[#9dffce]Applied the director's config — tweak anything, then QUEUE RUN.[/#9dffce]")
        except Exception:
            pass
        self.dismiss()

    def action_close(self):
        self.dismiss()

    def action_copy(self):
        _copy_chat(self.app, self.history, self._status)

    def action_reset(self):
        self.history.clear()
        self.cfg = {}
        self.query_one("#chatlog", RichLog).clear()
        self.query_one("#cfgpreview", Static).update("")
        self._status("[#9dffce]chat reset — start fresh.[/#9dffce]")


class ChatScreen(ModalScreen):
    """Talk to the underlying Qwen2.5-VL model DIRECTLY — not 'as the director', just the raw
    language/vision model for general use. Reuses the resident daemon with a neutral system prompt
    (raw=True); the model's VRAM is freed on close, same as consult."""
    DEFAULT_CSS = """
    ChatScreen { align: center middle; background: $background 80%; }
    #chatbox { width: 86%; height: 88%; border: round $primary; background: $background; padding: 1 2; }
    #chattitle { color: $accent; text-style: bold; height: 1; }
    #chatsub { color: $secondary; height: 1; margin: 0 0 1 0; }
    #rawlog { height: 1fr; border: round $border; background: $surface-deep; color: $foreground; }
    #rawstatus { height: 1; color: $success; }
    #rawstream { height: auto; max-height: 8; color: $text-bright; background: $surface-deep; border: round $border-strong; padding: 0 1; margin: 0 0 1 0; display: none; }
    #rawimg { border: tall $border-strong; background: $background; color: $text-bright; }
    #rawimg:focus { border: tall $accent; }
    #rawmsg { height: 4; border: tall $border-strong; background: $background; color: $text-bright; }
    #rawmsg:focus { border: tall $accent; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    """
    BINDINGS = [("escape", "close", "Close"), ("ctrl+y", "copy", "Copy chat"), ("ctrl+r", "reset", "Reset chat"), Binding("ctrl+enter", "send", "Send", priority=True)]

    def __init__(self):
        super().__init__()

    @property
    def history(self):
        # history lives on the APP so a closed-then-reopened chat remembers the conversation (same session)
        if getattr(self.app, "_chat_history", None) is None:
            self.app._chat_history = []
        return self.app._chat_history

    def compose(self) -> ComposeResult:
        with Vertical(id="chatbox"):
            yield Static("»  CHAT WITH THE MODEL  (Qwen2.5-VL, raw)", id="chattitle")
            yield Static("a direct conversation with the language/vision model — not the director. attach an image path to discuss it.", id="chatsub")
            yield RichLog(id="rawlog", markup=True, wrap=True)
            yield Static("", id="rawstream")
            yield Static("[dim]waking the model…[/dim]", id="rawstatus")
            yield Input(placeholder="optional: image path to show the model", id="rawimg")
            yield TextArea("", id="rawmsg", soft_wrap=True, tab_behavior="focus")
            with Horizontal(classes="crow"):
                yield Button("➤ SEND (Ctrl+Enter)", id="rawsendbtn")
                yield Button("⎘ COPY", id="rawcopybtn")
                yield Button("↺ RESET", id="rawresetbtn")
                yield Button("✕ CLOSE", id="rawclosebtn")

    def on_mount(self):
        self.query_one("#rawlog", RichLog).border_title = "« MODEL CHAT »"
        self.query_one("#rawsendbtn", Button).disabled = True
        self._repaint()
        self._refresh_ready()
        self.set_interval(0.4, self._refresh_ready)

    def _repaint(self):
        """Re-render the persisted conversation when reopened in the same session."""
        log = self.query_one("#rawlog", RichLog)
        log.clear()
        for m in self.history:
            who = "[#6dffab]you ›[/#6dffab]" if m.get("role") == "user" else "[#9dffce]model ›[/#9dffce]"
            log.write("%s %s" % (who, m.get("text", "")))

    def on_unmount(self):
        try:
            self.app.consult.kill()
        except Exception:
            pass

    def _refresh_ready(self):
        d = self.app.consult
        send = self.query_one("#rawsendbtn", Button)
        if d.ready:
            if send.disabled:
                send.disabled = False
                self._status("[#9dffce]ready — ask the model anything.  [dim](%s)[/dim][/#9dffce]" % (getattr(d, "info", "") or "loaded"))
                self.query_one("#rawmsg", TextArea).focus()
        elif d.alive():
            self._status("[dim]waking the model… loading[/dim]")
        else:
            if getattr(d, "last_error", ""):
                self._status(f"[#ff6d6d]model failed to load: {escape(d.last_error)} — retrying…[/#ff6d6d]")
            busy = self.app.mgr.active() is not None
            if busy:
                self._status("[#ffcf5c]a render is using the GPU — chat runs on CPU (~2-3 min per reply). Pause/cancel the run, or wait, for full-speed GPU.[/#ffcf5c]")
            else:
                self._status("[dim]loading the model… (its VRAM is freed when you close this)[/dim]")
            self.app.consult.warm(cpu=busy)

    def _status(self, msg):
        self.query_one("#rawstatus", Static).update(msg)

    def on_input_submitted(self, e: Input.Submitted):
        if e.input.id in ("rawmsg", "rawimg"):     # rawmsg is a TextArea now; Enter arrives from the image Input
            self._send()

    def action_send(self):
        self._send()

    def on_button_pressed(self, e: Button.Pressed):
        if e.button.id == "rawsendbtn":
            self._send()
        elif e.button.id == "rawcopybtn":
            self.action_copy()
        elif e.button.id == "rawresetbtn":
            self.action_reset()
        elif e.button.id == "rawclosebtn":
            self.action_close()

    def _send(self):
        if getattr(self, "_inflight", False):   # a reply is streaming; a 2nd reader would corrupt the JSON framing
            return
        if not self.app.consult.ready:
            return
        msg = (self.query_one("#rawmsg", TextArea).text or "").strip()
        if not msg:
            return
        img = (self.query_one("#rawimg", Input).value or "").strip()
        log = self.query_one("#rawlog", RichLog)
        log.write(f"[#6dffab]you ›[/#6dffab] {msg}")
        if img:
            log.write(f"[dim]   (image: {img})[/dim]")
        self.history.append({"role": "user", "text": msg})
        self.query_one("#rawmsg", TextArea).text = ""
        self.query_one("#rawsendbtn", Button).disabled = True
        self._status("[dim]the model is thinking…[/dim]")
        self._inflight = True
        self._stream_buf = ""
        sp = self.query_one("#rawstream", Static)
        sp.display = True
        sp.update("[#9dffce]model ›[/#9dffce] [dim]…[/dim]")
        self._ask(list(self.history), img or None)

    @work(thread=True)
    def _ask(self, history, image):
        resp = self.app.consult.ask_stream(
            history, image, lambda piece: self.app.call_from_thread(self._on_chunk, piece), raw=True)
        self.app.call_from_thread(self._reply, resp)

    def _on_chunk(self, piece):
        if not self.is_mounted:      # screen closed mid-stream: dropping a chunk beats crashing the worker
            return
        self._stream_buf = getattr(self, "_stream_buf", "") + piece
        raw = self._stream_buf.strip()
        body = ("… " if len(raw) > 600 else "") + escape(raw[-600:])   # show the TAIL so the newest text stays in view
        self.query_one("#rawstream", Static).update("[#9dffce]model ›[/#9dffce] %s[dim]▌[/dim]" % body)

    def _reply(self, resp):
        self._inflight = False
        if not self.is_mounted:      # screen closed mid-reply: persist the answer, skip the dead widgets
            try:
                if resp and resp.get("reply"):
                    self.history.append({"role": "assistant", "text": resp.get("reply", "")})
            except Exception:
                pass
            return
        self.query_one("#rawsendbtn", Button).disabled = False
        sp = self.query_one("#rawstream", Static)
        sp.display = False
        sp.update("")
        if resp.get("error"):
            self._status(f"[#ff6d6d]error: {resp['error']}[/#ff6d6d]")
            return
        reply = resp.get("reply", "")
        self.query_one("#rawlog", RichLog).write(f"[#9dffce]model ›[/#9dffce] {reply}")
        self.history.append({"role": "assistant", "text": reply})
        self._status("[#9dffce]ready — keep chatting.[/#9dffce]")

    def action_close(self):
        self.dismiss()

    def action_copy(self):
        _copy_chat(self.app, self.history, self._status)

    def action_reset(self):
        self.history.clear()
        self.query_one("#rawlog", RichLog).clear()
        self._status("[#9dffce]chat reset — start fresh.[/#9dffce]")


class EnhanceOptsScreen(ModalScreen):
    """Per-pass enhance options with a live resolution guard. dismiss(dict) runs, dismiss(None) cancels."""
    DEFAULT_CSS = """
    EnhanceOptsScreen { align: center middle; background: $background 80%; }
    #eoptbox { width: 66; height: auto; border: round $primary; background: $background; padding: 1 2; }
    #eopttitle { color: $accent; text-style: bold; height: 1; }
    #eoptsub { color: $secondary; height: auto; margin: 0 0 1 0; }
    #eopt_warn { height: auto; min-height: 1; color: $success; margin: 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #eopt_run { background: $border-strong; color: $success; text-style: bold; }
    .infobtn { width: 3; min-width: 3; border: none; background: $panel; color: $accent; }
    .infobtn:hover { background: $border-strong; color: $success; }
    #eopt_help { display: none; border: round $border; background: $surface; color: $success; padding: 0 1; margin: 1 0 0 0; height: auto; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, w0, h0, n_frames, defaults):
        super().__init__()
        self._help_showing = None
        self.w0 = w0 or 0
        self.h0 = h0 or 0
        self.n_frames = n_frames or 0
        self.defaults = defaults
        try:
            with open("/proc/meminfo") as f:
                self.ram_gb = int(next(l for l in f if l.startswith("MemTotal")).split()[1]) / 1e6
        except Exception:
            self.ram_gb = 0.0

    def compose(self) -> ComposeResult:
        with Vertical(id="eoptbox"):
            yield Static("▲  ENHANCE", id="eopttitle")
            yield Static("pick the passes for this iteration — you can re-enhance the result again afterward", id="eoptsub")
            yield field("INTERP", Select([("none (1x)", "1"), ("2x smooth", "2"), ("4x ultra", "4")],
                                         value=self.defaults["interp"], id="eopt_interp", allow_blank=False), "interp")
            yield field("INTERP ENGINE", Select([("RIFE (fast)", "rife"), ("FILM (large motion)", "film")],
                                                value=self.defaults.get("interp_engine", "rife"), id="eopt_interpeng", allow_blank=False), "interpeng")
            yield field("UPSCALE", Select([("off", "0"), ("2x (lighter)", "2"), ("4x", "4")],
                                          value=self.defaults["upscale"], id="eopt_up", allow_blank=False), "upscale")
            yield field("UP MODEL", Select([("Real-ESRGAN (smooth)", "realesrgan"), ("DAT-2 (realistic)", "dat2"), ("HAT-L (max, heavy)", "hat"), ("SwinIR (light)", "swinir")],
                                           value=self.defaults.get("upmodel", "realesrgan"), id="eopt_upmodel", allow_blank=False), "upmodel")
            yield field("FACE", Select([("off", "0"), ("GFPGAN", "gfpgan"), ("CodeFormer (tunable)", "codeformer")],
                                       value=self.defaults["face"], id="eopt_face", allow_blank=False), "face")
            yield field("DEFLICKER", Select([("off", "0"), ("on (stabilize)", "1")],
                                            value=self.defaults.get("deflicker", "0"), id="eopt_deflicker", allow_blank=False), "deflicker")
            yield field("RESTORE", Select([("none", "none"), ("SeedVR2-3B", "seedvr2-3b"), ("SeedVR2-7B", "seedvr2-7b")],
                                          value=self.defaults.get("restore", "none"), id="eopt_restore", allow_blank=False), "restore")
            yield field("FEATHER TILES", Select([("off", "0"), ("on", "1")],
                                                value=self.defaults.get("tile_feather", "0"), id="eopt_tilefeather", allow_blank=False), "tilefeather")
            yield field("SKIP CUTS", Select([("off", "0"), ("on", "1")],
                                            value=self.defaults.get("interp_skip", "0"), id="eopt_interpskip", allow_blank=False), "interpskip")
            yield Static("", id="eopt_warn")
            yield Static("", id="eopt_eta")
            yield Static("", id="eopt_help")
            with Horizontal(classes="crow"):
                yield Button("▲ RUN", id="eopt_run")
                yield Button("✕ CANCEL", id="eopt_close")

    def on_mount(self):
        self.query_one("#eoptbox").border_title = "« ENHANCE PASSES »"
        self._refresh()

    def on_select_changed(self, e):
        self._refresh()

    def _refresh(self):
        up_factor = int(self.query_one("#eopt_up", Select).value or "0")
        up = up_factor > 0
        face = self.query_one("#eopt_face", Select).value != "0"
        interp = int(self.query_one("#eopt_interp", Select).value or "1")
        warn = self.query_one("#eopt_warn", Static)
        run = self.query_one("#eopt_run", Button)
        out_w = self.w0 * (up_factor or 1)
        out_h = self.h0 * (up_factor or 1)
        # PEAK is always the x4 intermediate when any upscale is on: the engine runs the x4 model
        # and 2x is a post-downscale, so estimating with the 2x output under-reads peak RAM ~4x.
        peak_w = self.w0 * (4 if up_factor else 1)
        peak_h = self.h0 * (4 if up_factor else 1)
        out_frames = self.n_frames * interp
        # The enhance pipeline holds EVERY output frame in RAM (float32) at once, and the face
        # pass transiently doubles it. A 161-frame 768x512 interp4+upscale+face run hit ~48 GB and
        # OOM-killed the studio. Estimate peak RAM and BLOCK what won't fit on this box.
        factor = 2.0 if face else 1.3
        peak_gb = (out_frames * peak_w * peak_h * 3 * 4 * factor / 1e9) if (out_w and out_h) else 0
        msg = f"out ≈ {out_frames} frames @ {out_w}×{out_h} · ~{peak_gb:.0f} GB RAM"
        block = bool(self.ram_gb and peak_gb > 0.75 * self.ram_gb)
        if block:
            warn.update(f"[#ff6d6d]⛔ {msg} — over your {self.ram_gb:.0f} GB; this WILL OOM-crash. "
                        f"Lower INTERP, or turn UPSCALE/FACE off.[/#ff6d6d]")
        elif self.ram_gb and peak_gb > 0.45 * self.ram_gb:
            warn.update(f"[#ffcf5c]!! {msg} — heavy (of {self.ram_gb:.0f} GB), close to the limit.[/#ffcf5c]")
        elif out_w:
            warn.update(f"[#9dffce]{msg}[/#9dffce]")
        else:
            warn.update("")
        run.disabled = block
        # [HIGH] Enhance ETA — calibrated per 100 SOURCE frames: upscale ~80s/100f @ 4x (2x runs the
        # same x4 model then downscales, so it costs the same as 4x), interp ~50s/100f, face ~20s/100f.
        n100 = self.n_frames / 100.0
        eta = 0.0
        if up_factor:
            eta += 80.0 * n100
        if interp > 1:
            eta += 50.0 * n100
        if face:
            eta += 20.0 * n100
        self.query_one("#eopt_eta", Static).update(
            f"[#ffcf5c]plan: ~{fmt(int(eta))}[/#ffcf5c]" if eta > 0 else "[dim]plan: no passes selected[/dim]")

    def on_button_pressed(self, e):
        if e.button.id and e.button.id.startswith("i_"):
            self._toggle_help(e.button.id[2:])
            return
        if e.button.id == "eopt_run":
            self.dismiss(dict(interp=self.query_one("#eopt_interp", Select).value,
                              interp_engine=self.query_one("#eopt_interpeng", Select).value,
                              upscale=self.query_one("#eopt_up", Select).value,
                              upmodel=self.query_one("#eopt_upmodel", Select).value,
                              face=self.query_one("#eopt_face", Select).value,
                              deflicker=self.query_one("#eopt_deflicker", Select).value,
                              restore=self.query_one("#eopt_restore", Select).value,
                              tile_feather=self.query_one("#eopt_tilefeather", Select).value,
                              interp_skip=self.query_one("#eopt_interpskip", Select).value))
        else:
            self.dismiss(None)

    def _toggle_help(self, key):
        box = self.query_one("#eopt_help", Static)
        if self._help_showing == key:
            box.display = False
            self._help_showing = None
        else:
            box.update(f"[#6dffab]ⓘ[/#6dffab]  {EHELP.get(key, '')}")
            box.display = True
            self._help_showing = key

    def action_close(self):
        self.dismiss(None)


from studio_modals import (FrameScrollScreen, ConfirmDeleteScreen, ThemePickerScreen,
                           RenameScreen, RerollScreen, PairScreen, ReplicateScreen,
                           RatePairScreen)


class Studio(App):
    TITLE = "LTX STUDIO"
    # Every color below is a THEME VARIABLE (Ctrl+K themes restyle the whole shell). The pipboy
    # theme's values equal the old literals, so the default look is unchanged. border/border-strong/
    # surface-deep/text-bright are custom vars every theme in EXTRA_THEMES must define. Inline Rich
    # markup in dynamic content (readout bars, cards, schematics) is still pipboy-green -> T20.
    CSS = """
    Screen { background: $background; color: $foreground; }
    #topbar { dock: top; height: 1; background: $panel; }
    #topbartitle { width: 1fr; color: $accent; content-align: center middle; text-style: bold; }
    #statusmeter { width: auto; min-width: 24; content-align: right middle; text-style: bold; padding: 0 1 0 0; }
    Tabs { background: $panel; }
    Tab { color: $secondary; }
    Tab.-active { color: $success; text-style: bold; }
    /* When the tab bar has FOCUS, Textual paints the active tab on $block-cursor-background —
       our Tab.-active color (app-tier CSS) was overriding its readable pairing, which turned the
       label invisible on themes where success ~= accent (ice/white/tube). Restore the pair. */
    Tabs:focus Tab.-active { color: $block-cursor-foreground; }
    TabbedContent { height: 1fr; }
    TabbedContent > Tabs { dock: top; }
    .sec { color: $accent; text-style: bold; border-bottom: dashed $border; margin: 1 0 0 0; }
    .lbl { width: 13; color: $foreground; content-align: left middle; }
    .row { width: 1fr; height: 3; }
    .row Input, .row Select { width: 1fr; }
    .row.tarow { height: 5; }
    .row.tarow .lbl { content-align: left top; }
    TextArea.ta { width: 1fr; height: 5; border: tall $border-strong; background: $background; color: $text-bright; }
    TextArea.ta:focus { border: tall $accent; }
    Input { border: tall $border-strong; background: $background; color: $text-bright; }
    Input:focus { border: tall $accent; }
    Select, Switch { background: $background; }
    #form { width: 52; padding: 0 2 0 1; }
    #rightcol { width: 1fr; height: 100%; margin: 0 1 0 2; overflow: hidden; }
    #rctop { width: 1fr; height: 1fr; }
    #rleft { width: 3fr; min-width: 30; height: 1fr; margin: 0 1 0 0; overflow-y: auto; }
    /* NARROW rail (portable monitor): stack vertically at natural sizes and let the RAIL scroll.
       Squeezing three boxes into ~30 rows crushed whichever came last (the READOUT vanished to a
       border sliver) — scrolling keeps every box fully real. Wide layout unchanged. */
    #rightcol.-narrow { overflow-y: auto; }
    #rightcol.-narrow #rctop { layout: vertical; height: auto; }
    #rightcol.-narrow #rleft { width: 1fr; height: auto; margin: 0 0 1 0; }
    #rightcol.-narrow #infopanel { width: 1fr; height: 12; }
    #rightcol.-narrow #fieldvisual { height: 12; }
    #rightcol.-narrow #readout { height: 15; }
    /* height priority: READOUT is FIXED at 15 (title + six 2-line gauges, exactly) and the
       SCHEMATIC flexes — on short rails the schematic loses hint lines, never the gauges.
       (The old 1fr readout silently clipped SHOTS/QUAL/DRIFT on the portable monitor.) */
    #fieldvisual { width: 1fr; height: 1fr; min-height: 10; max-height: 16;
                   border: round $primary; background: $surface;
                   padding: 0 1; margin: 0 0 1 0; overflow: hidden; }
    #readout { width: 1fr; height: 15; border: round $border; background: $surface;
               padding: 0 1; overflow: hidden; }
    #infopanel { width: 2fr; min-width: 18; height: 1fr; border: round $border; background: $surface; padding: 0 1; }
    #newinfo { width: 1fr; height: auto; }
    #blindpanel { width: 1fr; border: round $primary; background: $surface; padding: 0 1; margin: 0 0 1 0; height: auto; display: none; }
    #blindpanel.-active { display: block; }
    #blindtitle { color: $accent; text-style: bold; height: 1; margin: 1 0 0 0; }
    #blindsub { color: $secondary; height: auto; margin: 0 0 1 0; }
    #blindvarrow { height: 3; }
    #blindvarrow .lbl { width: 15; }
    .blindab { height: auto; }
    .blindab .lbl { width: 15; }
    #blindmsg { color: $warning; height: auto; margin: 1 0 0 0; }
    #blindbtns { height: 3; margin-top: 1; }
    #blindbtns Button { margin-right: 2; }
    #blind_run { background: $border-strong; color: $success; text-style: bold; }
    #runest { padding: 1 0 0 0; }
    Button { border: tall $border; background: $panel; color: $text-bright; }
    Button:hover { background: $border-strong; }
    #queuebtn { margin-top: 1; }
    #consultbtn { margin-top: 1; }
    #chatbtn { margin-bottom: 1; }
    DataTable { background: $surface-deep; color: $foreground; border: round $border; height: 1fr; }
    DataTable > .datatable--cursor { background: $border-strong; color: $success; }
    DataTable > .datatable--header { background: $panel; color: $accent; }
    ProgressBar { margin: 1 1; }
    Bar > .bar--bar { color: $primary; }
    Bar > .bar--complete { color: $success; }
    RichLog { border: round $border; background: $surface-deep; color: $foreground; height: 1fr; }
    #livehdr { color: $accent; text-style: bold; padding: 1 1 1 1; border-bottom: dashed $border-strong; }
    #livephase { color: $success; padding: 0 1; height: 1; }
    #progtext { color: $foreground; padding: 0 1; height: 1; }
    #livemid { height: 1fr; min-height: 8; margin: 1 0 0 0; }
    #preview { width: 50; height: 1fr; border: round $border; background: $surface-deep; content-align: center middle; }
    #notescol { width: 1fr; height: 1fr; }
    #director { color: $warning; padding: 0 1; height: auto; max-height: 4; }
    #dirnotes { border: round $border; background: $surface-deep; color: $foreground; height: 1fr; }
    .strip { height: 2; margin: 0; padding: 0 1; border-top: dashed $border-strong; }
    .strip Static { width: 1fr; height: 1; color: $foreground; content-align: left middle; }
    /* SHORT terminals (16" portable monitor etc): the LIVE tab's fixed stack used to overflow and
       push the PAUSE/CANCEL row off-screen. -short (height < 38) sheds the two lower-value strips
       (PACE, STEERING) but KEEPS the PHASES stopwatch — the one live meter worth its rows. -tiny
       (height < 30) sheds PHASES too, restoring the controls-always-reachable guarantee on genuinely
       small/snapped windows. #livemid (1fr) shrinks to soak up the slack in both regimes. */
    Screen.-short #pacestrip, Screen.-short #steerstrip { display: none; }
    Screen.-tiny #phasestrip { display: none; }
    Screen.-short #livemid { min-height: 6; }
    #ph_timeline { width: 1fr; }
    #livelog { display: none; height: 8; }
    #livebar { height: 1; background: $panel; color: $accent; content-align: left middle; padding: 0 1; }
    #inspectpanel { border: round $border; background: $surface; height: 1fr; padding: 0 1; }
    #insprow { height: auto; }
    #inspectinfo { color: $foreground; width: 1fr; }
    #inspthumb { width: auto; margin-left: 2; display: none; }
    #qinspectpanel { border: round $border; background: $surface; height: 1fr; padding: 0 1; }
    #qinspect { color: $foreground; }
    #inspectlog { display: none; }
    #reltable { height: 8; margin-top: 1; display: none; }
    #playchildbtn { display: none; margin-top: 1; }
    #status { height: 2; border-top: solid $border; background: $panel; color: $accent; content-align: left middle; }
    .actions { height: 3; }
    .actions Button { margin-right: 1; }
    .arcrow Button { min-width: 0; }
    .arcsep { width: 1; height: 3; color: $border; content-align: center middle; margin: 0 1; }
    .arcflex { width: 1fr; height: 1; }
    Button.-active { background: $border-strong; color: $success; border: tall $accent; }
    """
    BINDINGS = [("ctrl+c", "quit", "Quit"), ("t", "toggle_term", "Terminal"),
                ("d", "toggle_dirraw", "Dir raw"),
                ("s", "suspend", "Suspend"), ("r", "resume", "Resume"),
                ("ctrl+p", "cycle_preview", "Preview"),
                ("ctrl+k", "pick_theme", "Theme"),
                Binding("ctrl+enter", "form_queue", "Queue run", priority=True)]

    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self):
        super().__init__()
        self.mgr = JobManager()
        self._live_id = None
        self._live_n = 0
        self._preview_id = None
        self._preview_mtime = 0.0
        self._preview_cols = 0
        self._preview_mode = None
        self._notes_n = 0
        self._dir_raw = False        # director's-notes raw-output toggle (collapsed by default)
        self._qsig = None
        self._asig = None
        self._insp_jid = None
        self._insp_view = "inspect"     # which archive view #inspectinfo shows
        self._smart_step_wall = None    # T25: wall-clock the step counter last advanced
        self._smart_step_key = None     # (job.id, seg, step) the wall-clock above belongs to
        self._smart_max = 0             # T25: latched running max % (display monotonicity)
        self._smart_max_id = None       # job.id the latched max belongs to
        self._beat = 0
        self._gpu_str = ""
        self.consult = ConsultDaemon()

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static("VAULT-TEC  PIP-BOY 3000  ::  L T X   S T U D I O  ::  JOB CONTROL", id="topbartitle")
            yield Static("", id="statusmeter")
        with TabbedContent(initial="tab-new"):
            with TabPane("▌ NEW RUN", id="tab-new"):
                with Horizontal():
                    with VerticalScroll(id="form"):
                        yield Static("▌ SHOT", classes="sec")
                        yield field("MODE", Select([("single clip", "single"), ("DIRECTOR (long)", "director")], value="single", id="mode", allow_blank=False))
                        yield Button("✎ CONSULT THE DIRECTOR", id="consultbtn")
                        yield Button("» CHAT WITH THE MODEL", id="chatbtn")
                        yield field("PROMPT", TextArea("", id="prompt", soft_wrap=True, tab_behavior="focus", classes="ta"))
                        yield field("IMAGE", Input(placeholder="input/start.png (optional)", id="image"))
                        yield field("LENGTH s", Input(value="4", id="seconds"))
                        yield Static("▌ QUALITY", classes="sec")
                        yield field("BACKEND", Select([("LTX-2B (fast)", "ltx"), ("Wan-VACE-1.3B (nicer, slower)", "wan"), ("Wan turbo (4-step distill)", "wan-turbo")], value="ltx", id="backend", allow_blank=False))
                        yield field("RES", Select([(k, k) for k in RES], value="704 x 480  balanced", id="res", allow_blank=False))
                        yield field("STEPS", Input(value="40", id="steps"))
                        yield field("GUIDANCE", Input(value="3.0", id="cfg"))
                        yield field("SEED", Input(value="", id="seed", placeholder="blank = random (default)"))
                        yield field("FPS", Input(value="24", id="fps"))
                        yield Static("▌ STYLE", classes="sec")
                        if style_presets is not None:   # T27: pick a preset -> its words APPEND to ANCHORS below
                            yield field("STYLE", Select([(n, n) for n in style_presets.load_presets(REPO)],
                                                        id="style_preset", prompt="+ add a style…", allow_blank=True))
                        yield field("ANCHORS", TextArea("", id="anchors", soft_wrap=True, tab_behavior="focus", classes="ta"))
                        yield field("NEG", TextArea(NEG, id="n_prompt", soft_wrap=True, tab_behavior="focus", classes="ta"))
                        yield Static("▌ CHAINING", classes="sec")
                        yield field("SEGMENT s", Input(value="3", id="seg"), "seg")
                        yield field("CONTINUITY", Input(value="1.0", id="cond_strength"))
                        yield Static("▌ DIRECTOR", classes="sec")
                        yield field("DIRECTIVE", TextArea("", id="directive", soft_wrap=True, tab_behavior="focus", classes="ta"), "directive")
                        yield field("STEADINESS", Select([("Hold (faithful)", "hold"), ("Balanced", "balanced"), ("Evolve (journey)", "evolve")], value="hold", id="steadiness", allow_blank=False), "steadiness")
                        yield Static("▌ ADVANCED", classes="sec")
                        yield field("GUIDANCE RESCALE", Select([("off", "off"), ("0.5", "0.5"), ("0.7", "0.7")],
                                                               value="off", id="cfg_rescale", allow_blank=False), "cfg_rescale")
                        yield field("GUIDANCE SCHEDULE", Select(
                            [("off", "off"), ("front 30%", "0.0:0.3"), ("front 50%", "0.0:0.5"),
                             ("front 70%", "0.0:0.7"), ("late 70%", "0.3:1.0"),
                             ("every 2nd step", "2"), ("every 3rd step", "3")],
                            value="off", id="cfg_interval", allow_blank=False), "cfg_interval")
                        yield field("IDENTITY ANCHOR", Select([("off", "off"), ("on (Wan only)", "on")],
                                                              value="off", id="wan_ref_anchor", allow_blank=False), "wan_ref_anchor")
                        yield Static("▌ STUDIO", classes="sec")
                        yield field("RESERVE (GB)", Select([("0.5 GB", "0.5"), ("1.0 GB (default)", "1.0"),
                                                             ("1.5 GB", "1.5"), ("2.0 GB", "2.0")],
                                                            value="1.0", id="vram_reserve", allow_blank=False), "vram_reserve")
                        yield field("SOUND", Select([("on", "on"), ("off", "off")],
                                                         value="on", id="sound_enabled", allow_blank=False), "sound_enabled")
                        _sfx = _sfx_options()
                        _v1 = "run_done.wav" if any(v == "run_done.wav" for _, v in _sfx) else _sfx[0][1]
                        _v2 = "run_stall.wav" if any(v == "run_stall.wav" for _, v in _sfx) else _sfx[0][1]
                        yield field("DONE SOUND", Select(_sfx, value=_v1, id="snd_done", allow_blank=False), "snd_done")
                        yield field("STALL SOUND", Select(_sfx, value=_v2, id="snd_stall", allow_blank=False), "snd_stall")
                        yield Button("▶ TEST SOUND", id="sndtestbtn")
                        yield Static("▌ OUTPUT", classes="sec")
                        yield field("NAME", Input(placeholder="optional — file name (else job_HHMMSS)", id="name"))
                        yield Static("", id="runest")
                        yield Button("▶ QUEUE RUN", id="queuebtn")
                        _rep = Button("×N REPLICATE", id="newreplbtn")
                        _rep.tooltip = ("Queue this exact config N times (2-5) with different random seeds "
                                        "— probes the seed noise floor. The runs group as a replicate set "
                                        "in the QUEUE / ARCHIVE.")
                        yield _rep
                        _bab = Button("⇄ BLIND A/B", id="blindabbtn")
                        _bab.tooltip = plain_help("blind_ab")
                        yield _bab
                    # Right region: [schematic | text help] side-by-side on top, READOUT full-width
                    # below. Three-across cramped everything; a single full-width stack wasted the
                    # horizontal space. #rightcol.-narrow (set in on_resize) restacks the top row
                    # vertically when the window is snapped/scaled small.
                    # FIXED right rail (user spec 2026-07-06): three UNMOVING boxes. Left stack
                    # (~3/5 width): schematic atop READOUT, same width. Right (~2/5): a tall,
                    # SCROLLABLE info panel. Boxes never appear/disappear or change size — only
                    # their contents update. The blind builder hides #rctop wholesale instead.
                    with Vertical(id="rightcol"):
                        with Horizontal(id="rctop"):
                            with Vertical(id="rleft"):
                                yield Static("", id="fieldvisual")
                                yield Static("", id="readout")      # T22: global readout meters
                            with VerticalScroll(id="infopanel"):
                                yield Static(INFO, id="newinfo")
                        with Vertical(id="blindpanel"):
                            yield Static("⇄  BLIND A/B RUN", id="blindtitle")
                            yield Static("Two runs from the CURRENT form, identical except ONE field — shared "
                                         "seed, randomized + blind until ↯ REVEAL. Pick a field, set A and B.",
                                         id="blindsub")
                            with Horizontal(id="blindvarrow", classes="row"):
                                yield Label("Field to vary", classes="lbl")
                                yield Select(self.BLIND_FIELD_OPTIONS, id="blind_var",
                                             prompt="pick a field…", allow_blank=True)
                            yield Vertical(id="blindclones")
                            yield Static("", id="blindmsg")
                            with Horizontal(id="blindbtns"):
                                yield Button("▶ RUN BLIND A/B", id="blind_run")
                                yield Button("✕ CANCEL", id="blind_cancel")
            with TabPane("≡ QUEUE", id="tab-queue"):
                yield DataTable(id="qtable", cursor_type="row", show_header=False)
                with Horizontal(classes="actions"):
                    yield Button("▶ RESUME", id="qresumebtn")
                    yield Button("↑ PROMOTE", id="promotebtn")
                    yield Button("» INSPECT", id="qinspectbtn")
                    yield Button("⧉ CLONE", id="qclonebtn")
                    yield Button("✕ REMOVE SELECTED", id="removebtn")
                with VerticalScroll(id="qinspectpanel"):
                    yield Static("[dim]Select a queued run above to see its full config.[/dim]", id="qinspect")
            with TabPane("▶ LIVE", id="tab-live"):
                yield Static("no active run", id="livehdr")
                yield Static("", id="livephase")
                yield ProgressBar(id="overbar", total=100, show_eta=False)
                yield Static("", id="progtext")
                with Horizontal(id="livemid"):
                    yield Static("", id="preview")
                    with Vertical(id="notescol"):
                        yield Static("", id="director")
                        yield RichLog(id="dirnotes", markup=True, wrap=True)
                with Horizontal(id="pacestrip", classes="strip"):
                    yield Static("", id="pace_rate")
                    yield Static("", id="pace_frames")
                    yield Static("", id="pace_shot")
                    yield Static("", id="pace_eta")
                with Horizontal(id="steerstrip", classes="strip"):
                    yield Static("", id="steer_mode")
                    yield Static("", id="steer_directive")
                    yield Static("", id="steer_anchors")
                with Horizontal(id="phasestrip", classes="strip"):
                    yield Static("", id="ph_timeline")
                with Horizontal(classes="actions"):
                    yield Button("‖ PAUSE", id="pausebtn")
                    yield Button("▶ RESUME", id="resumebtn")
                    yield Button("▽ SUSPEND", id="suspendbtn")
                    yield Button("■ CANCEL", id="cancelbtn")
                    yield Button("» TERMINAL", id="livetermbtn")
                    yield Button("» DIR RAW", id="dirnotebtn")
                    yield Button("▤ FRAMES", id="liveframesbtn")
                yield RichLog(id="livelog", highlight=True, markup=False, wrap=True)
                yield Static("", id="livebar")
            with TabPane("✓ ARCHIVE", id="tab-arch"):
                yield DataTable(id="atable", cursor_type="row")
                # actions in two GROUPED rows (compact .arcrow buttons, │ separators):
                #   row 1: view/inspect │ blind-pair verdicts │ favorite
                #   row 2: derive-a-new-run … [gap] … destructive (rename/delete pushed right)
                with Horizontal(classes="actions arcrow"):
                    yield Button("▶ PLAY", id="playbtn")
                    yield Button("▤ FRAMES", id="framesbtn")
                    yield Button("» INSPECT", id="inspectbtn")
                    yield Button("◷ TIMING", id="timingbtn")
                    yield Button("» TERMINAL", id="arctermbtn")
                    yield Static("│", classes="arcsep")
                    _rvl = Button("↯ REVEAL", id="revealbtn")
                    _rvl.tooltip = plain_help("blind_ab")
                    yield _rvl
                    yield Button("≷ RATE PAIR", id="ratepairbtn")
                    yield Static("│", classes="arcsep")
                    yield Button("★ FAVORITE", id="favbtn")
                with Horizontal(classes="actions arcrow"):
                    yield Button("⧉ CLONE", id="clonebtn")
                    yield Button("⟲ RE-ROLL", id="rerollbtn")
                    yield Button("⇄ PAIR A/B", id="pairbtn")
                    yield Button("×N REPLICATE", id="replbtn")
                    yield Button("▲ ENHANCE", id="enhancebtn")
                    yield Static(classes="arcflex")
                    yield Button("✎ RENAME", id="renamebtn")
                    yield Button("✕ DELETE", id="deletebtn")
                with VerticalScroll(id="inspectpanel"):
                    with Horizontal(id="insprow"):
                        yield Static("[dim]Select a run above and press INSPECT.[/dim]", id="inspectinfo")
                        yield Static("", id="inspthumb")   # T-thumb: opening-frame preview
                    yield DataTable(id="reltable", cursor_type="row")
                    yield Button("▶ PLAY SELECTED CHILD RUN", id="playchildbtn")
                yield RichLog(id="inspectlog", highlight=True, markup=False, wrap=True)
        yield Static("", id="status")
        yield Footer()

    def get_css_variables(self):
        """The stylesheet references four CUSTOM vars (border/border-strong/surface-deep/text-bright)
        that only the pipboy-family themes define. Fill safe fallbacks so picking a BUILTIN textual
        theme in the Ctrl+K picker restyles the app instead of crashing the stylesheet."""
        v = super().get_css_variables()
        v.setdefault("border", v.get("secondary", "#1f9a52"))
        v.setdefault("border-strong", v.get("panel", "#134a2a"))
        v.setdefault("surface-deep", v.get("background", "#06120b"))
        v.setdefault("text-bright", v.get("success", "#9dffce"))
        return v

    # Fields the launched command LITERALLY ignores outside director mode (verified against
    # build() + _plan()): --directive/--steadiness are appended only under `if director:`, and
    # _plan() reads the SEG field only in its director branch (single/auto-chain segment size
    # comes from the SAFE_PX cap). Values are preserved while disabled — v() still reads them.
    _DIRECTOR_ONLY = ("directive", "steadiness", "seg")

    def _sync_mode_disable(self):
        """Gray out (disable) the director-only dials when MODE != director."""
        try:
            director = (self.v("mode") == "director")
        except Exception:
            return
        for wid in self._DIRECTOR_ONLY:
            try:
                self.query_one("#" + wid).disabled = not director
            except Exception:
                pass

    def _sync_backend_disable(self):
        """Gray the dials the launched command LITERALLY ignores for the current BACKEND:
        GUIDANCE SCHEDULE is Wan-only (LTX's batched CFG forward is incompatible — it crashed two
        real runs; wan-turbo runs cfg=1.0, nothing to gate) and IDENTITY ANCHOR is Wan/VACE-only."""
        try:
            be = self.v("backend") or "ltx"
        except Exception:
            return
        for wid, dead in (("cfg_interval", be != "wan"),
                          ("wan_ref_anchor", be not in ("wan", "wan-turbo"))):
            try:
                self.query_one("#" + wid).disabled = dead
            except Exception:
                pass

    def _on_theme_changed(self, theme):
        """T20: re-tint every INLINE-markup surface from the new theme — studio SPAL (cards/meter/
        status/plan) + the readout and field_visuals module palettes — then invalidate the cached
        renders. Best-effort: a malformed theme leaves the previous palette."""
        try:
            v = dict(getattr(theme, "variables", None) or {})
            pal = {"accent": theme.accent or "#6dffab", "success": theme.success or "#9dffce",
                   "foreground": theme.foreground or "#34d977", "warning": theme.warning or "#ffcf5c",
                   "error": theme.error or "#ff6d6d", "primary": theme.primary or "#2fae5f",
                   "secondary": theme.secondary or "#1f9a52"}
            pal["text_bright"] = v.get("text-bright") or pal["success"]
            pal["border"] = v.get("border") or pal["secondary"]
            SPAL.update(pal)
            SPAL["title"] = pal["text_bright"]
            SPAL["muted"] = pal["secondary"]
            # extended slots: per-theme artistic picks, with graceful fallbacks for themes
            # (e.g. builtins) that don't define them
            SPAL["soft"] = v.get("tertiary") or pal["primary"]
            SPAL["accent2"] = v.get("accent-2") or pal["accent"]
            if field_visuals is not None:
                field_visuals.set_palette(pal)
            if readout is not None:
                readout.set_palette(pal)
            self._qsig = None            # queue cards re-render in the new palette next tick
            self.update_est()            # plan line + readout strip refresh now
        except Exception:
            pass

    def on_mount(self):
        for t in EXTRA_THEMES:
            self.register_theme(t)
        try:      # theme choice persists (picker ENTER writes it); unknown/stale names fall back
            _th = str(load_studio_config().get("theme") or "")
            self.theme = _th if (_th.startswith("pipboy") and _th in self.available_themes) else "pipboy"
        except Exception:
            self.theme = "pipboy"
        self.query_one("#qtable", DataTable).add_column("queue", key="card")   # single card column (header hidden)
        self.query_one("#atable", DataTable).add_columns("id", "title", "status", "started", "finished", "gen", "dur")
        self.query_one("#reltable", DataTable).add_columns("id", "relation", "status", "output")
        self.query_one("#livelog", RichLog).border_title = "« RAW TERMINAL »"
        self.query_one("#inspectlog", RichLog).border_title = "« RAW TERMINAL »"
        self.query_one("#inspectpanel").border_title = "« RUN DETAILS »"
        self.query_one("#qinspectpanel").border_title = "« QUEUED RUN DETAILS »"
        self.query_one("#fieldvisual").border_title = "« SCHEMATIC »"
        self.query_one("#infopanel").border_title = "« INFO »"
        self.query_one("#readout").border_title = "« READOUT »"
        self.query_one("#fieldvisual", Static).update(
            "[dim]schematic — focus a dial (or its ⓘ) to illustrate it[/dim]")
        self.call_after_refresh(self.on_resize)   # settle -narrow/-short once real sizes exist
        self.query_one("#preview", Static).border_title = "« LAST FRAME »"
        self.query_one("#dirnotes", RichLog).border_title = "« DIRECTOR'S NOTES »"
        self.query_one("#pacestrip").border_title = "« PACE »"
        self.query_one("#steerstrip").border_title = "« STEERING »"
        self.query_one("#phasestrip").border_title = "« PHASES »"
        # T14: load the persisted VRAM reserve (default 1.0 GB) into the form + the job manager
        try:
            reserve = float(load_studio_config().get("vram_reserve_gb", 1.0))
            opts = (0.5, 1.0, 1.5, 2.0)
            reserve = min(opts, key=lambda o: abs(o - reserve))   # snap to the nearest Select option
            self.query_one("#vram_reserve", Select).value = str(reserve)
            self.mgr.vram_reserve_gb = reserve
        except Exception:
            pass
        try:      # T20: inline-markup palettes follow the theme (CSS $vars already do)
            self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        except Exception:
            pass
        self._sync_mode_disable()     # initial MODE=single -> director-only dials start grayed
        self._sync_backend_disable()  # initial BACKEND=ltx -> Wan-only dials start grayed
        try:      # sound/alert prefs: master toggle + stall threshold/action/grace (defaults ON / 240s / suspend / 180s)
            _snd = load_studio_config().get("sounds", {}) or {}
            self.query_one("#sound_enabled", Select).value = "on" if _snd.get("enabled", True) else "off"
            self._stall_secs = float(_snd.get("stall_secs", 240) or 240)
            self._stall_action = str(_snd.get("stall_action", "suspend"))     # "suspend" | "alert"
            self._stall_grace = float(_snd.get("stall_grace_secs", 180) or 180)
            self._stall_decode_secs = float(_snd.get("stall_decode_secs", 600) or 600)   # warmup/decode/save fuse
            self._stall_max_secs = float(_snd.get("stall_max_secs", 7200) or 7200)       # any-phase catch-all
            _evm = dict(_snd.get("events") or {})    # reflect persisted per-event WAV picks in the pickers
            for _sid, _ev in (("snd_done", "run_done"), ("snd_stall", "run_stall")):
                try:
                    self.query_one("#" + _sid, Select).value = os.path.basename(_evm.get(_ev) or "")
                except Exception:
                    pass
        except Exception:
            self._stall_secs, self._stall_action, self._stall_grace = 240.0, "suspend", 180.0
            self._stall_decode_secs, self._stall_max_secs = 600.0, 7200.0
        # T22: readout meter strip — exposed configs (studio_config.json), both default ON
        try:
            _rc = load_studio_config()
            self.readout_enabled = bool(_rc.get("readout_enabled", True))
            self.readout_autofit = bool(_rc.get("readout_autofit", True))
        except Exception:
            self.readout_enabled, self.readout_autofit = True, True
        self.set_interval(0.5, self.tick)
        self.tick()
        self.update_est()
        # The director model is NOT pre-warmed: it loads ONLY while the CONSULT screen is open
        # (ConsultScreen warms it on open, frees it on close), so the board stays idle when you're
        # not consulting. NOTE: the real crash culprit was never VRAM — it was nvidia-smi polling
        # running CONCURRENTLY with CUDA on the WSL GPU passthrough (reproduced: it restarts the
        # whole VM). The GPU meter now pauses whenever CUDA is live — see _cuda_busy().

    def on_unmount(self):
        try:
            self.mgr.shutdown()              # T9: stop the runner + kill the live render so it can't orphan the GPU
        except Exception:
            pass
        try:
            self.consult.kill()
        except Exception:
            pass

    def _cuda_busy(self):
        """True when CUDA is live on the board (a run/enhance active, or the consult daemon
        resident). The studio must NEVER run nvidia-smi while this holds: nvidia-smi concurrent
        with CUDA on the WSL GPU passthrough crashes dxgkrnl and restarts the whole VM on this
        Blackwell + driver 591.74 + WSL2 combo (reproduced directly). Fails SAFE -> True when
        unsure, so we err toward NOT polling."""
        try:
            return self.mgr.active() is not None or self.consult.alive()
        except Exception:
            return True

    # _poll_gpu() REMOVED. nvidia-smi is BANNED in the studio: REPRODUCED that nvidia-smi polling
    # in this WSL2 (RTX 5070 Blackwell + driver 591.74) destabilizes the /dev/dxg GPU passthrough and
    # restarts the WHOLE WSL VM after ~30 calls — even with NO CUDA running at all. The live VRAM%
    # meter that used it is gone; tick() now shows working/idle from our own job state instead.

    def v(self, wid, over=None):
        # over: an optional field-id -> value dict (the blind-pair SNAPSHOT). When it carries this
        # field, read it from the dict instead of the live widget -> build()/​_plan() can construct a
        # variant WITHOUT mutating (and thus without firing on_select_changed/_sync_cfg_default on) the
        # shared form. Falls back to the live widget for any key the snapshot omits.
        if over is not None and wid in over and over[wid] is not None:
            return over[wid]
        w = self.query_one(f"#{wid}")
        return w.text if isinstance(w, TextArea) else w.value

    def on_descendant_focus(self):
        wid = getattr(self.focused, "id", None)
        if wid in HELP:
            self.query_one("#newinfo", Static).update(HELP[wid])
        # STICKY panels: the schematic + help boxes are persistent dashboard fixtures, not
        # transient tooltips. Focusing a button/table/etc must NOT blank them — only a field
        # that HAS a visual replaces the schematic (_refresh_field_visual no-ops otherwise).
        self._refresh_field_visual(wid)

    def _show_field_visual(self, wid):
        """Show the BF6-style schematic for `wid` ABOVE the text help, or clear it if the field has
        none. Purely additive: #newinfo text help is untouched. Never shown while the blind builder
        is active (it, like #newinfo, is hidden then). Defensive: any failure just clears the panel."""
        try:
            panel = self.query_one("#fieldvisual", Static)
        except Exception:
            return
        art = None
        try:
            if field_visuals is not None and wid:
                # while the blind builder owns the right region (#rctop hidden), don't bother
                if not self.query_one("#blindpanel", Vertical).has_class("-active"):
                    try:      # size the schematic to the FIXED box (falls back to render's default)
                        pw = int(panel.content_size.width)
                    except Exception:
                        pw = 0
                    art = field_visuals.render(wid, self, width=(pw if pw >= 24 else None))
        except Exception:
            art = None
        # FIXED fixture: content updates in place; a visual-less field KEEPS the previous schematic
        # (sticky-panel rule); the placeholder shows only before the first schematic ever renders.
        # no_wrap: block-art CLIPS at the box edge, never wraps (a 1-col deficit on a small monitor
        # used to smear bar fragments onto the next line).
        if art:
            _t = Text.from_markup(art)
            _t.no_wrap = True
            panel.update(_t)
            self._visual_set = True
            try:      # the border title always names WHICH dial is illustrated (sticky panels can
                panel.border_title = "« SCHEMATIC — %s »" % _dial_title(wid)   # outlive their focus)
            except Exception:
                pass
        elif not getattr(self, "_visual_set", False):
            panel.update("[dim]schematic — focus a dial (or its ⓘ) to illustrate it[/dim]")

    # any dial change -> refresh the plan estimate
    def on_input_changed(self, event):
        self.update_est()

    def on_resize(self, event=None):
        # window resized (scaled down / snapped to half) -> (1) restack the right rail vertically
        # when it gets narrow, (2) re-render the readout bars at the new panel width so they never
        # wrap. content_size can read 0 before first layout (which used to STICK -narrow on) ->
        # fall back to app width minus the fixed form; on_mount re-runs this after refresh.
        try:
            rc = self.query_one("#rightcol")
            w = int(rc.content_size.width) or max(0, int(self.size.width) - 57)
            # The user's two-column rail (schematic/readout stack | info) HOLDS at narrow widths —
            # schematic bars now scale to their panel (field_visuals._bar_w), so side-by-side works
            # down to ~52 cols. The vertical stack is a LAST RESORT for truly tiny terminals only.
            (rc.add_class if w < 52 else rc.remove_class)("-narrow")
        except Exception:
            pass
        try:      # short terminal (portable monitor): shed lower-value LIVE strips so controls stay
            h = int(self.size.height)   # visible. -short keeps the PHASES stopwatch; -tiny sheds it too
            (self.screen.add_class if h < 38 else self.screen.remove_class)("-short")
            (self.screen.add_class if h < 30 else self.screen.remove_class)("-tiny")
        except Exception:
            pass
        self.update_est()

    def on_select_changed(self, event):
        sid = getattr(event.select, "id", None)
        if sid == "blind_var":   # inline BLIND A/B builder: (re)mount the A/B clones for the chosen field
            val = event.value
            self._blind_render_clones(None if val in (None, Select.BLANK) else val)
            self.query_one("#blindmsg", Static).update("")
            return
        if sid == "style_preset":   # T27: APPEND the picked preset's words to ANCHORS, then reset the picker
            if style_presets is None or event.value in (None, Select.BLANK):
                return                                       # BLANK reset fires this again -> ignored (no loop)
            try:
                words = style_presets.load_presets(REPO).get(event.value, [])
                ta = self.query_one("#anchors", TextArea)
                ta.load_text(style_presets.apply_preset(ta.text, words))
                event.select.value = Select.BLANK            # act as an "add" picker, not a persistent choice
                self.query_one("#newinfo", Static).update("[#9dffce]+ style: %s[/#9dffce]" % event.value)
            except Exception:
                pass
            return
        if sid == "mode":      # gray out the dials the launched command literally ignores in this mode
            self._sync_mode_disable()
        if sid == "backend":   # ONLY a backend change may retune cfg/steps;
            self._sync_cfg_default()                         # RES/MODE/etc must never clobber tuned dials
            self._sync_backend_disable()                     # + gray Wan-only dials off-backend
        elif sid == "vram_reserve":   # T14: persist immediately + apply to the NEXT run this manager launches
            try:
                reserve = float(event.select.value)
                self.mgr.vram_reserve_gb = reserve
                save_studio_config({**load_studio_config(), "vram_reserve_gb": reserve})
            except Exception:
                pass
        elif sid == "sound_enabled":   # persist the run-done sound toggle to studio_config.json "sounds"
            try:
                _cfg = load_studio_config()
                _snd = dict(_cfg.get("sounds") or {})
                _snd["enabled"] = (event.select.value == "on")
                save_studio_config({**_cfg, "sounds": _snd})
                self.query_one("#newinfo", Static).update(
                    "[#9dffce]event sounds: %s[/#9dffce]" % event.select.value)
            except Exception:
                pass
        elif sid in ("snd_done", "snd_stall"):   # per-event WAV pick: persist ONLY — no audition.
            # A pick is not a request to HEAR it: the app never plays a sound the user didn't
            # explicitly ask for (user rule 2026-07-06). ▶ TEST SOUND is the opt-in play.
            try:
                ev = "run_done" if sid == "snd_done" else "run_stall"
                _cfg = load_studio_config()
                _snd = dict(_cfg.get("sounds") or {})
                _evm = dict(_snd.get("events") or {})
                _evm[ev] = "sfx/%s" % event.select.value
                _snd["events"] = _evm
                save_studio_config({**_cfg, "sounds": _snd})
                self.query_one("#newinfo", Static).update(
                    tmark("success", "♪ %s → %s   (▶ TEST SOUND to hear it)" % (ev, event.select.value)))
            except Exception:
                pass
        self.update_est()
        self._refresh_field_visual(sid)   # value-aware schematic must track the NEW select value

    def on_input_submitted(self, event):
        """Plain ENTER inside a NEW RUN form Input -> REFRESH that field's value-aware schematic
        (and the plan estimate), so a freshly TYPED value updates the tooltip marker. This is
        'enter = update this tooltip', NOT enter-to-queue: queueing stays Ctrl+Enter
        (action_form_queue). We only touch the visual + est and never enqueue. Guarded to the
        NEW RUN tab, only when the blind builder is inactive, and only when the field has a visual."""
        try:
            if self.query_one("#blindpanel", Vertical).has_class("-active"):
                return                                 # blind builder owns the region -> leave it be
        except Exception:
            pass
        self.update_est()
        self._refresh_field_visual(getattr(getattr(event, "input", None), "id", None))

    def _refresh_field_visual(self, wid):
        """Re-render #fieldvisual for `wid` via the SAME path used on focus, but only if that field
        actually has a visual (so a plain-Enter on a visual-less field doesn't blank a currently
        shown one). No-op when field_visuals is unavailable or the blind builder is active."""
        if not wid or field_visuals is None:
            return
        try:
            if field_visuals.VISUALS.get(wid) is None:
                return                                 # no schematic for this field -> don't disturb the panel
        except Exception:
            return
        self._show_field_visual(wid)

    def _sync_cfg_default(self):
        """Retarget GUIDANCE to the selected backend's sweet spot (LTX ~3.0 / Wan ~5.0 / Wan-turbo 1.0)
        when BACKEND changes, but ONLY if it is still on a known default -> a cfg you tuned is never clobbered.
        Wan-turbo is a CFG-distilled few-step LoRA: it REQUIRES cfg 1.0, so also default STEPS down to 6.
        T7: #cfg/#steps are blurred BEFORE the assignment -- if the field has focus, Input's own focus/
        validation handling can otherwise re-assert the OLD value over the one we just set here."""
        bk = self.v("backend") or "ltx"
        want = {"ltx": "3.0", "wan": "5.0", "wan-turbo": "1.0"}.get(bk, "3.0")
        try:
            cfg_in = self.query_one("#cfg", Input)
            if (cfg_in.value or "").strip() in ("", "1", "3", "5", "1.0", "3.0", "5.0"):
                if cfg_in.has_focus:
                    cfg_in.blur()
                cfg_in.value = want
        except Exception:
            pass
        if bk == "wan-turbo":                      # the distill is built for few steps; nudge off a heavy default
            try:
                steps_w = self.query_one("#steps")
                if str(getattr(steps_w, "value", "")).strip() in ("", "20", "30", "40", "50"):
                    if steps_w.has_focus:
                        steps_w.blur()
                    steps_w.value = "6"
            except Exception:
                pass
        self.call_after_refresh(self.update_est)   # land after focus/value settle, so the ETA reflects the new default

    def on_switch_changed(self, event):
        self.update_est()

    # ---------- plain-English narration of what's happening ----------
    def _phase(self, job, paused):
        if paused:
            return "[b]‖ Paused.[/b]  Nothing is running — press RESUME to pick up exactly where it stopped."
        # spinner leads EVERY live phase, so even the silent ones (load/decode/save) visibly animate
        glyph = self.SPIN[self._beat % len(self.SPIN)]
        return f"{glyph}  {self._phase_text(job)}"

    def _phase_text(self, job):
        nseg, nstep = job.nseg, job.nstep
        near_end = bool(nstep) and job.step >= nstep - 2
        if getattr(job, "status", "") == "suspending":
            return f"Suspending — finishing shot {job.seg} of {nseg}, then saving its place and freeing the GPU…"
        if job.kind == "enhance":
            return "Polishing the finished video — smoothing motion, upscaling, cleaning up faces…"
        phase = getattr(job, "phase", "") or ""
        if phase == "importing":
            return "Starting up — initializing PyTorch and the video model…"
        if phase == "loading":
            return f"Loading the model into memory — {getattr(job, 'load_msg', 'reading the checkpoint')} (first load ~2 min)…"
        if phase == "offload":
            return "Wiring up CPU/GPU memory offload (8 GB mode)…"
        if phase == "loading_vlm":
            return "Loading the AI director…"
        if phase == "warmup":
            return "Warming up the GPU — the first step is the slowest…"
        if phase == "redirecting":
            return f"The director is studying the last frame to plan shot {job.seg + 1} of {nseg}…"
        if phase == "decoding":
            return f"Finishing shot {job.seg} of {nseg} — turning the model's output into frames…"
        if phase == "saving":
            return "Saving and encoding the final video…"
        if phase == "generating":
            return f"Painting shot {job.seg} of {nseg} — step {job.step} of {nstep}." + ("  Almost done with this shot." if near_end else "")
        # fallback when no phase markers have arrived yet
        if nstep and not getattr(job, "saw_step", False):
            if job.seg <= 1:
                return "Warming up — loading the model into memory (first run ~2 min)…"
            return f"Starting shot {job.seg} of {nseg}…"
        if job.step > 0:
            return f"Painting shot {job.seg} of {nseg} — step {job.step} of {nstep}." + ("  Almost done with this shot." if near_end else "")
        return "Working…"

    # ---------- T25: unified smart progress (expected-wall-time model) ----------
    # Ordered meta-phases the bar sweeps through, in order. Each raw engine phase maps to one.
    _SMART_PHASES = ("load", "warm", "gen", "decode", "save")
    _PHASE_MAP = {"importing": "load", "loading": "load", "offload": "load", "loading_vlm": "load",
                  "warmup": "warm", "redirecting": "gen", "generating": "gen",
                  "decoding": "decode", "saving": "save"}

    def _run_budget(self, job):
        """Estimated wall-seconds for THIS run's config, split per meta-phase for the WHOLE run.
        Mirrors update_est's per-phase terms (LOAD/WARM/gen-per-step/DECODE) so the bar can be
        allocated by expected duration. Reads job.params (fixed at build) -> no form dependency.
        Fully guarded; returns a positive-total dict on any failure so callers never divide by 0."""
        try:
            p = job.params or {}
            backend = (p.get("backend") or "ltx")
            W, H = RES.get(res_key(p.get("res")), (704, 480))
            steps = int(float(p.get("steps") or job.nstep or 20))
            if backend == "wan-turbo":
                steps = min(steps, 8)
            nseg = int(job.nseg or int(p.get("nseg") or 1) or 1)
            seg_frames = int(float(p.get("seg_frames") or 0) or 0)
            px = (W * H) / (512 * 320)
            director = (p.get("mode") or job.kind) == "director"
            if backend in ("wan", "wan-turbo"):
                LOAD, COEF, WARM, DECODE, SEAM, SEG_REF = 40, 4.8, 220, 38, 90, 29
            else:
                LOAD, COEF, WARM, DECODE, SEAM, SEG_REF = 150, 1.5, 70, 20, 90, 49
            ff = (seg_frames / SEG_REF) if seg_frames else 1.0
            nseam = max(0, nseg - 1)
            _steady = (p.get("steadiness") or "hold")
            if director and _steady == "evolve":   # mirror the engine's blank/echo-directive downgrade
                _dv = (p.get("directive") or "").strip()
                if not _dv or _dv == (p.get("prompt") or "").strip():
                    _steady = "hold"
            if director and _steady != "evolve":
                nseam = -(-nseam // 3)
            gen = steps * COEF * px * ff * nseg + (SEAM * nseam if director else 0)
            decode = DECODE * ff * nseg
            b = {"load": float(LOAD), "warm": float(WARM) * nseg, "gen": float(gen),
                 "decode": float(decode), "save": max(6.0, 0.15 * decode)}
        except Exception:
            b = {"load": 60.0, "warm": 30.0, "gen": 120.0, "decode": 30.0, "save": 8.0}
        for k in self._SMART_PHASES:
            if not b.get(k) or b[k] <= 0:
                b[k] = 1.0
        return b

    def _live_frames(self, job):
        """Every frame the ACTIVE run has rendered SO FAR, oldest->newest. Source of truth is the
        checkpoint's frames/ dir (write_checkpoint persists ALL accumulated frames atomically after
        every completed shot); falls back to the final frames_dir (save phase), then to the single
        live-preview PNG. Single-shot runs have no checkpoint -> frames only exist after save."""
        ck = getattr(job, "ckpt_dir", None) or ""
        for d in ((os.path.join(ck, "frames") if ck else ""), (job.params or {}).get("frames_dir") or ""):
            if not d:
                continue
            dabs = d if os.path.isabs(d) else os.path.join(REPO, d)
            try:      # 4-digit pattern skips write_checkpoint's atomic *.png.tmp files
                fs = sorted(glob.glob(os.path.join(dabs, "[0-9][0-9][0-9][0-9].png")))
            except Exception:
                fs = []
            if fs:
                return fs
        pv = getattr(job, "preview", None)
        return [pv] if pv and os.path.exists(pv) else []

    def _time_left(self, job):
        """Seconds left on the ACTIVE run — the ONE source of truth for the livebar ETA, the PACE
        'left' cell, and the queue ETA (audit 2026-07-06). Measured-first: once a shot has completed,
        use THIS run's mean shot time including the in-progress shot's remainder — the old
        mean*(nseg-seg) formula read "~0s" for the whole final shot. Before any shot completes, fall
        back to the T25 smart budget (charges warmup/decode, which the old step-rate ETA ignored)."""
        try:
            ssecs = getattr(job, "seg_secs", []) or []
            if ssecs:
                mean = sum(ssecs) / len(ssecs)
                seg, nseg = int(getattr(job, "seg", 0) or 0), int(getattr(job, "nseg", 1) or 1)
                left = mean * max(0, nseg - seg)
                if seg >= 1:                       # the shot in flight: its remainder, not zero
                    t0 = getattr(job, "seg_started", None)
                    left += min(mean, max(0.0, mean - (time.time() - t0))) if t0 else mean
                return max(0, int(left))
            budget = self._run_budget(job)
            total = sum(budget.values())
            sp = self._smart_pct(job)
            if total > 0 and sp is not None:
                return max(0, int(total * (100.0 - float(sp)) / 100.0))
        except Exception:
            pass
        return None

    def _queue_eta(self):
        """Estimated wall-seconds until the QUEUE is empty: time left on the active run + the full budget
        of every queued job. Recomputed each tick, so it tracks jobs finishing / being added / progressing."""
        try:
            m = self.mgr
            total = 0.0
            act = m.active()
            if act is not None:
                tl = self._time_left(act)          # same estimator the LIVE tab shows (consistency)
                total += tl if tl is not None else \
                    max(0.0, sum(self._run_budget(act).values()) - max(0.0, act.elapsed()))
            for j in m.queued():
                total += sum(self._run_budget(j).values())
            return total
        except Exception:
            return 0.0

    def _smart_pct(self, job):
        """Expected-wall-time overall %: (est-time of completed meta-phases + current-phase
        fraction * current-phase est) / total-est. WITHIN gen it advances by (step+intra)/nstep
        using the measured step rate; WITHIN decode/save it creeps by elapsed/est. Capped < 100
        until the run is actually done (status done/archived) -> then 100. tick() also latches the
        running max so display is monotonic. Returns int in [0, 100]."""
        try:
            status = getattr(job, "status", "") or ""
            if status in ("done", "archived", "finished", "complete"):
                return 100
            budget = self._run_budget(job)
            total = sum(budget[k] for k in self._SMART_PHASES) or 1.0
            raw_phase = getattr(job, "phase", "") or ""
            meta = self._PHASE_MAP.get(raw_phase, "")
            # No phase marker yet -> fall back to load (pure startup) or gen (steps seen).
            if not meta:
                meta = "gen" if (job.nstep and getattr(job, "saw_step", False)) else "load"
            # cumulative est of the meta-phases strictly BEFORE the current one
            before = 0.0
            for k in self._SMART_PHASES:
                if k == meta:
                    break
                before += budget[k]
            frac = max(0.0, min(0.98, self._phase_fraction(job, meta, budget)))
            pct = 100.0 * (before + frac * budget[meta]) / total
            return int(max(0.0, min(99.0, pct)))   # never reach/exceed 100 until truly complete
        except Exception:
            try:
                return int(job.pct())
            except Exception:
                return 0

    def _avg_step_seconds(self, job):
        """seconds/step from the EXISTING measured step rate (inverse of the PACE 'rate steps/s':
        timed_steps / elapsed_since_first_step). Falls back to the config estimate (gen budget /
        total steps) when no step has been timed yet. Returns a positive float, or None."""
        try:
            first_ts = getattr(job, "first_step_ts", None)
            nstep = job.nstep or 0
            if first_ts and (job.step or 0) > 0 and nstep:
                base = getattr(job, "first_step_seg", 1) or 1
                timed = (job.seg - base) * nstep + job.step
                if timed > 0:
                    return (time.time() - first_ts) / timed
            # fallback: gen-phase estimate / total steps
            if nstep:
                b = self._run_budget(job)
                nseg = max(job.nseg or 1, job.seg or 1)
                tot = nseg * nstep
                if tot > 0 and b.get("gen"):
                    return b["gen"] / tot
        except Exception:
            return None
        return None

    def _phase_fraction(self, job, meta, budget):
        """Fraction in [0, ~0.98] of the current meta-phase completed."""
        now = time.time()
        if meta == "gen":
            nstep = job.nstep or 0
            nseg = max(job.nseg or 1, job.seg or 1)
            if not nstep or not nseg:
                return 0.0
            steps_done = (max(0, (job.seg or 1) - 1)) * nstep + (job.step or 0)
            steps_total = nseg * nstep
            # intra_step = clamp((now - last_step_ts) / avg_step_seconds, 0, 1), where
            # avg_step_seconds = 1/rate (the SAME PACE "rate steps/s" calc). last_step_ts is the
            # wall-clock the step counter last advanced (latched in tick as _smart_step_wall);
            # if the rate isn't known yet, intra stays 0.
            intra = 0.0
            try:
                avg = self._avg_step_seconds(job)               # seconds/step, or None
                last = getattr(self, "_smart_step_wall", None)
                if avg and avg > 0 and last:
                    intra = (now - last) / avg
            except Exception:
                intra = 0.0
            intra = max(0.0, min(1.0, intra))
            return min(0.98, (steps_done + intra) / max(1, steps_total))
        # load / warm / decode / save: no sub-steps -> creep by elapsed / est
        t0 = getattr(job, "phase_started", None)
        if not t0:
            return 0.0
        est = budget.get(meta, 1.0) or 1.0
        return max(0.0, min(0.98, (now - t0) / est))

    def _sync_table(self, table_id, rows, sig_attr):
        """Rebuild a DataTable only when its content changed -> the cursor stays put (no rubber-band)
        and no wasted work every tick. rows = list of tuples (key, *cells)."""
        sig = tuple(rows)
        if sig == getattr(self, sig_attr):
            return
        t = self.query_one(table_id, DataTable)
        sel = None
        try:
            sel = t.coordinate_to_cell_key(t.cursor_coordinate).row_key
        except Exception:
            pass
        t.clear()
        for r in rows:
            t.add_row(*r[1:], key=r[0])
        if sel is not None:
            try:
                t.move_cursor(row=t.get_row_index(sel), animate=False)
            except Exception:
                pass
        setattr(self, sig_attr, sig)

    def _queue_card(self, job, status, status_col, w):
        """One queued/suspended job as a decorated box-art card (rich markup, fixed 7-line height
        incl a trailing gap so DataTable rows stay uniform). Purely cosmetic — selection + every
        queue action still key off the ROW (job id), never this card text."""
        p = job.params or {}
        inner = max(20, w - 4)
        def trunc(s, n):
            s = str(s)
            return s if len(s) <= n else (s[:max(0, n - 1)] + "…")
        def pad(s, n):
            s = trunc(s, n)
            return s + " " * (n - len(s))
        glyph, label = _run_kind(job)
        badge = "%s %s" % (glyph, label.upper())
        bcol = (SPAL["warning"] if (label == "replicate" or label.startswith("pair") or label == "blind pair")
                else SPAL["accent2"] if label == "enhance" else SPAL["accent"])
        gap = max(1, inner - len(badge) - len(status))
        header = "[%s]%s[/%s]%s[%s]%s[/%s]" % (bcol, badge, bcol, " " * gap, status_col, status, status_col)
        title = _demark(job.title or p.get("prompt", "") or job.id)
        parms = "%s · %s×%s · %sst · cfg%s · %ss · seed %s" % (
            p.get("backend", "ltx"), p.get("width", "?"), p.get("height", "?"),
            p.get("steps", "?"), p.get("cfg", "?"), p.get("seconds", "?"),
            trunc(p.get("seed", "") or "rnd", 11))
        B = SPAL["primary"]
        top = "[%s]╭%s╮[/%s]" % (B, "─" * (w - 2), B)
        bot = "[%s]╰%s╯[/%s]" % (B, "─" * (w - 2), B)
        T, M, S = SPAL["title"], SPAL["muted"], SPAL["soft"]
        body = [
            "[%s]│[/%s] %s [%s]│[/%s]" % (B, B, header, B, B),
            "[%s]│[/%s] [%s]%s[/%s] [%s]│[/%s]" % (B, B, T, pad(title, inner), T, B, B),
            "[%s]│[/%s] [%s]%s[/%s] [%s]│[/%s]" % (B, B, M, pad(job.id, inner), M, B, B),
            "[%s]│[/%s] [%s]%s[/%s] [%s]│[/%s]" % (B, B, S, pad(parms, inner), S, B, B),
        ]
        return "\n".join([top] + body + [bot, ""])

    def _sync_queue_cards(self):
        """QUEUE rendered as decorated cards — rebuilt only when content changes (cursor preserved by
        key). Cosmetic redesign of the old 4-column table; row keys stay job ids, so _selected + every
        queue action + on_data_table_row_highlighted keep working unchanged."""
        m = self.mgr
        items = [(j, "QUEUED · #%d" % (i + 1), SPAL["accent"]) for i, j in enumerate(m.queued())]
        items += [(j, "SUSPENDED · shot %s/%s" % (j.seg, j.nseg), SPAL["warning"]) for j in m.suspended()]
        t = self.query_one("#qtable", DataTable)
        try:
            w = int(t.content_size.width) - 4      # leave room for the DataTable cell padding + scrollbar
        except Exception:
            w = 0
        w = max(48, min(96, w)) if (w and w > 0) else 72
        if not items:                        # empty state: one dim hint card; all actions no-op on its key
            if getattr(self, "_qsig", None) == ("empty", w):
                return
            t.clear()
            t.add_row(Text.from_markup(
                "[dim]≡ queue empty — fill the NEW RUN form and ▶ QUEUE RUN (^⏎)[/dim]"),
                height=3, key="__empty__")
            self._qsig = ("empty", w)
            return
        sig = (w,) + tuple((j.id, _run_kind(j), st, str(j.params)) for (j, st, _c) in items)
        if sig == getattr(self, "_qsig", None):
            return
        sel = None
        try:
            sel = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        except Exception:
            pass
        t.clear()
        for (j, st, scol) in items:
            t.add_row(Text.from_markup(self._queue_card(j, st, scol, w)), height=7, key=j.id)
        if sel is not None:
            try:
                t.move_cursor(row=t.get_row_index(sel), animate=False)
            except Exception:
                pass
        self._qsig = sig

    # ---------- live polling ----------
    def _meter(self):
        """Compact whole-studio status for the top-right corner — render / queue / director-on-CPU at
        a glance. Derived ONLY from our own job + daemon state (never nvidia-smi: it crashes this WSL
        GPU passthrough). The verbose twin is the bottom #status bar."""
        m, c = self.mgr, self.consult
        nq = len(m.queued())
        on_cpu = c.alive() and getattr(c, "cpu_mode", False)
        act = m.active()
        blink = (self._beat % 2 == 0)

        def led(on, label, key, pulse=False):
            """One motherboard LED cell: ▮ lit (theme color; `pulse` alternates bright/dim on the
            tick beat) / ▯ unlit-dim. Several can be lit at once (e.g. REN + QUE)."""
            if not on:
                return "[dim]▯%s[/dim]" % label
            col = SPAL.get(key) or "#34d977"
            if pulse and not blink:
                col = SPAL.get("secondary") or col
            return "[%s]▮%s[/%s]" % (col, label, col)

        return "  ".join([
            led(act is None and not c.alive(), "IDL", "primary"),
            led(act is not None and not m.paused, "REN", "success", pulse=True),
            led(nq > 0, "QUE", "accent"),
            led(act is not None and m.paused, "PAU", "warning"),
            led(bool(m.suspended()), "SUS", "warning"),
            led(c.alive(), "DIR", "warning") + ("[dim]·cpu[/dim]" if on_cpu else ""),
            led(bool(getattr(self, "_stall_note", "")), "STL", "error", pulse=True),
        ])

    def _alerts(self):
        """Event sounds + stall-sentry, driven PURELY by observed studio state (never the run's own
        finish code). run_done fires iff a NEW row appears in the ARCHIVE with status 'done'. run_stall
        fires if the ACTIVE run shows no observable progress (phase/seg/step, or a fresh preview) for
        STALL_SECS. Sets self._stall_note (a status-bar marker, shown even when sound is off).
        Best-effort — never raises into the tick loop."""
        m = self.mgr
        self._stall_note = ""
        # --- run_done: a NEW archive row. Baseline on the first pass so existing history is silent. ---
        try:
            arch = {j.id: j.status for j in m.archived()}
            seen = getattr(self, "_arch_seen", None)
            self._arch_seen = arch
            if seen is not None:
                if sounds is not None and any(jid not in seen and st == "done" for jid, st in arch.items()):
                    sounds.play("run_done", REPO)      # one chime even if several rows land together
        except Exception:
            pass
        # --- run_start + queue_empty: observed transitions, same pattern (silent unless a WAV exists) ---
        try:
            act = m.active()
            cur_id = getattr(act, "id", None)
            prev_id = getattr(self, "_start_seen", "__boot__")
            self._start_seen = cur_id
            if sounds is not None and cur_id and prev_id != "__boot__" and cur_id != prev_id:
                sounds.play("run_start", REPO)
            busy = bool(cur_id or m.queued())
            prev_busy = getattr(self, "_busy_seen", None)
            self._busy_seen = busy
            if sounds is not None and prev_busy and not busy:
                sounds.play("queue_empty", REPO)       # the whole batch just finished
        except Exception:
            pass
        # --- stall-sentry: active run wedged (no phase/seg/step change AND no fresh preview write).
        #     stall_action="suspend" (default) ESCALATES so the queue unblocks: graceful suspend at
        #     the threshold (SIGUSR1 -> checkpoint at the next shot boundary; no-op for single-shot),
        #     then hard_interrupt() after a grace window (ckpt kept -> lands resumable). "alert" =
        #     banner+sound only. Never escalates a PAUSED run or the load/download phases. ---
        try:
            job = m.active()
            if job is None or m.paused:
                self._stall_state = None
                return
            sig = (job.id, job.phase, job.seg, job.step, getattr(job, "load_step", 0))
            try:
                pv = getattr(job, "preview", None)
                pmt = os.path.getmtime(pv) if pv and os.path.exists(pv) else 0.0
            except Exception:
                pmt = 0.0
            now = time.monotonic()
            prev = getattr(self, "_stall_state", None)
            if prev is None or prev["sig"] != sig or prev["pmt"] != pmt:
                self._stall_state = {"sig": sig, "pmt": pmt, "since": now, "fired": False,
                                     "susp": False, "killed": False}
                return
            # PER-PHASE fuses. Only `generating` has step-granular markers, so only IT gets the
            # tight fuse. warmup (Wan averages 220s/shot with ZERO markers on this box's own refit)
            # and decode/save (a real multi-hour decode exists in experiments.jsonl) share the long
            # fuse, so an overnight batch never murders a slow-but-alive run.
            slow = job.phase in ("warmup", "decoding", "saving")
            fire_at = (getattr(self, "_stall_decode_secs", 600.0) if slow
                       else getattr(self, "_stall_secs", 240.0))
            grace = (getattr(self, "_stall_decode_secs", 600.0) if slow
                     else getattr(self, "_stall_grace", 180.0))
            idle = now - prev["since"]
            act_suspend = getattr(self, "_stall_action", "suspend") == "suspend"
            # CATCH-ALL: no phase may sit at zero progress for stall_max_secs (default 2h) — a
            # wedge in loading/importing previously blocked the queue ALL NIGHT with banner-only.
            if act_suspend and idle >= getattr(self, "_stall_max_secs", 7200.0) and not prev["killed"]:
                prev["killed"] = True
                m.hard_interrupt()
                self._stall_note = "    " + tmark(
                    "error", "!! DEAD %dm — killed (catch-all), queue continues" % int(idle // 60))
                return
            if idle < fire_at:
                return
            mins = int(idle // 60)
            if not prev["fired"]:
                prev["fired"] = True
                if sounds is not None:
                    sounds.play("run_stall", REPO)
            escalate = act_suspend and job.phase in ("warmup", "generating", "decoding", "saving")
            if not escalate:      # "alert" mode, or a load/download phase (legitimately silent-slow)
                self._stall_note = "    " + tmark("error", "!! STALL? no progress %dm" % mins)
                return
            # DELAYED graceful suspend (fire + grace/2): SIGUSR1 is irreversible once delivered —
            # the engine exits "suspended" at the next shot boundary even if the stall RECOVERS.
            # Latching later means a transient 3am hiccup no longer silently truncates a run.
            if idle >= fire_at + grace * 0.5 and not prev["susp"]:
                prev["susp"] = True
                m.suspend()       # graceful: checkpoint at the next boundary (no-op single-shot)
            if idle >= fire_at + grace and not prev["killed"]:
                prev["killed"] = True
                m.hard_interrupt()   # ckpt-preserving kill -> suspended (resumable) / interrupted; queue moves on
                self._stall_note = "    " + tmark("error", "!! STALLED %dm — killed, queue continues" % mins)
                return
            self._stall_note = "    " + tmark(
                "error", "!! STALL %dm — suspending… (kill in %ds)"
                % (mins, max(0, int(fire_at + grace - idle))))
        except Exception:
            pass

    def _absorb_standby_gap(self, job, gap):
        """Modern Standby froze the VM for `gap` wall-seconds (this platform enters standby when
        the display goes dark, ES_SYSTEM_REQUIRED or not — Kernel-Power confirmed, twice). Shift
        every wall-clock baseline forward so pace / this-shot / time-left math self-heals at wake
        instead of showing 8h elapsed / '~0s left' garbage, and tally job.slept for the display."""
        try:
            for attr in ("seg_started", "phase_started", "first_step_ts"):
                v = getattr(job, attr, None)
                if v:
                    setattr(job, attr, v + gap)
            job.slept = getattr(job, "slept", 0.0) + gap
            try:
                self._smart_step_wall += gap
            except Exception:
                pass
        except Exception:
            pass

    def tick(self):
        if not self.is_running:     # timer can fire once more during shutdown/teardown -> widgets gone
            return
        m = self.mgr
        self._beat += 1
        _wall = time.time()
        _gap = _wall - getattr(self, "_last_tick_wall", _wall)
        self._last_tick_wall = _wall
        if _gap > 120:              # ticks run every 0.5s; a 2min+ hole = the VM was frozen (standby)
            _aj = m.active()
            if _aj is not None:
                self._absorb_standby_gap(_aj, _gap)
        # GPU STATUS — from our OWN job state ONLY, never nvidia-smi. REPRODUCED: nvidia-smi polling
        # in this WSL2 (RTX 5070 Blackwell + driver 591.74) destabilizes the dxg passthrough and
        # restarts the WHOLE VM after ~30 calls, EVEN WITH NO CUDA RUNNING. So there is no live VRAM%
        # meter — we show working/idle from whether a run or the consult daemon is up.
        self._gpu_str = (tmark("warning", "GPU ● working") if self._cuda_busy()
                         else tmark("primary", "GPU ○ idle"))
        # CONSULT daemon is loaded only while its screen is open (it warms/frees itself there).
        # Here we just reclaim the GPU from it the moment a run starts.
        if m.active() is not None and self.consult.alive() and not self.consult.cpu_mode:
            self.consult.kill()        # reclaim the GPU — but NOT a CPU-bound consult (it isn't on the GPU)
        q, a, d, s = m.counts()
        self._alerts()      # run-done sound (archive-row-added) + stall-sentry; sets self._stall_note
        st = "PAUSED" if m.paused else ("RUNNING" if a else "idle")
        _eta = self._queue_eta()
        _etastr = ("  " + tmark("accent", "(~%s to empty)" % fmt(_eta))) if _eta > 1 else ""
        self.query_one("#status", Static).update(
            f"  ▌ QUEUED {q}{_etastr}    ▶ {st}    ✓ DONE {d}    ▽ SUSP {s}     │     {self._gpu_str}{self._stall_note}")
        self.query_one("#statusmeter", Static).update(self._meter())
        # queue + archive tables — rebuilt only when content changes (cursor stays put; no rubber-band)
        _dt = lambda ts: time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "—"
        def _atitle(j):
            pre = ("★ " if (j.params or {}).get("favorite") else "") + \
                  ("▲ " if (j.kind == "enhance" and not (j.title or "").startswith("▲")) else "")
            return (pre + (j.title or ""))[:30]
        arows = [(j.id, j.id, _atitle(j), j.status, _dt(j.started), _dt(j.finished), fmt(j.elapsed()), _vidlen(j)) for j in m.archived()]
        if not arows:                                # empty state hint row (every action no-ops on its key)
            arows = [("__empty__", "", Text.from_markup("[dim]no finished runs yet — outputs land here[/dim]"),
                      "", "", "", "", "")]
        self._sync_queue_cards()                     # QUEUE as decorated cards (replaces the old 4-column table)
        self._sync_table("#atable", arows, "_asig")
        # live
        job = m.active()
        hdr = self.query_one("#livehdr", Static)
        over = self.query_one("#overbar", ProgressBar)
        live = self.query_one("#livelog", RichLog)
        for bid in ("pausebtn", "resumebtn", "suspendbtn", "cancelbtn"):
            try:
                self.query_one(f"#{bid}", Button).disabled = job is None
            except Exception:
                pass
        if job is None:
            hdr.update("[dim]no active run — queue one in NEW RUN[/dim]")
            self.query_one("#livephase", Static).update("[dim]Nothing is generating right now.[/dim]")
            self.query_one("#director", Static).update("")
            self.query_one("#progtext", Static).update("")
            self.query_one("#livebar", Static).update("")
            self.query_one("#preview", Static).update(Text())
            for sid in ("pace_rate", "pace_frames", "pace_shot", "pace_eta",
                        "steer_mode", "steer_directive", "steer_anchors", "ph_timeline"):
                self.query_one(f"#{sid}", Static).update("")
            over.update(total=100, progress=0)
            if self._live_id is not None:
                self._live_id = None
                self._preview_id = None
            self._smart_step_wall = self._smart_step_key = None   # T25: reset smart-bar latches
            self._smart_max, self._smart_max_id = 0, None
            return
        tag = "‖ PAUSED" if m.paused else "▶ RUNNING"
        kglyph, klabel = _run_kind(job)
        _ttl = _demark((job.title or job.params.get('prompt', ''))[:48])
        hdr.update(f"[b]{tag}[/b]   [#9dffce]{kglyph} {klabel}[/#9dffce]   {_ttl}")
        loading = job.is_loading() if hasattr(job, "is_loading") else False
        # The big load-bar takeover is for the INITIAL load only: is_loading() includes "warmup", so
        # every later shot's warmup used to flip the header back to "5/5 · warming up" + a dead
        # "loading..." ETA twenty minutes into a render. Later warmups ride the smart bar instead.
        initial_load = loading and int(getattr(job, "seg", 0) or 0) <= 1 and not getattr(job, "saw_step", False)
        # T25: latch the wall-clock the step counter last advanced (for intra-step interpolation),
        # keyed to (job, seg, step) so it only updates on a real step change — never every tick.
        try:
            skey = (job.id, getattr(job, "seg", 0), getattr(job, "step", 0))
            if skey != self._smart_step_key:
                self._smart_step_key = skey
                self._smart_step_wall = time.time()
        except Exception:
            pass
        if initial_load:
            over.update(total=max(1, getattr(job, "load_total", 0) or 1),
                        progress=getattr(job, "load_step", 0))
            self.query_one("#progtext", Static).update(
                f"{getattr(job, 'load_step', 0)}/{getattr(job, 'load_total', 0)}   ·   {getattr(job, 'load_msg', '')}")
        else:
            # T25: expected-wall-time overall %, latched monotonic per run so it never steps back.
            try:
                sp = self._smart_pct(job)
                if self._smart_max_id != job.id:
                    self._smart_max, self._smart_max_id = 0, job.id
                sp = self._smart_max = max(self._smart_max, sp)
            except Exception:
                sp = job.pct()
            over.update(total=100, progress=sp)
            # keep the "shot X of N · step Y of Z" text; overall % now reflects wall-time progress
            _phase_hint = {"decoding": "  ·  decoding…", "saving": "  ·  saving…"}.get(
                getattr(job, "phase", ""), "")
            self.query_one("#progtext", Static).update(
                f"shot {job.seg} of {job.nseg}   ·   step {job.step} of {job.nstep}   ·   {sp}% overall{_phase_hint}")
        self.query_one("#livephase", Static).update(self._phase(job, m.paused))
        now_painting = job.director or job.params.get("prompt", "")
        self.query_one("#director", Static).update(
            ("[dim]this shot →[/dim] " + now_painting) if now_painting
            else ("[dim]—[/dim]" if job.kind != "director" else ""))
        # live frame preview + director's notes (both reset on job change)
        pv = self.query_one("#preview", Static)
        notes = self.query_one("#dirnotes", RichLog)
        if job.id != self._preview_id:
            self._preview_id, self._preview_mtime, self._notes_n = job.id, 0.0, 0
            pv.update("[dim]waiting for first frame…[/dim]")   # T12: placeholder until the first preview lands
            notes.clear()
            if job.kind != "director":     # T12: non-director runs never populate director's-notes -- say so
                notes.write("[dim]This run uses no director steering.[/dim]")
        ppath = getattr(job, "preview", None)
        if ppath and os.path.exists(ppath):
            mt = os.path.getmtime(ppath)
            w = self.size.width
            cols = 96 if w >= 150 else (72 if w >= 120 else 48)   # bigger preview on wide terminals
            if mt != self._preview_mtime or cols != self._preview_cols or preview_art.PREVIEW_MODE != self._preview_mode:
                self._preview_mtime, self._preview_cols, self._preview_mode = mt, cols, preview_art.PREVIEW_MODE
                pv.styles.width = cols + 2
                pv.update(render_preview(ppath, cols=cols))
        plans = getattr(job, "plans", None) or []
        for entry in plans[self._notes_n:]:
            seg, plan = int(entry[0]), entry[1]
            prompt = entry[2] if len(entry) > 2 else ""
            notes.write(f"[#6dffab]shot {seg + 1}[/#6dffab] — [#9dffce]plan:[/#9dffce] {plan or '…'}"
                        + (f"  [#ffcf5c]→[/#ffcf5c] {prompt}" if prompt else ""))
            cost = _dir_cost_line(job, seg)
            if cost:
                notes.write(f"   {cost}")
            if self._dir_raw:
                r = _director_raw(job, seg)
                if r and r.get("raw"):
                    notes.write(f"   [dim]raw ▾[/dim] {r['raw'].strip()[:1200]}")
        self._notes_n = len(plans)
        el = job.elapsed()
        first_ts = getattr(job, "first_step_ts", None)   # the PACE rate line below still needs this
        # UNIFIED remaining-time estimate (audit 2026-07-06): _time_left() is the ONE source of truth
        # for the livebar ETA, the PACE "left" cell, and the queue ETA. The old trio disagreed: the
        # step-rate ETA ignored warmup/decode, PACE's mean*(nseg-seg) read "~0s" for the ENTIRE final
        # shot, and "loading..." suppressed the estimate during every later shot's warmup.
        _left = self._time_left(job)
        if _left is not None:
            eta = f"~{fmt(_left)} left"
        elif loading:
            eta = "loading..."
        else:
            eta = "measuring..."
        p = job.params
        _slept = getattr(job, "slept", 0.0)
        _elstr = (f"t+{fmt(max(0, el - int(_slept)))} (+{fmt(int(_slept))} standby)" if _slept > 120
                  else f"t+{fmt(el)} elapsed")
        self.query_one("#livebar", Static).update(
            f"  {_elstr}   ·   {eta}      {p.get('res', '')}   {p.get('steps', '')} steps   "
            f"seed {p.get('seed', '')}      → {os.path.basename(job.out or '')}")
        # ---- PACE strip ----
        nstp = job.nstep or 0
        if first_ts and job.step > 0 and nstp:
            base = getattr(job, "first_step_seg", 1) or 1
            sps = ((job.seg - base) * nstp + job.step) / max(0.001, time.time() - first_ts)
            self.query_one("#pace_rate", Static).update(f"[dim]rate[/dim] {sps:.2f} steps/s")
        else:
            self.query_one("#pace_rate", Static).update("[dim]rate[/dim] —")
        ssecs = getattr(job, "seg_secs", []) or []
        seg_t0 = getattr(job, "seg_started", None)
        if ssecs:
            mean = sum(ssecs) / len(ssecs)
            self.query_one("#pace_shot", Static).update(f"[dim]per shot[/dim] {fmt(int(mean))} avg")
        elif seg_t0:                   # no completed shot yet -> show the current shot ticking (warmup too)
            self.query_one("#pace_shot", Static).update(f"[dim]this shot[/dim] {fmt(int(time.time() - seg_t0))}")
        else:
            self.query_one("#pace_shot", Static).update("[dim]per shot[/dim] ~measuring")
        self.query_one("#pace_eta", Static).update(f"[dim]left[/dim] {eta.removesuffix(' left')}")
        sf = int(p.get("seg_frames") or 0)
        tf = int(p.get("total_frames") or (sf * job.nseg if sf else 0))
        if sf and tf and nstp:
            ov = min(9, max(0, sf - 8))            # matches _plan()/director.py: later shots only ADD sf-ov
            eff = max(1, sf - ov)
            base = 0 if job.seg <= 1 else sf + (job.seg - 2) * eff
            cur = sf if job.seg <= 1 else eff
            fdone = min(tf, base + int(cur * job.step / nstp))
            self.query_one("#pace_frames", Static).update(f"[dim]frames[/dim] ~{fdone}/{tf}")
        else:
            self.query_one("#pace_frames", Static).update("[dim]frames[/dim] —")
        # ---- STEERING strip ----
        smode = (p.get("steadiness") or ("evolve" if p.get("directive") else "—")) if job.kind == "director" else job.kind
        self.query_one("#steer_mode", Static).update(f"[dim]mode[/dim] {smode}")
        if job.kind == "director":
            self.query_one("#steer_directive", Static).update(f"[dim]arc[/dim] {(p.get('directive') or '—')[:48]}")
        else:                          # no arc on single/chained -> show what it's actually rendering
            self.query_one("#steer_directive", Static).update(f"[dim]subject[/dim] {(p.get('prompt') or '—')[:48]}")
        self.query_one("#steer_anchors", Static).update(f"[dim]anchors[/dim] {(p.get('anchors') or '—')[:40]}")
        # ---- PHASE timeline strip ----
        psecs = getattr(job, "phase_secs", {}) or {}
        cur, cur_t0 = getattr(job, "phase", ""), getattr(job, "phase_started", None)
        cells = []
        for ph, lbl in (("loading", "load"), ("warmup", "warm"), ("generating", "gen"),
                        ("decoding", "decode"), ("saving", "save")):
            t = psecs.get(ph, 0)
            if ph == cur and cur_t0:
                cells.append(f"[#9dffce]{lbl} {fmt(int(t + time.time() - cur_t0))}[/#9dffce]")
            elif t > 0:
                cells.append(f"[dim]{lbl} {fmt(int(t))}[/dim]")
            else:
                cells.append(f"[dim]{lbl} ·[/dim]")
        self.query_one("#ph_timeline", Static).update("  →  ".join(cells))
        if job.id != self._live_id:
            self._live_id, self._live_n, self._live_last = job.id, 0, None
            live.clear()
        tail = job.tail
        # tail is a 300-line ring: once saturated, len() stops growing while content rotates, so a
        # plain cursor freezes forever. Re-anchor on the last line we wrote when the ring has moved.
        if self._live_n >= len(tail) and tail and tail[-1] != getattr(self, "_live_last", None) and self._live_n > 0:
            try:
                self._live_n = len(tail) - tail[::-1].index(self._live_last)
            except (ValueError, TypeError):      # our anchor rotated out entirely -> repaint
                live.clear()
                self._live_n = 0
        new = tail[self._live_n:]
        for ln in new:
            live.write(ln)
        if new:
            self._live_last = new[-1]
        self._live_n = len(tail)

    # ---------- planning: cap per-segment memory, auto-chain for length ----------
    SAFE_PX = 20_000_000  # frames*W*H budget per segment that fits 8GB (704x480 -> ~59 frames)

    def _plan(self, over=None):
        """(W,H,fps,total_frames,seg_frames,nseg,chain) - length auto-chains; resolution is the hard cap.
        over: optional snapshot dict (blind-pair build) read instead of the live form via v()."""
        V = lambda wid: self.v(wid, over)
        W, H = RES[V("res")]
        wan = (V("backend") or "ltx") in ("wan", "wan-turbo")   # Wan/turbo render at native 16fps on a //4 grid
        if wan:
            # mirror WanBackend.dims(): Wan upscales the short side to >=480 (//32). Plan with the REAL
            # render dims or the SAFE_PX segment budget + estimate are computed ~2x too generous at 512x320.
            short = min(W, H)
            if short < 480:
                s = 480.0 / short
                W = max(32, round(W * s / 32) * 32)
                H = max(32, round(H * s / 32) * 32)
        fps = 16 if wan else max(1, int(float(V("fps"))))
        q = (lambda n: ((max(1, n) - 1) // 4) * 4 + 1) if wan else (lambda n: (max(1, n) // 8) * 8 + 1)
        r = round if wan else int          # match each backend's to_frames() rounding EXACTLY, or the
        total_frames = max(9, q(r(float(V("seconds")) * fps)))   # backend plans one more segment than
        safe = max(9, q(int(self.SAFE_PX / (W * H))))       # the studio -> phantom-seg suspend zombie
        if V("mode") == "director":
            seg_frames = min(q(r(float(V("seg")) * fps)), safe)
        else:
            seg_frames = min(total_frames, safe)
        seg_frames = max(9, seg_frames)
        chain = total_frames > seg_frames
        overlap = min(9, seg_frames - 8)                    # matches director.py
        nseg = (1 + -(-(total_frames - seg_frames) // max(1, seg_frames - overlap))) if chain else 1
        return W, H, fps, total_frames, seg_frames, nseg, chain

    def _unique_slug(self, slug):
        """Return a slug whose outputs/<slug>.mp4 + outputs/<slug>_frames collide with NOTHING —
        neither a file already on disk NOR the output path of any job already known to the manager
        (queued/running/finished). The old check only looked at on-disk files, so two runs queued in
        the SAME second (classic: a blind A/B pair, both with a blank NAME -> the same job_HHMMSS base)
        both passed — their renders had not written yet — and got the IDENTICAL out path, so variant B
        would clobber variant A's finished mp4 on disk (data loss). We now also reserve against every
        in-memory job's `out`, and fall through to a process-monotonic counter so uniqueness holds even
        if two builds land in the same second before either job is registered. This is a GENERAL fix
        (any two same-second jobs) and BLIND-SAFE: the disambiguator is a neutral timestamp/counter, it
        never encodes the A/B label or the varied value, so it leaks nothing about which variant is which."""
        # ALSO reserve every slug this process has already handed out: two build() calls in the
        # same second BEFORE either job registers used to collide (the counter fallback only ran
        # on a detected collision, so the documented guarantee was false — caught by tests/test_build).
        handed = getattr(self, "_handed_slugs", None)
        if handed is None:
            handed = self._handed_slugs = set()
        taken = set(handed)
        try:
            for j in self.mgr.jobs.values():
                o = (j.params or {}).get("out") or getattr(j, "out", None)
                if o:
                    taken.add(os.path.basename(o)[:-4] if o.endswith(".mp4") else os.path.basename(o))
        except Exception:
            pass

        def _free(s):
            return (s not in taken
                    and not os.path.exists(os.path.join(REPO, f"outputs/{s}.mp4"))
                    and not os.path.exists(os.path.join(REPO, f"outputs/{s}_frames")))

        if _free(slug):
            handed.add(slug)
            return slug
        base, n = slug, 2                                  # first collision -> -2, -3, ... then a counter
        while True:
            cand = f"{base}-{n}"
            if _free(cand):
                handed.add(cand)
                return cand
            n += 1
            if n > 999:                                    # pathological: guarantee termination + uniqueness
                cand = f"{base}-{next(_slug_counter)}"
                handed.add(cand)
                return cand

    def build(self, over=None):
        # over: optional field-id -> value SNAPSHOT (blind-pair build). When present, every form read
        # goes through it instead of the live widgets, so a variant is constructed without mutating the
        # shared form (no on_select_changed/_sync_cfg_default clobber of cfg/steps). over may also carry
        # the virtual key "_ltx_variant" ("distilled"/"none") -> appended as --ltx_variant on BOTH the
        # director.py and run_ltx.py paths so a checkpoint A/B is real at any clip length.
        V = lambda wid: self.v(wid, over)
        W, H, fps, total_frames, seg_frames, nseg, chain = self._plan(over)
        seg_sec = round(seg_frames / fps, 2)
        slug = slugify(V("name")) or ("job_" + time.strftime("%H%M%S"))
        slug = self._unique_slug(slug)
        out, fdir = f"outputs/{slug}.mp4", f"outputs/{slug}_frames"
        prompt = (V("prompt") or "").strip()
        neg = (V("n_prompt") or "").strip() or NEG
        director = V("mode") == "director"
        backend = V("backend") or "ltx"
        steps_s = V("steps")
        if backend == "wan-turbo":
            try:                                             # the distill runs few-step; clamp at the SOURCE so
                steps_s = str(min(int(float(steps_s)), 8))   # [[STEP]] totals / previews / ETA all stay honest
            except Exception:
                steps_s = "6"
        _sv = (V("seed") or "").strip()                  # blank/invalid SEED -> a concrete random seed (recorded)
        seed_s = _sv if (_sv and _sv.lstrip("-").isdigit()) else str(random.randint(1, 2**31 - 1))
        common = ["--steps", steps_s, "--cfg", V("cfg"), "--seed", seed_s,
                  "--width", str(W), "--height", str(H), "--fps", str(fps), "--out", out, "--frames_dir", fdir]
        # Q4/Q5 opt-in advanced levers. Every one DEFAULTS OFF -> when untouched, `common` is byte-identical
        # to before. Backend scope mirrors director.py's own no-op gating; we also skip appending on the
        # backends where the flag is a hard no-op so the recorded command stays honest.
        try:
            _cfg_val = float((V("cfg") or "1").strip() or "1")
        except Exception:
            _cfg_val = 1.0
        cfg_rescale = V("cfg_rescale") or "off"
        cfg_interval = V("cfg_interval") or "off"
        wan_ref_anchor = V("wan_ref_anchor") or "off"
        if cfg_rescale != "off" and _cfg_val > 1.0 and backend != "wan-turbo":
            common += ["--cfg_rescale", cfg_rescale]           # LTX + Wan; no-op on wan-turbo / cfg<=1
        if cfg_interval != "off" and _cfg_val > 1.0 and backend == "wan":
            common += ["--cfg_interval", cfg_interval]         # WAN-ONLY: LTX's batched CFG forward is
            # incompatible with per-step gating (attention shape crash); wan-turbo has no uncond pass.
        if wan_ref_anchor == "on" and backend in ("wan", "wan-turbo"):
            common += ["--wan_ref_anchor"]                     # Wan / Wan-turbo only
        # checkpoint variant (blind-pair only): the snapshot carries "_ltx_variant". distilled = the
        # 0.9.8-distilled 2B transformer, LTX-backend only. Both director.py AND run_ltx.py now accept
        # --ltx_variant, so a checkpoint A/B is real at ANY clip length (short single clip or chained).
        ltx_variant = (over or {}).get("_ltx_variant") or "none"
        want_distilled = (ltx_variant == "distilled" and backend == "ltx")
        img = (V("image") or "").strip()
        if chain or director or backend in ("wan", "wan-turbo"):     # chained gen; Wan always routes through director.py
            cmd = [FP_PY, "director.py", "--prompt", prompt, "--n_prompt", neg,
                   "--total", V("seconds"), "--seg", str(seg_sec),
                   "--cond_strength", (V("cond_strength") or "1.0"), "--backend", backend] + common
            if backend == "ltx":
                cmd += ["--latent_chain",                   # LTX-only (Wan has no latent-chain equivalent)
                        "--ltx_repo", LTX_REPO_DEFAULT]     # pin + record the 0.9.5 checkpoint (Q1 provenance)
                if want_distilled:
                    cmd += ["--ltx_variant", "distilled"]
            anchors = (V("anchors") or "").strip()
            if anchors:                                     # style leash applies to ANY chained run, not just director
                cmd += ["--anchors", anchors]
            if director:
                cmd += ["--vlm", "--directive", (V("directive") or prompt),
                        "--steadiness", V("steadiness")]
            if img:
                cmd += ["--image", img]
            title = ((V("directive") if director else "") or prompt)[:40]
        else:                                               # short enough to fit one clip
            cmd = ([FP_PY, "run_ltx.py", "--prompt", prompt, "--n_prompt", neg, "--seconds", V("seconds")]
                   + common + ["--ltx_repo", LTX_REPO_DEFAULT])   # single clips are always LTX; pin 0.9.5 (Q1)
            if want_distilled:                              # run_ltx.py mirrors director.py's --ltx_variant handling
                cmd += ["--ltx_variant", "distilled"]
            if img:
                cmd += ["--image", img]
            title = prompt[:40]
        kind = "director" if director else ("chained" if chain else "single")
        params = dict(
            mode=kind, prompt=prompt, steps=steps_s, cfg=V("cfg"), seed=seed_s,
            fps=str(fps), seconds=V("seconds"), seg_sec=(seg_sec if (chain or director) else ""),
            res=V("res"), width=W, height=H, nseg=nseg, seg_frames=seg_frames, total_frames=total_frames,
            directive=(V("directive") if director else ""), anchors=(V("anchors") or ""),
            steadiness=(V("steadiness") if director else ""),
            image=img, out=out, frames_dir=fdir, name=slug, n_prompt=neg, backend=backend,
            cond_strength=(V("cond_strength") or "1.0"),
            cfg_rescale=cfg_rescale, cfg_interval=cfg_interval, wan_ref_anchor=wan_ref_anchor,
        )
        if backend == "ltx":                             # checkpoint provenance (Q1): every ltx run records its repo
            params["ltx_repo"] = LTX_REPO_DEFAULT
        if want_distilled:                               # record the variant intent honestly on the job
            params["ltx_variant"] = "distilled"
        return title, kind, cmd, params

    def _apply_config(self, c):
        """Fill the NEW RUN form from a config dict keyed by FORM FIELD IDS. Shared by CONSULT + CLONE."""
        if not c:
            return
        for wid in ("name", "prompt", "directive", "anchors", "image", "seconds", "seg",
                    "steps", "cfg", "seed", "fps", "n_prompt", "backend", "cond_strength",
                    "cfg_rescale", "cfg_interval", "wan_ref_anchor"):
            if c.get(wid) is not None:
                try:
                    v = str(c[wid])
                    if wid == "backend":               # LLM configs arrive with stray case/spacing, and an
                        v = v.strip().lower().replace(" ", "-")   # invalid Select value is SILENTLY dropped
                        if v not in ("ltx", "wan", "wan-turbo"):
                            v = "wan-turbo" if "turbo" in v else ("wan" if "wan" in v else "ltx")
                    w = self.query_one(f"#{wid}")
                    if isinstance(w, TextArea):
                        w.text = v
                    else:
                        w.value = v
                except Exception:
                    pass
        try:
            self.query_one("#res").value = res_key(c.get("res"))
        except Exception:
            pass
        try:
            self.query_one("#mode").value = "director" if str(c.get("mode", "")).lower() == "director" else "single"
        except Exception:
            pass
        try:
            sv = str(c.get("steadiness", "")).lower()
            self.query_one("#steadiness").value = sv if sv in ("hold", "balanced", "evolve") else "hold"
        except Exception:
            pass
        try:
            self.update_est()
        except Exception:
            pass

    def _clone_config(self, job):
        """Translate an archived job's params -> a NEW RUN config dict (field-id keyed)."""
        p = job.params or {}
        c = {
            "mode": job.kind or p.get("mode"),     # single|chained|director (enhance pre-filtered)
            "prompt": p.get("prompt"), "directive": p.get("directive"), "anchors": p.get("anchors"),
            "steadiness": p.get("steadiness"), "image": p.get("image"), "seconds": p.get("seconds"),
            "steps": p.get("steps"), "cfg": p.get("cfg"), "seed": p.get("seed"), "fps": p.get("fps"),
            "n_prompt": p.get("n_prompt"), "res": p.get("res"),
            "backend": p.get("backend"), "cond_strength": p.get("cond_strength"),
            "cfg_rescale": p.get("cfg_rescale"), "cfg_interval": p.get("cfg_interval"),
            "wan_ref_anchor": p.get("wan_ref_anchor"),
            # NAME intentionally omitted -> build() auto-mints job_HHMMSS
        }
        seg = p.get("seg_sec")                     # form field id is 'seg'; param key is 'seg_sec'
        if seg not in (None, ""):                  # skip for single runs so the form keeps its default
            c["seg"] = seg
        return {k: v for k, v in c.items() if v is not None}

    def update_est(self):
        try:
            est = self.query_one("#runest", Static)
        except Exception:
            return
        try:
            W, H, fps, total_frames, seg_frames, nseg, chain = self._plan()
            steps = int(self.v("steps"))
            if (self.v("backend") or "ltx") == "wan-turbo":
                steps = min(steps, 8)             # the 4-step distill runs few-step regardless of the steps field
            px = (W * H) / (512 * 320)
            director = self.v("mode") == "director"
            # Calibrated against real 8GB runs (sequential offload, 7B per-seam director).
            # LTX: load ~150s once; per shot ~steps*1.5*px gen + ~70s warmup + ~20s decode.
            # Wan-VACE is much heavier per step + a slower per-shot warmup than LTX (and runs at
            # 16fps internally, already reflected in W/H/seg_frames via _plan()'s real render dims);
            # the per-seam director is now the A1 resident CPU daemon (~90s/seam: no reload, no GPU
            # eviction; was ~300 for the per-seam GPU reload). Backend-independent.
            # [HIGH] refit 2026-07-04 against runs/*.json phase_secs from 5 completed Wan renders
            # (704x480/768x512, 25-45 steps, 1-4 shots): the OLD constants under-read gen by ~1.3-1.6x
            # and warm by ~2.4-4.7x, while over-reading decode by ~3x. New fit: COEF 4.8, WARM 220s/shot,
            # DECODE 38 (least-squares against actual phase_secs; one 21646s decode outlier excluded).
            if (self.v("backend") or "ltx") in ("wan", "wan-turbo"):
                LOAD, COEF, WARM, DECODE, SEAM, SEG_REF = 40, 4.8, 220, 38, 90, 29
            else:
                LOAD, COEF, WARM, DECODE, SEAM, SEG_REF = 150, 1.5, 70, 20, 90, 49
            ff = seg_frames / SEG_REF              # per-shot gen + decode scale with frame count -> fps/seg now move the ETA
            nseam = max(0, nseg - 1)
            _steady = (self.v("steadiness") or "hold")
            if director and _steady == "evolve":   # ETA follows the ENGINE's downgrade rule (audit #5):
                _dv = (self.v("directive") or "").strip()
                if not _dv or _dv == (self.v("prompt") or "").strip():
                    _steady = "hold"               # blank/echo directive -> engine runs hold
            if director and _steady != "evolve":
                nseam = -(-nseam // 3)             # hold/balanced redirect every 3rd seam (director.py cadence)
            secs = (LOAD + nseg * (steps * COEF * px * ff + WARM + DECODE * ff)
                    + (SEAM * nseam if director else 0))
            mode = "DIRECTOR" if director else ("auto-chained" if chain else "single clip")
            warn = "  [b]!! lower resolution[/b]" if seg_frames < 17 else ""
            actual_s, seg_s = round(total_frames / fps, 1), round(seg_frames / fps, 1)
            try:
                req_s = round(float(self.v("seconds")), 1)
            except Exception:
                req_s = None
            asked = f" (asked {req_s}s)" if (req_s is not None and abs(req_s - actual_s) >= 0.05) else ""
            est.update(tmark("warning", f"plan: {nseg} shot(s) · {seg_s}s/seg · {actual_s}s{asked}  ::  {mode}  ::  ~{fmt(secs)}{warn}"))
            self._update_readout(secs, (W, H, fps, total_frames, seg_frames, nseg, chain))   # T22
        except Exception:
            est.update("[dim]plan: enter numbers[/dim]")
            self._update_readout(None, None)   # T22

    def _update_readout(self, secs, plan):
        """T22: refresh the #readout meter strip from the current form. Display-only; a no-op when
        the readout config is off, the module failed to import, or the blind builder owns the right
        region (same coexistence rule as #fieldvisual). Any failure just clears the panel — it must
        never break update_est(). maybe_refit() self-gates on file mtime, so calling it per keystroke
        is cheap; autofit=False falls back to the cached fit only."""
        if readout is None or not getattr(self, "readout_enabled", True):
            return
        try:
            panel = self.query_one("#readout", Static)
        except Exception:
            return
        try:                                       # blind builder owns the region (#rctop hidden) -> skip work
            if self.query_one("#blindpanel", Vertical).has_class("-active"):
                return
        except Exception:
            pass
        try:
            def rd(wid, default=None):             # defensive per-field read (some ids have no widget)
                try:
                    return self.v(wid)
                except Exception:
                    return default
            if plan is not None:
                W, H, fps, total_frames, seg_frames, nseg, chain = plan
            else:
                W = H = fps = total_frames = seg_frames = nseg = 0
                chain = False
            try:
                consult = bool(self.consult.alive())
            except Exception:
                consult = False
            cfg = {"backend": rd("backend"), "mode": rd("mode"), "steadiness": rd("steadiness"),
                   "W": W, "H": H, "fps": fps, "seg_frames": seg_frames, "total_frames": total_frames,
                   "nseg": nseg, "chain": chain, "seconds": rd("seconds"),
                   "steps": rd("steps"), "cfg": rd("cfg"),
                   "cond_strength": rd("cond_strength"), "ltx_variant": rd("ltx_variant"),
                   "wan_ref_anchor": rd("wan_ref_anchor"), "latent_adain": rd("latent_adain"),
                   "reserve_gb": rd("vram_reserve", "1.0"), "consult": consult}
            fit = readout.maybe_refit(REPO) if getattr(self, "readout_autofit", True) else readout.load_fit(REPO)
            # size the bars to the panel's REAL content width -> nothing wraps on narrow/snapped
            # windows (on_resize re-runs update_est -> back here with the fresh measurement)
            try:
                wd = int(panel.content_size.width) or int(self.query_one("#rightcol").content_size.width) - 2
            except Exception:
                wd = 0
            art = readout.render_readout(cfg, secs, fit, width=(wd if wd > 0 else None))
        except Exception:
            art = None
        if art:                                    # fixed fixture: content updates, the box never moves
            _t = Text.from_markup(art)
            _t.no_wrap = True                      # bars clip at the box edge, never wrap
            panel.update(_t)

    def _winpath(self, linux_abs):
        r"""WSL abs path -> \\wsl.localhost UNC, pasteable into Windows Explorer."""
        distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
        return r"\\wsl.localhost" + "\\" + distro + linux_abs.replace("/", "\\")

    def _selected(self, table_id):
        t = self.query_one(table_id, DataTable)
        if t.row_count == 0:
            return None
        try:                                    # the row KEY is the job id — robust to single-cell card rows
            return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        except Exception:
            try:
                return t.get_row_at(t.cursor_row)[0]
            except Exception:
                return None

    def _play_job(self, jid):
        """Open a job's output in the system player. Shared by ▶ PLAY (#atable) and
        ▶ PLAY SELECTED CHILD RUN (#reltable) -- same file, two selection sources."""
        job = self.mgr.jobs.get(jid)
        if not job or not job.out:
            self.query_one("#inspectinfo", Static).update("[#ffcf5c]No output file for this run.[/#ffcf5c]")
            return
        abs_out = job.out if os.path.isabs(job.out) else os.path.join(REPO, job.out)
        if not os.path.exists(abs_out):
            self.query_one("#inspectinfo", Static).update(
                "[#ffcf5c]Output file is missing — nothing to play.[/#ffcf5c]")
            return
        if not open_in_player(abs_out):
            self.query_one("#inspectinfo", Static).update(
                "[#ffcf5c]Couldn't open a player. Paste this into Explorer:[/#ffcf5c]\n  "
                + self._winpath(abs_out))

    def _queue_current_run(self, over=None, skip_budget=False):
        """Build the NEW RUN form into a job and enqueue it. Shared by QUEUE RUN + RE-ROLL + ×N REPLICATE.
        over: optional field-id snapshot (blind-pair) built WITHOUT mutating the live form.
        skip_budget: skip the (nvidia-smi) GPU-budget probe — the ×N loop passes this on every run
        AFTER the first so a burst of N enqueues fires ONE probe, not N, on this Blackwell-sensitive box.
        Returns the queued Job, or None if blocked (empty prompt / GPU budget)."""
        if not (self.v("prompt", over) or "").strip():
            self.query_one("#newinfo", Static).update("[#ffcf5c]Enter a PROMPT first.[/#ffcf5c]")
            return None
        # HONEST GATE (director audit #1): the engine silently runs HOLD when the directive is blank or
        # echoes the prompt — queuing "evolve" like that would record a steadiness that never executed
        # (and a hold-vs-evolve blind A/B would compare two identical runs). Refuse with the reason.
        if (self.v("mode", over) == "director"
                and (self.v("steadiness", over) or "hold") == "evolve"):
            _d = (self.v("directive", over) or "").strip()
            if not _d or _d == (self.v("prompt", over) or "").strip():
                self.query_one("#newinfo", Static).update(
                    "[#ffcf5c]EVOLVE needs a DIRECTIVE distinct from the PROMPT — the engine would "
                    "silently run HOLD (and record it as evolve). Write a directive (the arc to move "
                    "toward), or switch STEADINESS to hold/balanced.[/#ffcf5c]")
                return None
        try:
            title, kind, cmd, params = self.build(over)
        except Exception as ex:   # bad numbers must never crash the app (a crash kills a live render)
            self.query_one("#newinfo", Static).update(
                f"[#ff6d6d]Can't plan this run — check LENGTH / FPS / SEGMENT / STEPS are numbers ({type(ex).__name__}).[/#ff6d6d]")
            return None
        # GPU-budget gate: budget_ok() runs nvidia-smi, so ONLY probe when the board is idle
        # (nvidia-smi during live CUDA crashes the WSL VM). An active run -> this just queues behind it.
        if not skip_budget and not self._cuda_busy():
            cost = gpu_budget.GPU_COST.get(kind, 5500)
            ok, free = gpu_budget.budget_ok(cost)
            if not ok:
                self.query_one("#newinfo", Static).update(
                    f"[#ff6d6d]⛔ Only {free} MB free on your 8 GB GPU — a {kind} run needs ~{cost} MB. "
                    f"Close GPU-heavy apps (browser/Discord/Spotify) or wait, then QUEUE again.[/#ff6d6d]")
                return None
        j = self.mgr.enqueue(title, kind, cmd, params)
        self.consult.kill()                      # free the GPU for the run
        self.query_one(TabbedContent).active = "tab-queue"
        self.query_one("#newinfo", Static).update(f"[#9dffce]Queued {j.id}[/#9dffce]\n{title}")
        return j

    def _new_run_replicate(self):
        """×N REPLICATE from the NEW RUN form: queue the CURRENT config N times (2-5), each with a fresh
        random seed — probes the seed noise floor without needing a first run + ARCHIVE round-trip. Built
        from a form snapshot via _queue_current_run(over=...) so the live widgets are never mutated; the
        first run is the set source and the rest are tagged replicate_set_id=source.id, so they group in
        the QUEUE/ARCHIVE exactly like the ARCHIVE ×N path (_related_jobs / _run_kind)."""
        if not (self.v("prompt") or "").strip():
            self.query_one("#newinfo", Static).update("[#ffcf5c]Enter a PROMPT first.[/#ffcf5c]")
            return
        base_cfg = self._current_form_config()

        def _do(res):
            if not res:
                return
            try:
                n = max(2, min(5, int((res.get("n") or "3").strip() or "3")))
            except Exception:
                n = 3
            set_id, queued = None, []
            for _ in range(n):
                over = dict(base_cfg)
                over["seed"] = str(random.randint(1, 2**31 - 1))   # fresh seed per replicate
                # one GPU-budget probe (nvidia-smi) for the whole set: the FIRST run probes, the rest skip
                j = self._queue_current_run(over=over, skip_budget=(set_id is not None))
                if not j:                       # blocked (empty prompt / GPU budget) -> stop; msg already shown
                    break
                if set_id is None:
                    set_id = j.id               # first run is the set source (no replicate_set_id)
                else:
                    j.params["replicate_set_id"] = set_id
                    j.save()
                queued.append(j.id)
            if queued:
                self.query_one("#newinfo", Static).update(
                    f"[#9dffce]×{len(queued)} replicate set queued (random seeds): {', '.join(queued)}.[/#9dffce]")

        _summ = (base_cfg.get("prompt") or "this run")[:40]
        self.push_screen(ReplicateScreen(
            _summ, subtitle=f"queue '{_summ}' N times with different random seeds — probes the seed noise floor."), _do)

    # ---------- ⇄ BLIND A/B (fresh, blind, randomized variable-isolation pair) ----------
    # Every run-affecting field is varyable. 'checkpoint' is virtual (ltx_variant none<->distilled, LTX-only).
    BLIND_VARS = ("prompt", "n_prompt", "seconds", "steps", "cfg", "seed", "fps", "res",
                  "backend", "seg", "cond_strength", "steadiness", "checkpoint",
                  "cfg_rescale", "cfg_interval", "wan_ref_anchor")

    # (var, friendly label) for the 'Field to vary' Select — order = the builder's dropdown order.
    BLIND_FIELD_OPTIONS = [
        ("prompt", "prompt"), ("negative", "n_prompt"), ("length (seconds)", "seconds"),
        ("steps", "steps"), ("guidance (cfg)", "cfg"), ("seed", "seed"), ("fps", "fps"),
        ("resolution", "res"), ("backend", "backend"), ("segment (seg)", "seg"),
        ("cond_strength", "cond_strength"), ("steadiness", "steadiness"),
        ("checkpoint", "checkpoint"), ("guidance rescale", "cfg_rescale"),
        ("guidance schedule", "cfg_interval"), ("identity anchor", "wan_ref_anchor"),
    ]

    def _blind_clone_widget(self, var, wid, prefill):
        """Build a FRESH widget of the SAME class + options/validation as the form's field for `var`,
        with id `wid`, pre-set to `prefill`. Select fields clone their exact option list; numeric/text
        fields become Input; prompt/negative become TextArea. checkpoint -> a 0.9.5/0.9.8 Select."""
        SELECT_OPTS = {
            "res": [(k, k) for k in RES],
            "backend": [("LTX-2B (fast)", "ltx"), ("Wan-VACE-1.3B (nicer, slower)", "wan"),
                        ("Wan turbo (4-step distill)", "wan-turbo")],
            "steadiness": [("Hold (faithful)", "hold"), ("Balanced", "balanced"),
                           ("Evolve (journey)", "evolve")],
            "cfg_rescale": [("off", "off"), ("0.5", "0.5"), ("0.7", "0.7")],
            "cfg_interval": [("off", "off"), ("front 30%", "0.0:0.3"), ("front 50%", "0.0:0.5"),
                             ("front 70%", "0.0:0.7"), ("late 70%", "0.3:1.0"),
                             ("every 2nd step", "2"), ("every 3rd step", "3")],
            "wan_ref_anchor": [("off", "off"), ("on (Wan only)", "on")],
            "checkpoint": [("0.9.5 (base)", "none"), ("0.9.8-distilled", "distilled")],
        }
        if var in ("prompt", "n_prompt"):
            return TextArea(str(prefill or ""), id=wid, soft_wrap=True, tab_behavior="focus", classes="ta")
        if var in SELECT_OPTS:
            opts = SELECT_OPTS[var]
            vals = [v for _, v in opts]
            val = prefill if prefill in vals else vals[0]
            return Select(opts, id=wid, value=val, allow_blank=False)
        return Input(value=str(prefill if prefill is not None else ""), id=wid)

    def _blind_render_clones(self, var):
        """Mount/replace the two A/B clone widgets for the chosen field inside #blindclones.
        A pre-fills from the current form value; B seeds a sensible different default."""
        box = self.query_one("#blindclones", Vertical)
        for child in list(box.children):
            child.remove()
        if not var:
            return
        a_default = self._blind_form_default(var)
        b_default = self._blind_b_default(var, a_default)
        arow = Horizontal(Label("A", classes="lbl"),
                          self._blind_clone_widget(var, "blind_a", a_default), classes="row blindab")
        brow = Horizontal(Label("B", classes="lbl"),
                          self._blind_clone_widget(var, "blind_b", b_default), classes="row blindab")
        box.mount(arow)
        box.mount(brow)

    def _blind_b_default(self, var, a_default):
        """A sensible starting B value that differs from A (user overrides freely)."""
        if var == "checkpoint":
            return "none" if str(a_default) != "none" else "distilled"
        if var == "backend":
            return "wan" if str(a_default) != "wan" else "ltx"
        if var == "steadiness":
            return "balanced" if str(a_default) != "balanced" else "evolve"
        if var in ("cfg_rescale", "cfg_interval", "wan_ref_anchor"):
            return "off"
        if var == "res":
            keys = list(RES.keys())
            for k in keys:
                if k != a_default:
                    return k
            return keys[0]
        return ""

    def _blind_read_clone(self, wid):
        """Read a clone widget's current value (TextArea.text / Input.value / Select.value)."""
        w = self.query_one(f"#{wid}")
        val = w.text if isinstance(w, TextArea) else w.value
        return "" if val in (None, Select.BLANK) else str(val)

    def _current_form_config(self):
        """Snapshot the NEW RUN form as a field-id-keyed config dict (same shape _apply_config consumes),
        so a blind pair can be rebuilt twice with only ONE field swapped. NAME dropped -> auto-timestamps."""
        c = {"mode": ("director" if self.v("mode") == "director" else "single")}
        for wid in ("prompt", "directive", "anchors", "image", "seconds", "seg", "steps", "cfg",
                    "seed", "fps", "n_prompt", "backend", "cond_strength", "cfg_rescale",
                    "cfg_interval", "wan_ref_anchor", "steadiness"):
            try:
                c[wid] = self.v(wid)
            except Exception:
                pass
        try:
            c["res"] = self.v("res")
        except Exception:
            pass
        return c

    def _blind_form_default(self, var):
        """The A-value default for a blind variable = its CURRENT form value (checkpoint -> 'none')."""
        if var == "checkpoint":
            return "none"
        try:
            return str(self.v("seg" if var == "seg" else var))
        except Exception:
            return ""

    def _blind_apply_var(self, cfg, var, value):
        """Set ONE blind variable on a field-id config dict. checkpoint is virtual (handled post-build)."""
        if var == "checkpoint":
            cfg["_ltx_variant"] = "distilled" if str(value).strip().lower() in ("distilled", "0.9.8", "b") else "none"
        else:
            cfg[var] = value

    def _validate_blind_value(self, var, value):
        """Reject a typo before it becomes a silently-coerced run. Returns an error string or None."""
        if var in ("steps", "cfg", "seg", "cond_strength", "fps", "seconds"):
            try:
                float(value)
            except (TypeError, ValueError):
                return f"{var} must be a number (got '{value}')."
        elif var == "seed":
            if not str(value).strip().lstrip("-").isdigit():
                return f"seed must be an integer (got '{value}')."
        elif var == "res":
            if value not in RES:
                return f"res must be one of: {', '.join(RES.keys())} (got '{value}')."
        elif var in ("prompt", "n_prompt"):
            if not str(value).strip():
                return f"{var} must not be empty."
        elif var == "backend":
            if value.lower().replace(" ", "-") not in ("ltx", "wan", "wan-turbo"):
                return f"backend must be one of: ltx, wan, wan-turbo (got '{value}')."
        elif var == "steadiness":
            if value.lower() not in ("hold", "balanced", "evolve"):
                return f"steadiness must be one of: hold, balanced, evolve (got '{value}')."
        elif var in ("cfg_rescale", "cfg_interval"):
            ok = ("off", "on") if var == "cfg_interval" else None   # cfg_rescale accepts off + a numeric strength
            if var == "cfg_interval":
                if value.lower() not in ("off", "on", "0.0:0.5"):
                    return f"cfg_interval must be off or on/0.0:0.5 (got '{value}')."
            else:
                if value.lower() != "off":
                    try:
                        float(value)
                    except (TypeError, ValueError):
                        return f"cfg_rescale must be off or a number (got '{value}')."
        elif var == "wan_ref_anchor":
            if value.lower() not in ("off", "on"):
                return f"wan_ref_anchor must be off or on (got '{value}')."
        elif var == "checkpoint":
            if str(value).strip().lower() not in ("none", "distilled", "0.9.5", "0.9.8", "a", "b"):
                return f"checkpoint must be none (0.9.5) or distilled (0.9.8) (got '{value}')."
        return None

    def _blind_variant_cfg(self, base_cfg, var, value, seed):
        """The override SNAPSHOT dict for one variant (name auto, shared seed, one field swapped).
        Pure/torch-free: no widget I/O, no side effects -- used both to queue and to self-check that
        the two variants differ in exactly one field before anything is enqueued."""
        cfg = dict(base_cfg)
        cfg["name"] = ""
        cfg["seed"] = str(seed)
        cfg.pop("_ltx_variant", None)
        self._blind_apply_var(cfg, var, value)
        return cfg

    def _queue_blind_variant(self, base_cfg, var, value, seed):
        """Build the base-form SNAPSHOT with ONE variable = value + the shared seed, and queue it
        WITHOUT mutating the live form widgets. Building from the snapshot dict (via build(over=...))
        means no on_select_changed/_sync_cfg_default side-effect can fire and clobber cfg/steps, so the
        two variants are guaranteed identical except the one varied field. The virtual `checkpoint`
        variable rides as over["_ltx_variant"], appended as --ltx_variant on WHICHEVER engine runs
        (director.py or run_ltx.py). Returns the Job (or None if blocked / refused)."""
        cfg = self._blind_variant_cfg(base_cfg, var, value, seed)   # one field swapped, shared seed, no widget I/O
        # REFUSE a false pairing: a checkpoint A/B is only real on the LTX backend (the distilled flag is
        # LTX-only and silently ignored on wan/wan-turbo). Record nothing rather than a lie.
        if cfg.get("_ltx_variant") == "distilled" and (cfg.get("backend") or "ltx") != "ltx":
            self.query_one("#newinfo", Static).update(
                "[#ff6d6d]checkpoint A/B needs backend=ltx (0.9.8-distilled is LTX-only) — "
                "switch BACKEND to ltx, then retry.[/#ff6d6d]")
            return None
        return self._queue_current_run(over=cfg)          # same gated path as QUEUE RUN, dict-driven build

    def _blind_msg(self, markup):
        """Show a builder status line in the inline panel (falls back to #newinfo if not mounted)."""
        try:
            self.query_one("#blindmsg", Static).update(markup)
        except Exception:
            self.query_one("#newinfo", Static).update(markup)

    def _run_blind(self, var, a_val, b_val):
        """Queue a BLIND A/B pair from the CURRENT form, varying ONE field (a_val vs b_val). REUSED by
        the inline builder. Shared seed + coin-flip labels + randomized enqueue order + one-field
        isolation self-check; both variants built from the snapshot dict via build(over=...) so ONLY the
        chosen field differs. Writes status to the builder message line. No modal, no widget mutation."""
        if var not in self.BLIND_VARS:
            self._blind_msg(f"[#ff6d6d]BLIND A/B needs a field in: {', '.join(self.BLIND_VARS)}.[/#ff6d6d]")
            return
        base_cfg = self._current_form_config()
        # A base PROMPT is required unless the prompt itself is the field being varied (then the A/B
        # clones supply it). n_prompt/others still need a real prompt to render anything.
        if var != "prompt" and not (base_cfg.get("prompt") or "").strip():
            self._blind_msg("[#ffcf5c]Enter a base PROMPT first — or set 'Field to vary' to prompt.[/#ffcf5c]")
            return
        a_val = (a_val if a_val is not None else "")
        b_val = (b_val if b_val is not None else "")
        if var not in ("prompt", "n_prompt"):
            a_val, b_val = a_val.strip(), b_val.strip()
        if not a_val:
            a_val = self._blind_form_default(var)
        if not str(b_val).strip():
            self._blind_msg("[#ff6d6d]Enter a B value.[/#ff6d6d]")
            return
        for label, val in (("A", a_val), ("B", b_val)):
            err = self._validate_blind_value(var, val)
            if err:
                self._blind_msg(f"[#ff6d6d]{label}: {err}[/#ff6d6d]")
                return
        if str(a_val).strip().lower() == str(b_val).strip().lower():
            self._blind_msg("[#ff6d6d]A and B are the same value — nothing to isolate.[/#ff6d6d]")
            return
        # Pre-flight the checkpoint-needs-LTX refusal HERE so the message lands on the visible builder
        # line (the same guard fires again inside _queue_blind_variant on #newinfo, but that panel is
        # hidden while the builder is open).
        if var == "checkpoint":
            def _is_distilled(x):
                return str(x).strip().lower() in ("distilled", "0.9.8", "b")
            if (_is_distilled(a_val) or _is_distilled(b_val)) and (base_cfg.get("backend") or "ltx") != "ltx":
                self._blind_msg("[#ff6d6d]checkpoint A/B needs backend=ltx (0.9.8-distilled is LTX-only) — "
                                "switch BACKEND to ltx, then retry.[/#ff6d6d]")
                return
        if var == "cfg_interval" and (base_cfg.get("backend") or "ltx") != "wan":
            # would silently produce two IDENTICAL runs (build omits the flag off-wan) -> refuse
            self._blind_msg("[#ff6d6d]guidance-schedule A/B needs backend=wan (interval CFG is "
                            "Wan-only; LTX's batched CFG is incompatible) — switch BACKEND, then retry.[/#ff6d6d]")
            return
        # SINGLE shared seed: honor the form seed if set, else mint ONE concrete random seed for both.
        s = (base_cfg.get("seed") or "").strip()
        seed = s if (s and s.lstrip("-").isdigit()) else str(random.randint(1, 2**31 - 1))
        # ISOLATION SELF-CHECK (torch-free): for a NON-cascading variable (not backend/checkpoint,
        # which legitimately move a 2nd engine-mandatory field), the two variant snapshots MUST
        # differ in exactly one key. Refuse to enqueue a pair that would secretly differ in more.
        if var not in ("backend", "checkpoint"):
            ca = self._blind_variant_cfg(base_cfg, var, a_val, seed)
            cb = self._blind_variant_cfg(base_cfg, var, b_val, seed)
            keys = set(ca) | set(cb)
            diff = sorted(k for k in keys if ca.get(k) != cb.get(k))
            if diff != [var]:
                self._blind_msg(
                    f"[#ff6d6d]BLIND A/B isolation check failed — variants differ in {diff}, "
                    f"expected only ['{var}']. Not queued.[/#ff6d6d]")
                return
        # BLINDING via two independent coin-flips:
        #   (1) which VALUE is labeled "A" vs "B"  -- so the label leaks nothing about the value
        #   (2) the ORDER the two physical runs are enqueued -- so run order leaks nothing either
        if random.random() < 0.5:                 # flip #1: assign the A/B labels to the values
            label_value = {"A": a_val, "B": b_val}
        else:
            label_value = {"A": b_val, "B": a_val}
        enqueue_labels = ["A", "B"]
        random.shuffle(enqueue_labels)            # flip #2: randomize enqueue order
        pair_id = time.strftime("blind-%y%m%d-%H%M%S") + f"-{random.randint(1000, 9999)}"
        jobs = {}                                 # label ("A"/"B") -> Job
        for lbl in enqueue_labels:
            j = self._queue_blind_variant(base_cfg, var, label_value[lbl], seed)
            if not j:                            # gate blocked (empty prompt / GPU budget / checkpoint refusal) -> abort
                self._blind_msg(
                    "[#ff6d6d]BLIND A/B aborted — a run was blocked (check GPU budget / prompt / backend).[/#ff6d6d]")
                return
            j.params["pair_id"] = pair_id
            j.params["pair_variant"] = lbl
            j.params["pair_blind"] = True
            j.params["pair_varied_dial"] = var
            j.params["pair_revealed"] = False
            j.save()
            jobs[lbl] = j
        a_job, b_job = jobs.get("A"), jobs.get("B")
        # BLIND STORAGE: the ONLY place the A/B <-> config truth lives in the clear.
        try:
            os.makedirs(os.path.join(REPO, "runs"), exist_ok=True)
            with open(os.path.join(REPO, "runs", "pair_blinds.jsonl"), "a") as f:
                f.write(json.dumps({
                    "pair_id": pair_id,
                    "a_job_id": a_job.id if a_job else None,
                    "b_job_id": b_job.id if b_job else None,
                    "varied": var,
                    "a_value": label_value["A"],   # what logical label A actually ran
                    "b_value": label_value["B"],   # what logical label B actually ran
                    "seed": seed, "ts": time.time()}) + "\n")
        except Exception:
            pass
        # done -> hide the builder, restore the info panel, report on #newinfo, jump to the queue.
        self._close_blind_panel()
        # wan-turbo is a mandatory cfg=1.0 / few-step distill: when a backend A/B pits it against
        # ltx/wan, the engine forces cfg/steps for the turbo side, so that variant unavoidably
        # differs in cfg+steps too (not a pure one-variable isolation). Warn, don't block.
        _turbo_note = ""
        if var == "backend" and any(
                str(x).lower().replace(" ", "-") == "wan-turbo" for x in (a_val, b_val)):
            _turbo_note = ("  [#ffcf5c]note: wan-turbo forces cfg=1.0 / steps≤8, so that side also "
                           "differs in cfg+steps (engine-mandatory).[/#ffcf5c]")
        self.query_one(TabbedContent).active = "tab-queue"
        self.query_one("#newinfo", Static).update(
            f"[#9dffce]Queued BLIND A/B pair {pair_id} — two runs, seed {seed}, varying "
            f"'{var}' (blind). Rate with ≷ RATE PAIR, then ↯ REVEAL in ARCHIVE.[/#9dffce]"
            + _turbo_note)

    def _open_blind_panel(self):
        """Show the inline builder in the right region: the WHOLE fixed rail (#rctop) steps aside
        as one unit — individual boxes never appear/disappear on their own."""
        try:
            self.query_one("#rctop").display = False
        except Exception:
            pass
        panel = self.query_one("#blindpanel", Vertical)
        panel.add_class("-active")
        self.query_one("#blindmsg", Static).update("")
        try:                                     # start with no field chosen -> no clones mounted
            self.query_one("#blind_var", Select).value = Select.BLANK
        except Exception:
            pass
        self._blind_render_clones(None)

    def _close_blind_panel(self):
        """Hide the inline builder and bring the fixed rail back."""
        try:
            self.query_one("#blindpanel", Vertical).remove_class("-active")
        except Exception:
            pass
        try:
            self.query_one("#rctop").display = True
        except Exception:
            pass
        try:
            self.update_est()                    # T22: re-render the readout strip now the region is free
        except Exception:
            pass

    def on_button_pressed(self, e: Button.Pressed):
        b = e.button.id
        if b and b.startswith("i_"):        # ⓘ tooltip buttons -> show the dial's help in the focus #newinfo panel
            key = b[2:]
            if key in HELP:
                self.query_one("#newinfo", Static).update(HELP[key])
            self._show_field_visual(key)
            return
        if b == "queuebtn":
            self._queue_current_run()
        elif b == "newreplbtn":
            self._new_run_replicate()
        elif b == "liveframesbtn":  # step through everything the ACTIVE run has rendered so far
            job = self.mgr.active()
            if job is None:
                return
            frames = self._live_frames(job)
            if not frames:
                self.notify("No frames on disk yet — they land after the first completed shot "
                            "(single-shot runs: only at save).", severity="warning", timeout=6)
                return
            scr = FrameScrollScreen(frames, "%s — %d so far" % (job.id, len(frames)))
            scr.i = len(frames) - 1     # open on the NEWEST frame (the live edge), step back from there
            self.push_screen(scr)
        elif b == "sndtestbtn":     # preview the run-done sound — bypasses the SOUND toggle so it always plays
            msg = sounds.preview("run_done", REPO) if sounds is not None else "sound module unavailable"
            self.query_one("#newinfo", Static).update("[#9dffce]♪ %s[/#9dffce]" % msg)
        elif b == "blindabbtn":
            # TOGGLE the inline builder in the right region (no modal). Opening does NOT require a prompt
            # — 'prompt' is itself a varyable field, so the base prompt may legitimately be empty here;
            # the base-prompt requirement is enforced at RUN time in _run_blind only when var != prompt.
            if self.query_one("#blindpanel", Vertical).has_class("-active"):
                self._close_blind_panel()
                return
            self._open_blind_panel()
        elif b == "blind_cancel":
            self._close_blind_panel()
        elif b == "blind_run":
            try:
                var = self.query_one("#blind_var", Select).value
            except Exception:
                var = Select.BLANK
            if var in (None, Select.BLANK):
                self._blind_msg("[#ff6d6d]Pick a field to vary first.[/#ff6d6d]")
                return
            try:
                a_val = self._blind_read_clone("blind_a")
                b_val = self._blind_read_clone("blind_b")
            except Exception:
                self._blind_msg("[#ff6d6d]Pick a field to vary first.[/#ff6d6d]")
                return
            self._run_blind(var, a_val, b_val)
        elif b == "consultbtn":
            if self.mgr.active() is not None:
                self.query_one("#newinfo", Static).update(
                    "[#ffcf5c]GPU is busy with a run — SUSPEND it (frees the GPU) or let it finish, then consult. "
                    "A plain PAUSE still holds the GPU, so it won't free the director.[/#ffcf5c]")
            else:
                self.push_screen(ConsultScreen())
        elif b == "chatbtn":
            if self.mgr.active() is not None:
                self.query_one("#newinfo", Static).update(
                    "[#ffcf5c]GPU is busy with a run — SUSPEND it or let it finish, then chat with the model.[/#ffcf5c]")
            else:
                self.push_screen(ChatScreen())
        elif b == "removebtn":
            jid = self._selected("#qtable")
            if jid:
                self.mgr.remove(jid)
        elif b == "qresumebtn":
            jid = self._selected("#qtable")
            if jid:
                self.mgr.resume_suspended(jid)
        elif b == "promotebtn":
            jid = self._selected("#qtable")
            if jid:
                self.mgr.promote(jid)
        elif b == "qinspectbtn":
            self._render_qinspect()
        elif b == "qclonebtn":
            # CLONE a queued run -> NEW RUN form, reusing the EXACT archive-clone path
            # (_clone_config -> _apply_config -> switch to NEW RUN). Read-only on the
            # queued run's fixed config; no dirty read, RESUME/PROMOTE/REMOVE untouched.
            jid = self._selected("#qtable")
            if not jid:
                return
            job = self.mgr.jobs.get(jid)
            if not job:
                return
            if (job.params.get("mode") or job.kind) == "enhance":
                self.query_one("#qinspect", Static).update(
                    "[#ffcf5c]Enhance runs can't be cloned — they're a post-process of another run.[/#ffcf5c]")
                return
            try:
                cfg = self._clone_config(job)
                self._apply_config(cfg)
                self.query_one(TabbedContent).active = "tab-new"
                try:
                    self.query_one("#prompt").focus()
                except Exception:
                    pass
                self.query_one("#newinfo", Static).update(
                    f"[#9dffce]Cloned queued {job.id} — tweak anything, then QUEUE RUN. "
                    f"Seed {cfg.get('seed', '0')} kept; NAME left blank so it auto-timestamps.[/#9dffce]")
            except Exception:
                pass
        elif b == "pausebtn":
            self.mgr.pause()
        elif b == "resumebtn":
            self.mgr.resume()
        elif b == "suspendbtn":
            self.mgr.suspend()
        elif b == "cancelbtn":
            self.mgr.cancel()
        elif b == "livetermbtn":
            self._toggle_live_term()
        elif b == "dirnotebtn":
            self.action_toggle_dirraw()
        elif b == "arctermbtn":
            self._toggle_arc_term()
        elif b in ("inspectbtn", "timingbtn"):
            jid = self._selected("#atable")
            if not jid:
                return
            self._insp_jid = jid
            self._insp_view = "timing" if b == "timingbtn" else "inspect"
            self._render_inspect()
            self.query_one("#inspectpanel").display = True
            self.query_one("#inspectlog").display = False
            self.query_one("#arctermbtn", Button).label = "» TERMINAL"

        elif b == "revealbtn":
            jid = self._selected("#atable")
            if not jid:
                return
            job = self.mgr.jobs.get(jid)
            if not job:
                return
            if not (job.params.get("pair_blind") and job.params.get("pair_id")):
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]Selected run isn't a blind A/B pair — nothing to reveal.[/#ffcf5c]")
                return
            if job.params.get("pair_revealed"):
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]This blind A/B pair is already revealed.[/#ffcf5c]")
                return
            pid = job.params.get("pair_id")
            partners = [jj for jj in self.mgr.jobs.values() if jj.params.get("pair_id") == pid]
            # Only reveal once BOTH runs have finished (never leak while a result is still forming).
            unfinished = [jj for jj in partners if jj.status not in ARCHIVED]
            if unfinished:
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]Both runs must finish before REVEAL — keeps the comparison honest.[/#ffcf5c]")
                return
            for jj in partners:                          # flip BOTH jobs of the pair (persists via params)
                jj.params["pair_revealed"] = True
                jj.save()
            self._asig = None                            # force the ARCHIVE table (kind glyph) to repaint
            # Re-render the INSPECT view so the varied value + variant now show; then leave a summary line.
            self._insp_jid = jid
            self._insp_view = "inspect"
            self._render_inspect()
            self.query_one("#inspectpanel").display = True
            self.query_one("#inspectlog").display = False

        elif b == "clonebtn":
            jid = self._selected("#atable")
            if not jid:
                return
            job = self.mgr.jobs.get(jid)
            if not job:
                return
            if (job.params.get("mode") or job.kind) == "enhance":
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]Enhance runs can't be cloned — they're a post-process of another run. "
                    "Clone the original instead.[/#ffcf5c]")
                return
            cfg = self._clone_config(job)
            self._apply_config(cfg)
            self.query_one(TabbedContent).active = "tab-new"
            try:
                self.query_one("#prompt").focus()
            except Exception:
                pass
            self.query_one("#newinfo", Static).update(
                f"[#9dffce]Cloned {job.id} — tweak anything, then QUEUE RUN. "
                f"Seed {cfg.get('seed', '0')} kept for reproducibility; NAME left blank so it auto-timestamps.[/#9dffce]")
        elif b == "rerollbtn":
            jid = self._selected("#atable")
            if not jid:
                return
            job = self.mgr.jobs.get(jid)
            if not job:
                return
            if (job.params.get("mode") or job.kind) == "enhance":
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]Enhance runs can't be re-rolled — they have no seed. Re-roll the original render.[/#ffcf5c]")
                return

            def _do_reroll(res):
                if not res:                          # None -> user cancelled
                    return
                import random
                s = (res.get("seed") or "").strip()
                seed = s if (s and s.lstrip("-").isdigit()) else str(random.randint(1, 2**31 - 1))
                cfg = self._clone_config(job)
                cfg["seed"] = seed                   # the ONLY change vs the original run
                cfg["name"] = ""                     # clear any stale NAME typed in the form (A13)
                cfg.setdefault("backend", "ltx")     # legacy jobs missing these params must not
                cfg.setdefault("cond_strength", "1.0")   # inherit whatever the form currently holds
                self._apply_config(cfg)
                j = self._queue_current_run()        # same path as QUEUE RUN (budget gate, enqueue)
                if j:
                    self.query_one("#inspectinfo", Static).update(
                        f"[#9dffce]Re-rolled {job.id} → {j.id} with seed {seed}.[/#9dffce]")
            self.push_screen(RerollScreen((job.title or job.id)[:40], job.params.get("seed")), _do_reroll)
        elif b == "pairbtn":
            jid = self._selected("#atable")
            if not jid:
                return
            job = self.mgr.jobs.get(jid)
            if not job:
                return
            if (job.params.get("mode") or job.kind) == "enhance":
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]Enhance runs can't be paired — they have no seed. Pair the original render.[/#ffcf5c]")
                return

            def _do_pair(res):
                if not res:                          # None -> user cancelled
                    return
                dial, value = res.get("dial") or "", (res.get("value") or "").strip()
                if dial not in PairScreen.DIALS or not value:
                    self.query_one("#inspectinfo", Static).update(
                        f"[#ff6d6d]PAIR needs a dial in: {', '.join(PairScreen.DIALS)}.[/#ff6d6d]")
                    return
                # Validate the VALUE per dial -> a typo becomes a clear rejection, not a silently-coerced run
                # (the form's own fallbacks would otherwise turn 'sdxl' into ltx, or bad res into 704x480).
                err = None
                if dial in ("steps", "cfg", "seg", "cond_strength", "fps"):
                    try:
                        float(value)
                    except (TypeError, ValueError):
                        err = f"{dial} must be a number (got '{value}')."
                elif dial == "res":
                    digits = ""
                    for ch in value:                  # mirror res_key()'s leading-digit match; reject its silent fallback
                        if ch.isdigit():
                            digits += ch
                        elif digits:
                            break
                    if not any(k.split()[0] == digits for k in RES):
                        err = f"res must round-trip to one of: {', '.join(RES)} (got '{value}')."
                elif dial == "backend":
                    if value.lower().replace(" ", "-") not in ("ltx", "wan", "wan-turbo"):
                        err = f"backend must be one of: ltx, wan, wan-turbo (got '{value}')."
                elif dial == "steadiness":
                    if value.lower() not in ("hold", "balanced", "evolve"):
                        err = f"steadiness must be one of: hold, balanced, evolve (got '{value}')."
                if err:
                    self.query_one("#inspectinfo", Static).update(f"[#ff6d6d]{err}[/#ff6d6d]")
                    return
                cfg = self._clone_config(job)         # KEEP the seed -- only the one dial differs
                cfg[dial] = value
                cfg["name"] = ""                      # clear any stale NAME typed in the form (A13)
                cfg.setdefault("backend", "ltx")      # legacy jobs missing these params must not
                cfg.setdefault("cond_strength", "1.0")   # inherit whatever the form currently holds
                self._apply_config(cfg)
                j = self._queue_current_run()         # same path as QUEUE RUN (budget gate, enqueue)
                if j:
                    j.params["pair_id"] = job.id
                    j.params["pair_variant"] = "B"
                    j.save()
                    job.params.setdefault("pair_id", job.id)      # back-annotate the source, if unset
                    job.params.setdefault("pair_variant", "A")
                    job.save()
                    self.query_one("#inspectinfo", Static).update(
                        f"[#9dffce]Paired {job.id} → {j.id} ({dial}={value}, same seed).[/#9dffce]")
            self.push_screen(PairScreen((job.title or job.id)[:40]), _do_pair)
        elif b == "replbtn":
            jid = self._selected("#atable")
            if not jid:
                return
            job = self.mgr.jobs.get(jid)
            if not job:
                return
            if (job.params.get("mode") or job.kind) == "enhance":
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]Enhance runs can't be replicated — they have no seed. Replicate the original render.[/#ffcf5c]")
                return

            def _do_replicate(res):
                if not res:
                    return
                import random
                try:
                    n = max(2, min(5, int((res.get("n") or "3").strip() or "3")))
                except Exception:
                    n = 3
                queued = []
                for _ in range(n):
                    cfg = self._clone_config(job)
                    cfg["seed"] = str(random.randint(1, 2**31 - 1))
                    cfg["name"] = ""
                    cfg.setdefault("backend", "ltx")
                    cfg.setdefault("cond_strength", "1.0")
                    self._apply_config(cfg)
                    j = self._queue_current_run()
                    if j:
                        j.params["replicate_set_id"] = job.id
                        j.save()
                        queued.append(j.id)
                if queued:
                    self.query_one("#inspectinfo", Static).update(
                        f"[#9dffce]Replicated {job.id} ×{len(queued)}: {', '.join(queued)}.[/#9dffce]")
            self.push_screen(ReplicateScreen((job.title or job.id)[:40]), _do_replicate)
        elif b == "favbtn":         # toggle ★ favorite on the selected ARCHIVE run (persisted in job.params)
            jid = self._selected("#atable")
            job = self.mgr.jobs.get(jid) if jid else None
            if job:
                p = job.params or {}
                fav = not bool(p.get("favorite"))
                p["favorite"] = fav
                job.params = p
                job.save()
                self.query_one("#inspectinfo", Static).update(
                    ("[#ffcf5c]★ favorited[/#ffcf5c]" if fav else "[dim]☆ unfavorited[/dim]") + "  " + job.id)
        elif b == "framesbtn":      # scroll a finished run's saved output frames as terminal art
            jid = self._selected("#atable")
            job = self.mgr.jobs.get(jid) if jid else None
            if not job:
                return
            fd = (job.params or {}).get("frames_dir") or ""
            fdabs = fd if os.path.isabs(fd) else os.path.join(REPO, fd)
            try:
                frames = sorted(os.path.join(fdabs, f) for f in os.listdir(fdabs) if f.lower().endswith(".png"))
            except Exception:
                frames = []
            if not frames:
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]No saved frames for this run (frames_dir empty or cleaned up).[/#ffcf5c]")
                return
            self.push_screen(FrameScrollScreen(frames, job.id))
        elif b == "ratepairbtn":
            jid = self._selected("#atable")
            if not jid:
                return
            job = self.mgr.jobs.get(jid)
            if not job:
                return
            pid = job.params.get("pair_id")
            if not pid:
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]Selected run has no pair — use PAIR A/B first.[/#ffcf5c]")
                return
            partner = next((jj for jj in self.mgr.jobs.values()
                            if jj.id != job.id and jj.params.get("pair_id") == pid), None)
            if not partner:
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]Partner run not found (may have been deleted).[/#ffcf5c]")
                return
            import random
            a, bb = (job, partner) if random.random() < 0.5 else (partner, job)
            slot = {"1": a.id, "2": bb.id}

            def _do_rate(pick):
                if not pick:
                    return
                winner = "tie" if pick == "tie" else slot[pick]
                rec = {"pair_id": pid, "winner": winner, "ts": time.time()}
                try:
                    os.makedirs(os.path.join(REPO, "runs"), exist_ok=True)
                    with open(os.path.join(REPO, "runs", "pair_ratings.jsonl"), "a") as f:
                        f.write(json.dumps(rec) + "\n")
                    self.query_one("#inspectinfo", Static).update(f"[#9dffce]Recorded: {winner}.[/#9dffce]")
                except Exception as ex:
                    self.query_one("#inspectinfo", Static).update(
                        f"[#ff6d6d]Couldn't record rating: {ex}[/#ff6d6d]")
            self.push_screen(RatePairScreen(a.out or "(no output)", bb.out or "(no output)"), _do_rate)
        elif b == "enhancebtn":
            jid = self._selected("#atable")
            if not jid:
                return
            job = self.mgr.jobs.get(jid)
            if not job:
                return
            src_rel = job.params.get("frames_dir", "")
            src_abs = os.path.join(REPO, src_rel) if src_rel else ""
            pngs = sorted(glob.glob(os.path.join(src_abs, "*.png"))) if src_abs else []
            if not pngs:    # only runs/enhances that saved PNG frames can be re-enhanced
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]No reusable frames for this run — nothing to enhance.[/#ffcf5c]")
                return
            try:
                w0, h0 = Image.open(pngs[0]).size
            except Exception:
                w0 = h0 = 0
            gen = int(job.params.get("enh_gen", 0)) + 1
            src_fps = int(float(job.params.get("fps_out") or job.params.get("fps") or 24))
            defaults = dict(interp=("1" if gen > 1 else "2"),     # don't silently double fps on re-passes
                            interp_engine=(job.params.get("enh_interpeng") or "rife"),
                            upscale=("0" if w0 >= 1280 else "4"),  # auto-off once already large
                            upmodel=(job.params.get("enh_upmodel") or "realesrgan"),
                            face="gfpgan", deflicker="0", restore="none",
                            tile_feather="0", interp_skip="0")

            def _go(d):
                nonlocal gen            # _go mutates gen (line below); without this the read 2 lines down raises UnboundLocalError
                if not d:
                    return
                # budget_ok() runs nvidia-smi -> ONLY probe when the board is idle (nvidia-smi during
                # live CUDA crashes the WSL VM). If a run is active, this enhance queues behind it.
                if not self._cuda_busy():
                    ok, free = gpu_budget.budget_ok(gpu_budget.GPU_COST["enhance"])
                    if not ok:
                        self.query_one("#inspectinfo", Static).update(
                            f"[#ff6d6d]⛔ Only {free} MB free on your 8 GB GPU — enhance needs ~{gpu_budget.GPU_COST['enhance']} MB. "
                            f"Free some VRAM (close GPU apps) and retry.[/#ff6d6d]")
                        return
                pf = ["--interp", d["interp"]]
                if d.get("interp_engine") == "film" and d["interp"] != "1":
                    pf += ["--interp_engine", "film"]
                if d.get("deflicker") == "1":
                    pf += ["--deflicker"]
                if d["upscale"] != "0":
                    pf += ["--upscale", "--upscaler", d.get("upmodel", "realesrgan"), "--upscale_factor", d["upscale"]]
                if d["face"] != "0":
                    pf += ["--face", "--face_model", d["face"]]
                restore = d.get("restore", "none")
                if restore != "none":      # "seedvr2-3b" / "seedvr2-7b" -> --restore seedvr2 --restore_model 3b/7b
                    pf += ["--restore", "seedvr2", "--restore_model", restore.split("-")[-1]]
                if d.get("tile_feather") == "1":
                    pf += ["--tile_feather"]
                if d.get("interp_skip") == "1":
                    pf += ["--interp_skip_cuts"]
                base = re.sub(r"_enh\d+$", "", os.path.splitext(os.path.basename(job.out or "job"))[0])
                out_rel = f"outputs/{base}_enh{gen}.mp4"
                fout_rel = f"outputs/{base}_enh{gen}_frames"
                # collision-safe: the source's enh_gen never increments, so re-enhancing the same run
                # would silently OVERWRITE the earlier enhance (enhance.py wipes frames_out + the mp4).
                while os.path.exists(os.path.join(REPO, out_rel)) or os.path.exists(os.path.join(REPO, fout_rel)):
                    gen += 1
                    out_rel = f"outputs/{base}_enh{gen}.mp4"
                    fout_rel = f"outputs/{base}_enh{gen}_frames"
                cmd = [AD_PY, "-m", "scripts.enhance", "--frames", src_abs] + pf + [
                    "--frames_out", os.path.join(REPO, fout_rel),
                    "--out", os.path.join(REPO, out_rel), "--fps", str(src_fps)]
                ej = self.mgr.enqueue(f"▲ {(job.title or job.id)[:28]} (g{gen})", "enhance", cmd,
                                      dict(mode="enhance", steps=0, nseg=1, seconds=job.params.get("seconds"), out=out_rel, frames_dir=fout_rel,
                                           enh_gen=gen, fps=str(src_fps), fps_out=src_fps * int(d["interp"]),
                                           enh_interp=d["interp"], enh_upscale=d["upscale"], enh_face=d["face"],
                                           enh_deflicker=d.get("deflicker", "0"),
                                           enh_upmodel=d.get("upmodel", "realesrgan"), enh_interpeng=d.get("interp_engine", "rife"),
                                           enh_restore=restore,
                                           enh_tile_feather=d.get("tile_feather", "0"), enh_interp_skip=d.get("interp_skip", "0"),
                                           source_id=job.id, source_root=job.params.get("source_root", job.id),
                                           source_title=(job.title or "")))
                ej.cmd = cmd               # enhance runs in the AnimateDiff repo (absolute paths point home)
                ej.params["cwd"] = AD_REPO
                ej.save()
                self.query_one("#inspectinfo", Static).update(
                    f"[#9dffce]Queued enhance g{gen} — {ej.id}[/#9dffce]\n→ {out_rel}")
                self.query_one(TabbedContent).active = "tab-queue"

            self.push_screen(EnhanceOptsScreen(w0, h0, len(pngs), defaults), _go)

        elif b == "playbtn":
            jid = self._selected("#atable")
            if not jid:
                return
            self._play_job(jid)

        elif b == "playchildbtn":
            jid = self._selected("#reltable")
            if not jid:
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]Select a run in the RELATED RUNS table first.[/#ffcf5c]")
                return
            self._play_job(jid)

        elif b == "renamebtn":
            jid = self._selected("#atable")
            if not jid:
                return
            job = self.mgr.jobs.get(jid)
            if not job or jid == self.mgr.current:
                self.query_one("#inspectinfo", Static).update("[#ffcf5c]Can't rename a running run.[/#ffcf5c]")
                return
            cur = job.params.get("name") or os.path.splitext(os.path.basename(job.out or ""))[0] or (job.title or "")

            def _do_rename(new, jid=jid):
                if not new:
                    return
                ok, msg = self.mgr.rename(jid, new)
                self._asig = None      # title/dur changed -> rebuild the archive table
                if ok and self._insp_jid == jid:
                    self._render_inspect()
                elif not ok:
                    self.query_one("#inspectinfo", Static).update(f"[#ffcf5c]{msg}[/#ffcf5c]")
            self.push_screen(RenameScreen(cur), _do_rename)

        elif b == "deletebtn":
            jid = self._selected("#atable")
            if not jid:
                return
            if not self.mgr.deletable(jid):
                self.query_one("#inspectinfo", Static).update(
                    "[#ffcf5c]Can't delete a running or queued run — cancel/finish it first.[/#ffcf5c]")
                return
            job = self.mgr.jobs.get(jid)
            kids = self.mgr.enhance_children(jid)
            summary = (f"[b]{os.path.basename(job.out or job.id)}[/b]  ({_filesize(job.out)})\n"
                       f"[dim]{job.id}  ·  {job.status}[/dim]\n\n"
                       "Removes the mp4, its frames, the log, preview and checkpoint. "
                       "[b]Cannot be undone.[/b]")
            if kids:
                summary += f"\n\n[#ffcf5c]!! {len(kids)} enhanced version(s) from this run will be KEPT.[/#ffcf5c]"

            def _do_delete(ok, jid=jid):
                if not ok:
                    return
                if self.mgr.delete(jid):
                    self._asig = None
                    if self._insp_jid == jid:
                        self._insp_jid = None
                        self.query_one("#inspectinfo", Static).update("[dim]Run deleted.[/dim]")
            self.push_screen(ConfirmDeleteScreen(summary), _do_delete)

    # ---------- inspect details + terminal toggles ----------
    def on_data_table_row_highlighted(self, event):
        """T24: keep the inline QUEUE details panel in sync with the highlighted row -> a live,
        read-only view (mirrors how ARCHIVE refreshes its inspect on selection). Only the qtable
        drives this; everything else is a no-op. Fully guarded — must never break the app."""
        try:
            tid = event.data_table.id if getattr(event, "data_table", None) is not None else None
        except Exception:
            return
        if tid == "qtable":
            try:
                self._render_qinspect()
            except Exception:
                pass
        elif tid == "reltable":
            # T-thumb: the preview follows the highlighted related run, so replicates can be
            # compared frame-to-frame just by moving the cursor. Falls back to the inspected run.
            try:
                jid = self._selected("#reltable")
                job = self.mgr.jobs.get(jid) if jid else None
                self._render_insp_thumb(job or self.mgr.jobs.get(self._insp_jid))
            except Exception:
                pass

    def _render_qinspect(self):
        """T24: inline read-only details for the selected QUEUE row. Reuses the EXISTING
        _fmt_inspect(job) (config shown, results empty for a not-yet-run job). Guarded so it
        never breaks the tick loop / RowHighlighted. Adds a small 'queued — not run yet' note
        for queued (not suspended) jobs, without rewriting _fmt_inspect."""
        try:
            panel = self.query_one("#qinspect", Static)
        except Exception:
            return
        jid = self._selected("#qtable")
        job = self.mgr.jobs.get(jid) if jid else None
        if job is None:
            panel.update("[dim]Select a queued run above to see its full config.[/dim]")
            return
        try:
            body = self._fmt_inspect(job)
        except Exception:
            panel.update("[dim]could not read this run's config[/dim]")
            return
        note = ""
        try:
            if getattr(job, "status", "") == "queued":
                note = "[#ffcf5c]≡ queued — not run yet (config below; no results until it renders)[/#ffcf5c]\n\n"
            elif getattr(job, "status", "") == "suspended":
                note = ("[#ffcf5c]▽ suspended @ shot %s/%s — will resume from its checkpoint[/#ffcf5c]\n\n"
                        % (getattr(job, "seg", "?"), getattr(job, "nseg", "?")))
        except Exception:
            note = ""
        # _fmt_inspect returns a str -> safe to prepend the note.
        panel.update(note + body if isinstance(body, str) else body)

    def _render_inspect(self):
        """Repaint #inspectinfo with whichever archive view is active (inspect | timing)."""
        job = self.mgr.jobs.get(self._insp_jid)
        fn = self._fmt_provenance if self._insp_view == "timing" else self._fmt_inspect
        self.query_one("#inspectinfo", Static).update(fn(job))
        self._render_related_table(job)
        self._render_insp_thumb(job)

    def _render_related_table(self, job):
        """Populate #reltable with runs spawned from (or that spawned) `job` -- pairs, replicates,
        enhancements -- so they can be selected and played individually without leaving inspect."""
        t = self.query_one("#reltable", DataTable)
        rel = self._related_jobs(job) if job else []
        t.clear()
        for k, label in sorted(rel, key=lambda x: x[0].created):
            t.add_row(k.id, label, f"{_status_glyph(k.status)} {k.status}", os.path.basename(k.out or "?"), key=k.id)
        t.display = bool(rel)
        self.query_one("#playchildbtn", Button).display = bool(rel)

    def _thumb_art(self, job, cols=44):
        """T-thumb: opening-frame art for a run's output, cached as runs/thumbs/<id>.png so
        repeat inspects are instant. Extraction uses the bundled imageio-ffmpeg binary.
        Returns a renderable, or None (no output yet / extraction failed) — never raises."""
        try:
            out = getattr(job, "out", None)
            if not out:
                return None
            src = out if os.path.isabs(out) else os.path.join(REPO, out)
            if not os.path.exists(src):
                return None
            tdir = os.path.join(REPO, "runs", "thumbs")
            os.makedirs(tdir, exist_ok=True)
            thumb = os.path.join(tdir, f"{job.id}.png")
            if not os.path.exists(thumb) or os.path.getmtime(thumb) < os.path.getmtime(src):
                import imageio_ffmpeg
                ff = imageio_ffmpeg.get_ffmpeg_exe()
                r = subprocess.run(
                    [ff, "-y", "-i", src, "-frames:v", "1", "-vf", "scale=480:-2", thumb],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
                if r.returncode != 0 or not os.path.exists(thumb):
                    return None
            return render_preview(thumb, cols=cols)
        except Exception:
            return None

    def _render_insp_thumb(self, job):
        """T-thumb: show the opening frame beside RUN DETAILS so near-identical runs
        (especially replicates) can be told apart without opening the media player.
        Follows the highlighted #reltable row when one is selected."""
        try:
            pv = self.query_one("#inspthumb", Static)
        except Exception:
            return
        art = self._thumb_art(job) if job is not None else None
        if art is None:
            pv.display = False
            pv.update("")
        else:
            pv.styles.width = 46
            pv.display = True
            pv.update(art)

    def _blind_lookup(self, pair_id):
        """Look up a blind A/B pair's TRUE mapping in runs/pair_blinds.jsonl (the only in-the-clear copy).
        Returns the last matching record dict, or None."""
        if not pair_id:
            return None
        path = os.path.join(REPO, "runs", "pair_blinds.jsonl")
        rec = None
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("pair_id") == pair_id:
                        rec = d           # keep the last (most recent) match
        except FileNotFoundError:
            return None
        except Exception:
            return None
        return rec

    def _pair_rating(self, pair_id):
        """Return the LAST recorded rating for this pair from runs/pair_ratings.jsonl -> its dict
        ({'pair_id','winner','ts'}, winner = a job id or 'tie'), or None if never rated. Used post-REVEAL
        to map the user's picked video (recorded by job id) back to its variant + varied value."""
        if not pair_id:
            return None
        path = os.path.join(REPO, "runs", "pair_ratings.jsonl")
        rec = None
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("pair_id") == pair_id:
                        rec = d           # keep the last (most recent) rating
        except FileNotFoundError:
            return None
        except Exception:
            return None
        return rec

    def _fmt_inspect(self, job):
        if job is None:
            return "[dim]run not found[/dim]"
        p = job.params

        def row(k, v):
            return f"  {k:<12}{v}"

        # --- blind A/B: while blind + not yet revealed, hide the ONE varied dial's value + the variant ---
        _blind = bool(p.get("pair_blind")) and not p.get("pair_revealed")
        _varied = p.get("pair_varied_dial") if p.get("pair_id") else None
        _HIDDEN = "[hidden — blind A/B, REVEAL to show]"

        def brow(k, v, dial):
            """A SETTINGS row that hides its value iff this dial is the blind-varied one."""
            return row(k, _HIDDEN if (_blind and _varied == dial) else v)

        _KGLYPH = {"single": "▭", "chained": "▥", "director": "✦", "enhance": "▲"}
        L = [f"[b]{job.id}[/b]    {_status_glyph(job.status)} \[{job.status.upper()}]",
             time.strftime(f"[dim]{_KGLYPH.get(job.kind, '·')} {job.kind} · %b %d  %H:%M[/dim]",
                           time.localtime(job.finished or job.created)),
             "─" * 48,
             "[#6dffab]PROMPT[/#6dffab]", f"  {p.get('prompt') or job.title or '(none)'}"]
        if p.get("directive"):
            L += ["[#6dffab]DIRECTIVE[/#6dffab]", f"  {p['directive']}"]
        if p.get("anchors"):
            L += ["[#6dffab]ANCHORS[/#6dffab]", f"  {p['anchors']}"]
        if p.get("image"):
            L += ["[#6dffab]START IMAGE[/#6dffab]", f"  {p['image']}"]
        L += ["", "[#6dffab]SETTINGS[/#6dffab]"]
        if _blind and _varied == "steadiness":
            L.append(row("mode", f"{job.kind} · {_HIDDEN}" if p.get("steadiness") else job.kind))
        else:
            L.append(row("mode", job.kind + (f" · {p['steadiness']}" if p.get("steadiness") else "")))
        L += [row("resolution", p.get("res", "?")),
              row("length", f"{p.get('seconds', '?')}s")]
        if int(job.nseg or 1) > 1:
            if _blind and _varied == "seg":
                L.append(row("shots", f"{job.nseg}  ×  {_HIDDEN}"))
            else:
                L.append(row("shots", f"{job.nseg}  ×  {p.get('seg_sec', '?')}s each"))
        L += [brow("steps", p.get("steps", "?"), "steps"), brow("guidance", p.get("cfg", "?"), "cfg"),
              row("seed", p.get("seed", "?")), brow("fps", p.get("fps", "?"), "fps")]
        if _blind:
            L += ["", "[#6dffab]BLIND A/B[/#6dffab]",
                  row("status", "blind — variant + varied value hidden"),
                  row("varied", "[hidden] — press ↯ REVEAL (below) once both runs finish")]
        elif p.get("pair_blind") and p.get("pair_revealed") and p.get("pair_id"):
            bl = self._blind_lookup(p.get("pair_id"))
            _pv = p.get("pair_variant", "?")
            L += ["", "[#6dffab]A/B RESULT[/#6dffab]",
                  row("variant", _pv)]
            _vdial = (bl.get("varied") if bl else None) or p.get("pair_varied_dial") or "?"
            if bl:
                L.append(row("varied", f"{_vdial}   "
                                       f"A={bl.get('a_value', '?')} vs B={bl.get('b_value', '?')}"))
            else:
                L.append(row("varied", _vdial))
            # PICK: map the recorded rating (winner by JOB ID) -> variant + varied value, so the user
            # sees plainly which CONFIG the video they preferred was, without hopping slot->job->variant.
            rt = self._pair_rating(p.get("pair_id"))
            if rt is None:
                L.append(row("your pick", "not rated yet — use ≷ RATE PAIR, then this shows your winner"))
            elif rt.get("winner") == "tie":
                L.append(row("your pick", "you rated it a TIE (no preferred config)"))
            else:
                win_id = rt.get("winner")
                win_job = self.mgr.jobs.get(win_id)
                # variant of the winner: prefer its own recorded variant, else infer from the blind record.
                win_var = (win_job.params.get("pair_variant") if win_job else None)
                if not win_var and bl:
                    if win_id == bl.get("a_job_id"):
                        win_var = "A"
                    elif win_id == bl.get("b_job_id"):
                        win_var = "B"
                win_var = win_var or "?"
                other_var = "B" if win_var == "A" else ("A" if win_var == "B" else "?")
                if bl:
                    win_val = bl.get(f"{win_var.lower()}_value", "?") if win_var in ("A", "B") else "?"
                    other_val = bl.get(f"{other_var.lower()}_value", "?") if other_var in ("A", "B") else "?"
                    L.append(row("your pick", f"variant {win_var} = {_vdial}={win_val}"
                                              f"   (other = variant {other_var} = {other_val})"))
                else:
                    L.append(row("your pick", f"variant {win_var} (varied value unavailable)"))
        neg = p.get("n_prompt")
        if neg and neg != NEG:
            L += ["", "[#6dffab]NEGATIVE[/#6dffab]", f"  {neg}"]
        if job.kind == "enhance":
            modes = []
            if p.get("enh_interp") and str(p.get("enh_interp")) != "1":
                modes.append(f"interp {p.get('enh_interp')}x")
            if str(p.get("enh_deflicker")) == "1":
                modes.append("deflicker")
            _up = str(p.get("enh_upscale") or "0")
            _up = "4" if _up == "1" else _up        # legacy on-flag -> x4
            if _up not in ("0", ""):
                modes.append(f"upscale x{_up}")
            _fc = str(p.get("enh_face") or "0")
            _fc = "gfpgan" if _fc == "1" else _fc    # legacy on-flag
            if _fc not in ("0", ""):
                modes.append(f"face: {_fc}")
            _rs = str(p.get("enh_restore") or "none")
            if _rs != "none":
                modes.append(f"restore: {_rs}")
            if str(p.get("enh_tile_feather")) == "1":
                modes.append("feather tiles")
            if str(p.get("enh_interp_skip")) == "1":
                modes.append("skip cuts")
            L += ["", "[#6dffab]ENHANCED FROM[/#6dffab]",
                  row("source", p.get("source_title") or p.get("source_id", "?")),
                  row("generation", f"gen {p.get('enh_gen', 1)}"),
                  row("modes", "  ·  ".join(modes) if modes else "(not recorded)")]
            if p.get("source_root") and p.get("source_root") != p.get("source_id"):
                L.append(row("original", p.get("source_root")))
        L += ["", "[#6dffab]RESULT[/#6dffab]", row("runtime", fmt(job.elapsed())),
              row("shots", f"{job.seg or job.nseg} of {job.nseg} done"),
              row("output", os.path.basename(job.out or "?")), row("size", _filesize(job.out))]
        if job.out:
            out_abs = job.out if os.path.isabs(job.out) else os.path.join(REPO, job.out)
            L += ["", "[#6dffab]FILES[/#6dffab]",
                  row("linux", out_abs + ("" if os.path.exists(out_abs) else "  [#ffcf5c](missing)[/#ffcf5c]")),
                  row("windows", self._winpath(out_abs))]
            fd = p.get("frames_dir")
            if fd:
                fd_abs = fd if os.path.isabs(fd) else os.path.join(REPO, fd)
                if glob.glob(os.path.join(fd_abs, "*.png")):
                    L.append(row("frames", fd_abs))
                else:
                    L.append(row("frames", "[dim]No saved frames — ENHANCE to re-frame.[/dim]"))
        if job.error:
            L += ["", "[#ff6d6d]✕ ERROR[/#ff6d6d]", f"  {job.error[:240]}",
                  "  [dim]full trace → » TERMINAL[/dim]"]
        plans = getattr(job, "plans", None) or []
        if plans:
            L += ["", "[#6dffab]DIRECTOR'S NOTES[/#6dffab]"]
            dm = getattr(job, "dir_ms", None) or {}
            if dm:
                loads = [v[0] for v in dm.values()]; thinks = [v[1] for v in dm.values()]
                L.append("  [dim]per-seam cost: load {:.0f}s avg · think {:.1f}s avg · {} seams[/dim]"
                         .format(sum(loads) / len(loads) / 1000, sum(thinks) / len(thinks) / 1000, len(dm)))
            for entry in plans:
                seg, plan = int(entry[0]), entry[1]
                prompt = entry[2] if len(entry) > 2 else ""
                L.append(f"  [#9dffce]shot {seg + 1}[/#9dffce] — {plan or '…'}")
                c = _dir_cost_line(job, seg)
                if c:
                    L.append(f"      {c}")
                if prompt:
                    L.append(f"      [#ffcf5c]→[/#ffcf5c] {prompt}")
                if self._dir_raw:
                    r = _director_raw(job, seg)
                    if r and r.get("raw"):
                        L.append(f"      [dim]raw:[/dim] {r['raw'].strip()}")
        elif job.director:
            L += ["", "[#6dffab]LAST DIRECTOR PROMPT[/#6dffab]", f"  [#ffcf5c]{job.director}[/#ffcf5c]"]
        rel = self._related_jobs(job) if getattr(self, "mgr", None) else []
        if rel:
            L += ["", "[#6dffab]RELATED RUNS[/#6dffab]",
                  "  [dim]see the table below -- select one + ▶ PLAY SELECTED CHILD RUN.[/dim]"]
        L += ["", "[dim]◷ TIMING for provenance · » TERMINAL (or 't') for the raw log.[/dim]"]
        return "\n".join(L)

    def _related_jobs(self, job):
        """Every OTHER job related to this one: PAIR A/B partner, ×N REPLICATE siblings/source, and
        ENHANCE children/source. A run can now have several sibling output files (Q3's PAIR/REPLICATE) --
        this feeds both the RELATED RUNS pointer above and the #reltable list. Returns [(Job, label), ...]."""
        out = []
        pid = job.params.get("pair_id")
        if pid:
            if job.params.get("pair_blind") and not job.params.get("pair_revealed"):
                partner_label = "blind pair"        # hide the partner's A/B variant until REVEAL
            else:
                partner_variant = "A" if job.params.get("pair_variant") == "B" else "B"
                partner_label = f"pair {partner_variant}"
            out += [(j, partner_label) for j in self.mgr.jobs.values()
                    if j.id != job.id and j.params.get("pair_id") == pid]
        rsid = job.params.get("replicate_set_id")
        if rsid:      # this job IS a replicate -> pull in its source + sibling replicates
            out += [(j, "replicate source" if j.id == rsid else "replicate sibling")
                    for j in self.mgr.jobs.values()
                    if j.id != job.id and (j.id == rsid or j.params.get("replicate_set_id") == rsid)]
        else:         # this job might BE a replicate source
            out += [(j, "replicate") for j in self.mgr.jobs.values()
                    if j.id != job.id and j.params.get("replicate_set_id") == job.id]
        out += [(j, "enhanced") for j in self.mgr.enhance_children(job.id)]
        if job.kind == "enhance" and job.params.get("source_id"):
            src = self.mgr.jobs.get(job.params["source_id"])
            if src:
                out.append((src, "enhanced from"))
        seen, uniq = set(), []
        for j, label in out:
            if j.id not in seen:
                seen.add(j.id); uniq.append((j, label))
        return uniq

    def _fmt_provenance(self, job):
        if job is None:
            return "[dim]run not found[/dim]"
        p = job.params

        def row(k, v):
            return f"  {k:<13}{v}"

        def ts(t):
            return time.strftime("%b %d  %H:%M:%S", time.localtime(t)) if t else "—"

        L = [f"[b]{job.id}[/b]    {_status_glyph(job.status)} \[{job.status.upper()}]",
             f"[dim]{job.kind} · provenance[/dim]",
             "─" * 48,
             "[#6dffab]TIMELINE[/#6dffab]",
             row("created", ts(job.created)),
             row("started", ts(job.started)),
             row("finished", ts(job.finished))]
        if job.started and job.created:
            L.append(row("queued", f"{fmt(int(job.started - job.created))} wait"))
        L.append(row("runtime", fmt(job.elapsed())))
        psecs = getattr(job, "phase_secs", {}) or {}
        if psecs:
            L += ["", "[#6dffab]PHASES[/#6dffab]"]
            tot = sum(psecs.values()) or 1
            for ph, lbl in (("loading", "load"), ("warmup", "warm"), ("generating", "gen"),
                            ("decoding", "decode"), ("saving", "save")):
                t = psecs.get(ph, 0)
                if t > 0:
                    L.append(row(lbl, f"{fmt(int(t))}   [dim]{100 * t / tot:.0f}%[/dim]"))
        ssecs = getattr(job, "seg_secs", []) or []
        if ssecs:
            mean = sum(ssecs) / len(ssecs)
            L += ["", f"[#6dffab]SHOTS[/#6dffab]  [dim]{len(ssecs)} timed · {fmt(int(mean))} avg[/dim]"]
            for i, s in enumerate(ssecs, 1):
                bar = "█" * max(1, int(round(12 * s / (max(ssecs) or 1))))
                L.append(row(f"shot {i}", f"[#9dffce]{bar}[/#9dffce] {fmt(int(s))}"))
        res, ckpt = getattr(job, "resumes", 0), getattr(job, "last_ckpt_seg", 0)
        if res or ckpt:
            L += ["", "[#6dffab]RESILIENCE[/#6dffab]"]
            if res:
                L.append(row("resumes", f"{res}x after suspend"))
            if ckpt:
                L.append(row("checkpoint", f"shot {ckpt}"))
        sf = int(p.get("seg_frames") or 0)
        tf = int(p.get("total_frames") or (sf * int(job.nseg or 1) if sf else 0))
        L += ["", "[#6dffab]OUTPUT[/#6dffab]",
              row("frames", str(tf) if tf else "—"),
              row("size", _filesize(job.out)),
              row("file", os.path.basename(job.out or "—"))]
        L += ["", "[dim]« INSPECT for the creative summary · » TERMINAL for the raw log.[/dim]"]
        return "\n".join(L)

    def _toggle_live_term(self):
        w = self.query_one("#livelog")
        w.display = not w.display
        # constant label + lit -active style while open (a self-relabeling button reads as two
        # different controls; the lit state says "this is on" without renaming it)
        self.query_one("#livetermbtn", Button).set_class(w.display, "-active")

    def _toggle_arc_term(self):
        log = self.query_one("#inspectlog", RichLog)
        panel = self.query_one("#inspectpanel")
        show = not log.display
        log.display = show
        panel.display = not show
        self.query_one("#arctermbtn", Button).set_class(show, "-active")
        if show:
            log.clear()
            job = self.mgr.jobs.get(self._insp_jid)
            if job:
                for ln in job.tail[-400:]:
                    log.write(ln)

    def action_cycle_preview(self):
        """Cycle the preview glyph density (sextant -> quadrant -> half). Use if sextants
        show as boxes/tofu on an older Cascadia — quadrant is universally font-safe."""
        order = ["sextant", "quadrant", "half"]
        preview_art.PREVIEW_MODE = (order[(order.index(preview_art.PREVIEW_MODE) + 1) % 3]
                                    if preview_art.PREVIEW_MODE in order else "sextant")
        self._preview_mtime = 0.0    # force a re-render on the next tick
        self.notify(f"Preview style: {preview_art.PREVIEW_MODE}")

    def action_pick_theme(self):
        """T13: open the live-preview theme picker (Ctrl+K)."""
        self.push_screen(ThemePickerScreen())

    def action_toggle_dirraw(self):
        """Expand/collapse the director's FULL raw model output per seam (LIVE notes + ARCHIVE inspect)."""
        self._dir_raw = not self._dir_raw
        self.notify(f"Director raw output: {'ON' if self._dir_raw else 'off'}")
        tab = self.query_one(TabbedContent).active
        if tab == "tab-live":
            self._notes_n = 0
            self.query_one("#dirnotes", RichLog).clear()   # append-only log -> full repaint next tick
            try:
                self.query_one("#dirnotebtn", Button).label = "» HIDE RAW" if self._dir_raw else "» DIR RAW"
            except Exception:
                pass
        elif tab == "tab-arch":
            if self._insp_jid:
                self._render_inspect()

    def action_toggle_term(self):
        tab = self.query_one(TabbedContent).active
        if tab == "tab-live":
            self._toggle_live_term()
        elif tab == "tab-arch":
            self._toggle_arc_term()

    def action_form_queue(self):
        """Ctrl+Enter anywhere in the NEW RUN form -> QUEUE RUN, same as clicking the button.
        The form's TextAreas (prompt/anchors/directive/n_prompt) would otherwise swallow Ctrl+Enter
        as an indent; blur the focused field first so it commits its value before we read it."""
        if self.query_one(TabbedContent).active != "tab-new":
            return
        try:
            if self.focused is not None:
                self.focused.blur()
        except Exception:
            pass
        self._queue_current_run()

    def action_suspend(self):
        self.mgr.suspend()

    def action_resume(self):
        jid = self._selected("#qtable")
        if jid:
            self.mgr.resume_suspended(jid)


if __name__ == "__main__":
    Studio().run()
