#!/usr/bin/env python
"""LTX STUDIO - Pip-Boy themed job-runner dashboard.
Tabs: NEW RUN (configure+queue) / QUEUE / LIVE (watch+pause/cancel) / ARCHIVE (inspect+enhance).
Backend: studio_core.JobManager (persistent runs, queue, pause/resume/cancel).
Launch: ./studio.sh   (or ./venv/bin/python studio.py)
"""
import os, sys, time, json, subprocess, threading, glob, re, shutil
import gpu_budget
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from rich.markup import escape
from textual.binding import Binding
from textual.widgets import (Button, Footer, Input, Label, RichLog, Select, Static, Switch, TextArea,
                             TabbedContent, TabPane, DataTable, ProgressBar)
from textual.theme import Theme
from textual.screen import ModalScreen
from PIL import Image
from rich.text import Text
from rich.style import Style
from studio_core import JobManager, REPO

PIPBOY = Theme(
    name="pipboy",
    primary="#2fae5f",
    secondary="#1f9a52",
    accent="#6dffab",
    foreground="#34d977",
    background="#06120b",
    surface="#08160d",
    panel="#0a1c10",
    success="#9dffce",
    warning="#ffcf5c",
    error="#ff6d6d",
    dark=True,
    variables={
        "block-cursor-foreground": "#06120b",
        "block-cursor-background": "#6dffab",
        "footer-key-foreground": "#9dffce",
        "footer-description-foreground": "#34d977",
        "border": "#1c7a42",
    },
)

FP_PY = sys.executable
AD_REPO = "/home/wolve/video_gen/AnimateDiff"
AD_PY = os.path.join(AD_REPO, "venv/bin/python")
NEG = ("worst quality, inconsistent motion, blurry, jittery, distorted, low detail, "
       "deformed, malformed anatomy, missing or extra limbs, mutated, fused body, headless")
RES = {"512 x 320  fast": (512, 320), "704 x 480  balanced": (704, 480), "768 x 512  sharp": (768, 512)}

DIRECTOR_VENV_PY = "/home/wolve/video_gen/director_venv/bin/python"
PLANNER_SCRIPT = os.path.join(REPO, "vlm_planner.py")


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
except Exception:
    HELP = {}


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


def slugify(s, maxlen=40):
    """name -> filesystem-safe slug: lowercase, spaces->_, keep [a-z0-9-_], strip, cap length."""
    s = (s or "").strip().lower().replace(" ", "_")
    s = "".join(ch for ch in s if ch.isalnum() or ch in "-_")
    return s.strip("-_")[:maxlen].strip("-_")


# ---- dense sub-cell preview renderer (torch-free, PIL + Rich only) -----------
# Modes: "sextant" (2x3 px/cell, ~3x sharper), "quadrant" (2x2, universally font-safe),
# "half" (1x2, the original). Cell grid stays cols x rows in every mode, so the panel
# width math (cols+2) never changes. Sextants (U+1FB00..) need Cascadia 2404+, which
# Windows Terminal ships; we DEFAULT to sextant when $WT_SESSION says we're in WT, else
# quadrant. PREVIEW_MODE env or Ctrl+P (in-app) overrides if glyphs render as tofu.
PREVIEW_MODE = os.environ.get("PREVIEW_MODE", "").strip().lower()
if PREVIEW_MODE not in ("sextant", "quadrant", "half"):
    PREVIEW_MODE = "sextant"   # sharpest; if glyphs show as boxes (old Cascadia) press Ctrl+P -> quadrant

# quadrant mask (TL=1,TR=2,BL=4,BR=8) -> glyph
_QUAD = {0: " ", 1: "▘", 2: "▝", 3: "▀", 4: "▖", 5: "▌", 6: "▞", 7: "▛",
         8: "▗", 9: "▚", 10: "▐", 11: "▜", 12: "▄", 13: "▙", 14: "▟", 15: "█"}


def _sextant_char(m):
    """6-bit mask (pos1=top-left .. pos6=bottom-right, bit=2^(pos-1)) -> glyph.
    Masks 0/21/42/63 have dedicated codepoints outside the U+1FB00 run."""
    if m == 0:  return " "
    if m == 21: return "▌"   # U+258C left half  (positions 1,3,5)
    if m == 42: return "▐"   # U+2590 right half (positions 2,4,6)
    if m == 63: return "█"   # U+2588 full
    off = m - 1
    if m > 21: off -= 1
    if m > 42: off -= 1
    return chr(0x1FB00 + off)


def _two_color_cell(sub):
    """sub = list of (r,g,b) sub-pixels in row-major order. Returns (mask, fg, bg) via
    luminance-vs-mean 2-clustering (chafa-style mean colors). mask=-1 -> solid fg, 0 -> solid bg."""
    lum = [0.299 * r + 0.587 * g + 0.114 * b for (r, g, b) in sub]
    thr = sum(lum) / len(lum)
    mask = 0
    fr = fg_ = fb = br = bg_ = bb = nf = nb = 0
    for i, (r, g, b) in enumerate(sub):
        if lum[i] >= thr:
            mask |= (1 << i); fr += r; fg_ += g; fb += b; nf += 1
        else:
            br += r; bg_ += g; bb += b; nb += 1
    n = len(sub)
    if nf == 0:                       # genuinely flat -> solid bg
        bg = (sum(r for r, _, _ in sub) // n, sum(g for _, g, _ in sub) // n, sum(b for _, _, b in sub) // n)
        return 0, bg, bg
    if nb == 0:                       # genuinely flat -> solid fg
        fg = (fr // nf, fg_ // nf, fb // nf)
        return -1, fg, fg
    return mask, (fr // nf, fg_ // nf, fb // nf), (br // nb, bg_ // nb, bb // nb)


def render_preview(path, cols=48):
    """Render a preview PNG as truecolor sub-cell ANSI art. Cell grid is cols x rows; each cell
    packs 2x3 (sextant), 2x2 (quadrant) or 1x2 (half) sub-pixels with one fg + one bg color,
    chosen per-cell by luminance clustering (chafa algorithm). Torch-free (PIL + Rich), never
    raises -> Text() on error. Returns a rich.text.Text, drop-in for the original half-block one."""
    try:
        mode = PREVIEW_MODE
        sx, sy = {"sextant": (2, 3), "quadrant": (2, 2), "half": (1, 2)}.get(mode, (2, 3))
        im = Image.open(path).convert("RGB")
        w, h = im.size
        # 0.5 = terminal cell width/height; sets the on-screen aspect (same as the original
        # half-block path). Sub-pixel density (sx,sy) is independent of this — it only raises
        # resolution WITHIN each cell, so the cell grid (cols x rows) stays aspect-correct.
        rows = max(1, round(cols * (h / w) * 0.5))
        im = im.resize((cols * sx, rows * sy), Image.Resampling.LANCZOS)
        px = im.load()
        t = Text()
        for ry in range(rows):
            y0 = ry * sy
            for cx in range(cols):
                x0 = cx * sx
                if mode == "half":
                    tr, tg, tb = px[x0, y0]
                    br, bg, bb = px[x0, y0 + 1]
                    t.append("▀", Style(color=f"#{tr:02x}{tg:02x}{tb:02x}", bgcolor=f"#{br:02x}{bg:02x}{bb:02x}"))
                    continue
                sub = [px[x0 + dx, y0 + dy] for dy in range(sy) for dx in range(sx)]
                m, fg, bg = _two_color_cell(sub)
                if m == 0:
                    t.append(" ", Style(bgcolor=f"#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}"))
                elif m == -1:
                    t.append("█", Style(color=f"#{fg[0]:02x}{fg[1]:02x}{fg[2]:02x}"))
                else:
                    glyph = _sextant_char(m) if mode == "sextant" else _QUAD[m]
                    t.append(glyph, Style(color=f"#{fg[0]:02x}{fg[1]:02x}{fg[2]:02x}",
                                          bgcolor=f"#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}"))
            if ry != rows - 1:
                t.append("\n")
        return t
    except Exception:
        return Text()


def _status_glyph(status):
    return {"done": "[#9dffce]✓[/#9dffce]", "failed": "[#ff6d6d]✕[/#ff6d6d]",
            "cancelled": "[dim]■[/dim]", "interrupted": "[#ffcf5c]‖[/#ffcf5c]",
            "suspended": "[#ffcf5c]▽[/#ffcf5c]"}.get(status, "·")


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
    #consultbox { width: 86%; height: 88%; border: round #2fae5f; background: #06120b; padding: 1 2; }
    #consulttitle { color: #6dffab; text-style: bold; height: 1; }
    #consultsub { color: #1f9a52; height: 1; margin: 0 0 1 0; }
    #chatlog { height: 1fr; border: round #1c7a42; background: #050d08; color: #34d977; }
    #cfgpreview { height: auto; min-height: 5; max-height: 7; color: #ffcf5c; border: round #1c7a42; background: #08160d; padding: 0 1; margin: 1 0; }
    #consultstatus { height: 1; color: #9dffce; }
    #streampreview { height: auto; max-height: 8; color: #7dffb8; background: #04100a; border: round #134a2a; padding: 0 1; margin: 0 0 1 0; display: none; }
    #chatimg { border: tall #134a2a; background: #06120b; color: #7dffb8; }
    #chatimg:focus { border: tall #6dffab; }
    #chatmsg { height: 4; border: tall #134a2a; background: #06120b; color: #7dffb8; }
    #chatmsg:focus { border: tall #6dffab; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #applybtn { background: #134a2a; color: #9dffce; text-style: bold; }
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
    #chatbox { width: 86%; height: 88%; border: round #2fae5f; background: #06120b; padding: 1 2; }
    #chattitle { color: #6dffab; text-style: bold; height: 1; }
    #chatsub { color: #1f9a52; height: 1; margin: 0 0 1 0; }
    #rawlog { height: 1fr; border: round #1c7a42; background: #050d08; color: #34d977; }
    #rawstatus { height: 1; color: #9dffce; }
    #rawstream { height: auto; max-height: 8; color: #7dffb8; background: #04100a; border: round #134a2a; padding: 0 1; margin: 0 0 1 0; display: none; }
    #rawimg { border: tall #134a2a; background: #06120b; color: #7dffb8; }
    #rawimg:focus { border: tall #6dffab; }
    #rawmsg { height: 4; border: tall #134a2a; background: #06120b; color: #7dffb8; }
    #rawmsg:focus { border: tall #6dffab; }
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
            yield Static("⌨  CHAT WITH THE MODEL  (Qwen2.5-VL, raw)", id="chattitle")
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
    #eoptbox { width: 66; height: auto; border: round #2fae5f; background: #06120b; padding: 1 2; }
    #eopttitle { color: #6dffab; text-style: bold; height: 1; }
    #eoptsub { color: #1f9a52; height: auto; margin: 0 0 1 0; }
    #eopt_warn { height: auto; min-height: 1; color: #9dffce; margin: 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #eopt_run { background: #134a2a; color: #9dffce; text-style: bold; }
    .infobtn { width: 3; min-width: 3; border: none; background: #0a1c10; color: #6dffab; }
    .infobtn:hover { background: #134a2a; color: #9dffce; }
    #eopt_help { display: none; border: round #1c7a42; background: #08160d; color: #9dffce; padding: 0 1; margin: 1 0 0 0; height: auto; }
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
            yield Static("", id="eopt_warn")
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
            warn.update(f"[#ffcf5c]⚠ {msg} — heavy (of {self.ram_gb:.0f} GB), close to the limit.[/#ffcf5c]")
        elif out_w:
            warn.update(f"[#9dffce]{msg}[/#9dffce]")
        else:
            warn.update("")
        run.disabled = block

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
                              deflicker=self.query_one("#eopt_deflicker", Select).value))
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


class ConfirmDeleteScreen(ModalScreen):
    """Confirm a permanent delete. dismiss(True) deletes, dismiss(False)/escape cancels."""
    DEFAULT_CSS = """
    ConfirmDeleteScreen { align: center middle; background: $background 80%; }
    #delbox { width: 70; height: auto; border: round #ff6d6d; background: #120606; padding: 1 2; }
    #deltitle { color: #ff6d6d; text-style: bold; height: 1; }
    #delbody { height: auto; color: #ffb3b3; margin: 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #del_yes { background: #5a1212; color: #ffd6d6; text-style: bold; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, summary):
        super().__init__()
        self.summary = summary

    def compose(self) -> ComposeResult:
        with Vertical(id="delbox"):
            yield Static("✕  DELETE RUN — PERMANENT", id="deltitle")
            yield Static(self.summary, id="delbody")
            with Horizontal(classes="crow"):
                yield Button("✕ DELETE", id="del_yes")
                yield Button("↩ KEEP", id="del_no")

    def on_mount(self):
        self.query_one("#delbox").border_title = "« CONFIRM DELETE »"

    def on_button_pressed(self, e):
        self.dismiss(e.button.id == "del_yes")

    def action_close(self):
        self.dismiss(False)


class RenameScreen(ModalScreen):
    """Prefilled Input; dismiss(str) renames, dismiss(None)/escape cancels."""
    DEFAULT_CSS = """
    RenameScreen { align: center middle; background: $background 80%; }
    #renbox { width: 64; height: auto; border: round #2fae5f; background: #06120b; padding: 1 2; }
    #rentitle { color: #6dffab; text-style: bold; height: 1; }
    #rensub { color: #1f9a52; height: auto; margin: 0 0 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #ren_ok { background: #134a2a; color: #9dffce; text-style: bold; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, current):
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="renbox"):
            yield Static("✎  RENAME RUN", id="rentitle")
            yield Static("renames the title AND the output file (outputs/<slug>.mp4)", id="rensub")
            yield Input(value=self.current, id="ren_input")
            with Horizontal(classes="crow"):
                yield Button("✓ RENAME", id="ren_ok")
                yield Button("✕ CANCEL", id="ren_close")

    def on_mount(self):
        self.query_one("#renbox").border_title = "« RENAME »"
        self.query_one("#ren_input", Input).focus()

    def _submit(self):
        self.dismiss((self.query_one("#ren_input", Input).value or "").strip() or None)

    def on_input_submitted(self, e):
        self._submit()

    def on_button_pressed(self, e):
        if e.button.id == "ren_ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_close(self):
        self.dismiss(None)


class RerollScreen(ModalScreen):
    """Re-run a job with a NEW seed, everything else identical. dismiss({"seed": str}) re-rolls
    (empty/invalid seed -> randomize); dismiss(None)/escape cancels."""
    DEFAULT_CSS = """
    RerollScreen { align: center middle; background: $background 80%; }
    #rrbox { width: 64; height: auto; border: round #2fae5f; background: #06120b; padding: 1 2; }
    #rrtitle { color: #6dffab; text-style: bold; height: 1; }
    #rrsub { color: #1f9a52; height: auto; margin: 0 0 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #rr_ok { background: #134a2a; color: #9dffce; text-style: bold; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, summary, cur_seed=None):
        super().__init__()
        self.summary = summary
        self.cur_seed = cur_seed

    def compose(self) -> ComposeResult:
        with Vertical(id="rrbox"):
            yield Static("⟲  RE-ROLL RUN", id="rrtitle")
            yield Static(f"re-run '{self.summary}' with a NEW seed — everything else identical.\n"
                         f"leave the box empty to randomize (current seed: {self.cur_seed}).", id="rrsub")
            yield Input(placeholder="optional: a specific seed (empty = random)", id="rr_seed")
            with Horizontal(classes="crow"):
                yield Button("⟲ RE-ROLL", id="rr_ok")
                yield Button("✕ CANCEL", id="rr_close")

    def on_mount(self):
        self.query_one("#rrbox").border_title = "« RE-ROLL »"
        self.query_one("#rr_seed", Input).focus()

    def _submit(self):
        self.dismiss({"seed": (self.query_one("#rr_seed", Input).value or "").strip()})

    def on_input_submitted(self, e):
        self._submit()

    def on_button_pressed(self, e):
        if e.button.id == "rr_ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_close(self):
        self.dismiss(None)


class Studio(App):
    TITLE = "LTX STUDIO"
    CSS = """
    Screen { background: #06120b; color: #34d977; }
    #topbar { dock: top; height: 1; background: #0a1c10; }
    #topbartitle { width: 1fr; color: #6dffab; content-align: center middle; text-style: bold; }
    #statusmeter { width: 22; content-align: right middle; text-style: bold; padding: 0 1 0 0; }
    Tabs { background: #0a1c10; }
    Tab { color: #1f9a52; }
    Tab.-active { color: #9dffce; text-style: bold; }
    TabbedContent { height: 1fr; }
    TabbedContent > Tabs { dock: top; }
    .sec { color: #6dffab; text-style: bold; border-bottom: dashed #1c7a42; margin: 1 0 0 0; }
    .lbl { width: 13; color: #34d977; content-align: left middle; }
    .row { width: 1fr; height: 3; }
    .row Input, .row Select { width: 1fr; }
    .row.tarow { height: 5; }
    .row.tarow .lbl { content-align: left top; }
    TextArea.ta { width: 1fr; height: 5; border: tall #134a2a; background: #06120b; color: #7dffb8; }
    TextArea.ta:focus { border: tall #6dffab; }
    Input { border: tall #134a2a; background: #06120b; color: #7dffb8; }
    Input:focus { border: tall #6dffab; }
    Select, Switch { background: #06120b; }
    #form { width: 52; padding: 0 2 0 1; }
    #newinfo { width: 1fr; border: round #1c7a42; background: #08160d; padding: 0 1; margin: 0 1 0 2; }
    #runest { padding: 1 0 0 0; }
    Button { border: tall #1c7a42; background: #0a1c10; color: #7dffb8; }
    Button:hover { background: #134a2a; }
    #queuebtn, #consultbtn { margin-top: 1; }
    DataTable { background: #050d08; color: #34d977; border: round #1c7a42; height: 1fr; }
    DataTable > .datatable--cursor { background: #134a2a; color: #9dffce; }
    DataTable > .datatable--header { background: #0a1c10; color: #6dffab; }
    ProgressBar { margin: 1 1; }
    Bar > .bar--bar { color: #2fae5f; }
    Bar > .bar--complete { color: #9dffce; }
    RichLog { border: round #1c7a42; background: #050d08; color: #34d977; height: 1fr; }
    #livehdr { color: #6dffab; text-style: bold; padding: 1 1 1 1; border-bottom: dashed #134a2a; }
    #livephase { color: #9dffce; padding: 0 1; height: 1; }
    #progtext { color: #34d977; padding: 0 1; height: 1; }
    #livemid { height: 1fr; min-height: 14; margin: 1 0 0 0; }
    #preview { width: 50; height: 1fr; border: round #1c7a42; background: #050d08; content-align: center middle; }
    #notescol { width: 1fr; height: 1fr; }
    #director { color: #ffcf5c; padding: 0 1; height: auto; max-height: 4; }
    #dirnotes { border: round #1c7a42; background: #050d08; color: #34d977; height: 1fr; }
    .strip { height: 2; margin: 0; padding: 0 1; border-top: dashed #134a2a; }
    .strip Static { width: 1fr; height: 1; color: #34d977; content-align: left middle; }
    #ph_timeline { width: 1fr; }
    #livelog { display: none; height: 8; }
    #livebar { height: 1; background: #0a1c10; color: #6dffab; content-align: left middle; padding: 0 1; }
    #inspectpanel { border: round #1c7a42; background: #08160d; height: 1fr; padding: 0 1; }
    #inspectinfo { color: #34d977; }
    #inspectlog { display: none; }
    #status { height: 2; border-top: solid #1c7a42; background: #0a1c10; color: #6dffab; content-align: left middle; }
    .actions { height: 3; }
    .actions Button { margin-right: 1; }
    """
    BINDINGS = [("ctrl+c", "quit", "Quit"), ("t", "toggle_term", "Terminal"),
                ("d", "toggle_dirraw", "Dir raw"),
                ("s", "suspend", "Suspend"), ("r", "resume", "Resume"),
                ("ctrl+p", "cycle_preview", "Preview")]

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
                        yield field("PROMPT", TextArea("", id="prompt", soft_wrap=True, tab_behavior="focus", classes="ta"))
                        yield field("IMAGE", Input(placeholder="input/start.png (optional)", id="image"))
                        yield Static("▌ CHAINING", classes="sec")
                        yield field("SEGMENT s", Input(value="3", id="seg"))
                        yield field("CONTINUITY", Input(value="1.0", id="cond_strength"))
                        yield field("ANCHORS", TextArea("", id="anchors", soft_wrap=True, tab_behavior="focus", classes="ta"))
                        yield Static("▌ DIRECTOR", classes="sec")
                        yield field("DIRECTIVE", TextArea("", id="directive", soft_wrap=True, tab_behavior="focus", classes="ta"))
                        yield field("STEADINESS", Select([("Hold (faithful)", "hold"), ("Balanced", "balanced"), ("Evolve (journey)", "evolve")], value="hold", id="steadiness", allow_blank=False))
                        yield Static("▌ QUALITY", classes="sec")
                        yield field("BACKEND", Select([("LTX-2B (fast)", "ltx"), ("Wan-VACE-1.3B (nicer, slower)", "wan"), ("Wan turbo (4-step distill)", "wan-turbo")], value="ltx", id="backend", allow_blank=False))
                        yield field("RES", Select([(k, k) for k in RES], value="704 x 480  balanced", id="res", allow_blank=False))
                        yield field("LENGTH s", Input(value="4", id="seconds"))
                        yield field("STEPS", Input(value="40", id="steps"))
                        yield field("GUIDANCE", Input(value="3.0", id="cfg"))
                        yield field("NEG", TextArea(NEG, id="n_prompt", soft_wrap=True, tab_behavior="focus", classes="ta"))
                        yield field("SEED", Input(value="0", id="seed"))
                        yield field("FPS", Input(value="24", id="fps"))
                        yield Static("▌ OUTPUT", classes="sec")
                        yield field("NAME", Input(placeholder="optional — file name (else job_HHMMSS)", id="name"))
                        yield Static("", id="runest")
                        yield Button("✎ CONSULT THE DIRECTOR", id="consultbtn")
                        yield Button("⌨ CHAT WITH THE MODEL", id="chatbtn")
                        yield Button("▶ QUEUE RUN", id="queuebtn")
                    yield Static(INFO, id="newinfo")
            with TabPane("≡ QUEUE", id="tab-queue"):
                yield DataTable(id="qtable", cursor_type="row")
                with Horizontal(classes="actions"):
                    yield Button("▶ RESUME", id="qresumebtn")
                    yield Button("↑ PROMOTE", id="promotebtn")
                    yield Button("× REMOVE SELECTED", id="removebtn")
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
                yield RichLog(id="livelog", highlight=True, markup=False, wrap=True)
                yield Static("", id="livebar")
            with TabPane("✓ ARCHIVE", id="tab-arch"):
                yield DataTable(id="atable", cursor_type="row")
                with Horizontal(classes="actions"):
                    yield Button("▶ PLAY", id="playbtn")
                    yield Button("» INSPECT", id="inspectbtn")
                    yield Button("⏱ TIMING", id="timingbtn")
                    yield Button("» TERMINAL", id="arctermbtn")
                with Horizontal(classes="actions"):
                    yield Button("⧉ CLONE", id="clonebtn")
                    yield Button("⟲ RE-ROLL", id="rerollbtn")
                    yield Button("▲ ENHANCE", id="enhancebtn")
                    yield Button("✎ RENAME", id="renamebtn")
                    yield Button("✕ DELETE", id="deletebtn")
                with VerticalScroll(id="inspectpanel"):
                    yield Static("[dim]Select a run above and press INSPECT.[/dim]", id="inspectinfo")
                yield RichLog(id="inspectlog", highlight=True, markup=False, wrap=True)
        yield Static("", id="status")
        yield Footer()

    def on_mount(self):
        self.register_theme(PIPBOY)
        self.theme = "pipboy"
        self.query_one("#qtable", DataTable).add_columns("id", "title", "mode", "status")
        self.query_one("#atable", DataTable).add_columns("id", "title", "status", "started", "finished", "gen", "dur")
        self.query_one("#livelog", RichLog).border_title = "« RAW TERMINAL »"
        self.query_one("#inspectlog", RichLog).border_title = "« RAW TERMINAL »"
        self.query_one("#inspectpanel").border_title = "« RUN DETAILS »"
        self.query_one("#preview", Static).border_title = "« LAST FRAME »"
        self.query_one("#dirnotes", RichLog).border_title = "« DIRECTOR'S NOTES »"
        self.query_one("#pacestrip").border_title = "« PACE »"
        self.query_one("#steerstrip").border_title = "« STEERING »"
        self.query_one("#phasestrip").border_title = "« PHASES »"
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

    def v(self, wid):
        w = self.query_one(f"#{wid}")
        return w.text if isinstance(w, TextArea) else w.value

    def on_descendant_focus(self):
        wid = getattr(self.focused, "id", None)
        if wid in HELP:
            self.query_one("#newinfo", Static).update(HELP[wid])

    # any dial change -> refresh the plan estimate
    def on_input_changed(self, event):
        self.update_est()

    def on_select_changed(self, event):
        if getattr(event.select, "id", None) == "backend":   # ONLY a backend change may retune cfg/steps;
            self._sync_cfg_default()                         # RES/MODE/etc must never clobber tuned dials
        self.update_est()

    def _sync_cfg_default(self):
        """Retarget GUIDANCE to the selected backend's sweet spot (LTX ~3.0 / Wan ~5.0 / Wan-turbo 1.0)
        when BACKEND changes, but ONLY if it is still on a known default -> a cfg you tuned is never clobbered.
        Wan-turbo is a CFG-distilled few-step LoRA: it REQUIRES cfg 1.0, so also default STEPS down to 6."""
        bk = self.v("backend") or "ltx"
        want = {"ltx": "3.0", "wan": "5.0", "wan-turbo": "1.0"}.get(bk, "3.0")
        try:
            cfg_in = self.query_one("#cfg", Input)
            if (cfg_in.value or "").strip() in ("", "1", "3", "5", "1.0", "3.0", "5.0"):
                cfg_in.value = want
        except Exception:
            pass
        if bk == "wan-turbo":                      # the distill is built for few steps; nudge off a heavy default
            try:
                steps_w = self.query_one("#steps")
                if str(getattr(steps_w, "value", "")).strip() in ("", "20", "30", "40", "50"):
                    steps_w.value = "6"
            except Exception:
                pass

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

    # ---------- live polling ----------
    def _meter(self):
        """Compact whole-studio status for the top-right corner — render / queue / director-on-CPU at
        a glance. Derived ONLY from our own job + daemon state (never nvidia-smi: it crashes this WSL
        GPU passthrough). The verbose twin is the bottom #status bar."""
        m, c = self.mgr, self.consult
        nq = len(m.queued())
        on_cpu = c.alive() and getattr(c, "cpu_mode", False)
        if m.active() is not None:
            seg = ("[#ffcf5c]‖ PAUSED[/#ffcf5c]" if m.paused
                   else "[#9dffce]%s RENDER[/#9dffce]" % self.SPIN[self._beat % len(self.SPIN)])
        elif c.alive() and not on_cpu:
            seg = "[#ffcf5c]✎ DIRECTOR[/#ffcf5c]"
        else:
            seg = "[#2fae5f]○ idle[/#2fae5f]"
        if nq:
            seg += "  [#6dffab]≡%d[/#6dffab]" % nq
        if on_cpu:
            seg += "  [#ffcf5c]✎CPU[/#ffcf5c]"
        return seg

    def tick(self):
        m = self.mgr
        self._beat += 1
        # GPU STATUS — from our OWN job state ONLY, never nvidia-smi. REPRODUCED: nvidia-smi polling
        # in this WSL2 (RTX 5070 Blackwell + driver 591.74) destabilizes the dxg passthrough and
        # restarts the WHOLE VM after ~30 calls, EVEN WITH NO CUDA RUNNING. So there is no live VRAM%
        # meter — we show working/idle from whether a run or the consult daemon is up.
        self._gpu_str = ("[#ffcf5c]GPU ● working[/#ffcf5c]" if self._cuda_busy()
                         else "[#2fae5f]GPU ○ idle[/#2fae5f]")
        # CONSULT daemon is loaded only while its screen is open (it warms/frees itself there).
        # Here we just reclaim the GPU from it the moment a run starts.
        if m.active() is not None and self.consult.alive() and not self.consult.cpu_mode:
            self.consult.kill()        # reclaim the GPU — but NOT a CPU-bound consult (it isn't on the GPU)
        q, a, d, s = m.counts()
        st = "PAUSED" if m.paused else ("RUNNING" if a else "idle")
        self.query_one("#status", Static).update(
            f"  ▌ QUEUED {q}    ▶ {st}    ✓ DONE {d}    ▽ SUSP {s}     │     {self._gpu_str}")
        self.query_one("#statusmeter", Static).update(self._meter())
        # queue + archive tables — rebuilt only when content changes (cursor stays put; no rubber-band)
        qrows = [(j.id, j.id, (j.title or j.params.get("prompt", ""))[:34], j.kind, "queued")
                 for j in m.queued()]
        qrows += [(j.id, j.id, (j.title or j.params.get("prompt", ""))[:34], j.kind,
                   f"SUSPENDED @ shot {j.seg}/{j.nseg}") for j in m.suspended()]
        _dt = lambda ts: time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "—"
        arows = [(j.id, j.id, (("▲ " if j.kind == "enhance" and not (j.title or "").startswith("▲") else "") + (j.title or ""))[:30], j.status, _dt(j.started), _dt(j.finished), fmt(j.elapsed()), _vidlen(j)) for j in m.archived()]
        self._sync_table("#qtable", qrows, "_qsig")
        self._sync_table("#atable", arows, "_asig")
        # live
        job = m.active()
        hdr = self.query_one("#livehdr", Static)
        over = self.query_one("#overbar", ProgressBar)
        live = self.query_one("#livelog", RichLog)
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
            return
        tag = "‖ PAUSED" if m.paused else "▶ RUNNING"
        hdr.update(f"[b]{tag}[/b]   {(job.title or job.params.get('prompt', ''))[:48]}")
        loading = job.is_loading() if hasattr(job, "is_loading") else False
        if loading:
            over.update(total=max(1, getattr(job, "load_total", 0) or 1),
                        progress=getattr(job, "load_step", 0))
            self.query_one("#progtext", Static).update(
                f"{getattr(job, 'load_step', 0)}/{getattr(job, 'load_total', 0)}   ·   {getattr(job, 'load_msg', '')}")
        else:
            over.update(total=100, progress=job.pct())
            self.query_one("#progtext", Static).update(
                f"shot {job.seg} of {job.nseg}   ·   step {job.step} of {job.nstep}   ·   {job.pct()}% overall")
        self.query_one("#livephase", Static).update(self._phase(job, m.paused))
        now_painting = job.director or job.params.get("prompt", "")
        self.query_one("#director", Static).update(
            ("[dim]this shot →[/dim] " + now_painting) if now_painting else "")
        # live frame preview + director's notes (both reset on job change)
        pv = self.query_one("#preview", Static)
        notes = self.query_one("#dirnotes", RichLog)
        if job.id != self._preview_id:
            self._preview_id, self._preview_mtime, self._notes_n = job.id, 0.0, 0
            pv.update(Text())
            notes.clear()
        ppath = getattr(job, "preview", None)
        if ppath and os.path.exists(ppath):
            mt = os.path.getmtime(ppath)
            w = self.size.width
            cols = 96 if w >= 150 else (72 if w >= 120 else 48)   # bigger preview on wide terminals
            if mt != self._preview_mtime or cols != self._preview_cols or PREVIEW_MODE != self._preview_mode:
                self._preview_mtime, self._preview_cols, self._preview_mode = mt, cols, PREVIEW_MODE
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
        # step-rate ETA that excludes load time
        first_ts = getattr(job, "first_step_ts", None)
        if first_ts and job.step > 0 and job.nstep:
            base_seg = getattr(job, "first_step_seg", 1) or 1
            timed = (job.seg - base_seg) * job.nstep + job.step   # steps actually timed in THIS process
            steps_done = (job.seg - 1) * job.nstep + job.step
            steps_total = job.nseg * job.nstep
            rate = (time.time() - first_ts) / max(1, timed)
            eta_secs = int(rate * max(0, steps_total - steps_done))
            eta = f"~{fmt(eta_secs)} left"
        elif loading:
            eta = "loading..."
        else:
            eta = "measuring..."
        p = job.params
        self.query_one("#livebar", Static).update(
            f"  t+{fmt(el)} elapsed   ·   {eta}      {p.get('res', '')}   {p.get('steps', '')} steps   "
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
            self.query_one("#pace_eta", Static).update(f"[dim]left[/dim] ~{fmt(int(mean * max(0, job.nseg - job.seg)))}")
        elif seg_t0 and not loading:   # no completed shot yet -> show the current shot ticking
            self.query_one("#pace_shot", Static).update(f"[dim]this shot[/dim] {fmt(int(time.time() - seg_t0))}")
            self.query_one("#pace_eta", Static).update(f"[dim]left[/dim] {eta}")
        else:
            self.query_one("#pace_shot", Static).update("[dim]per shot[/dim] ~measuring")
            self.query_one("#pace_eta", Static).update(f"[dim]left[/dim] {eta}")
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

    def _plan(self):
        """(W,H,fps,total_frames,seg_frames,nseg,chain) - length auto-chains; resolution is the hard cap."""
        W, H = RES[self.v("res")]
        wan = (self.v("backend") or "ltx") in ("wan", "wan-turbo")   # Wan/turbo render at native 16fps on a //4 grid
        if wan:
            # mirror WanBackend.dims(): Wan upscales the short side to >=480 (//32). Plan with the REAL
            # render dims or the SAFE_PX segment budget + estimate are computed ~2x too generous at 512x320.
            short = min(W, H)
            if short < 480:
                s = 480.0 / short
                W = max(32, round(W * s / 32) * 32)
                H = max(32, round(H * s / 32) * 32)
        fps = 16 if wan else max(1, int(float(self.v("fps"))))
        q = (lambda n: ((max(1, n) - 1) // 4) * 4 + 1) if wan else (lambda n: (max(1, n) // 8) * 8 + 1)
        r = round if wan else int          # match each backend's to_frames() rounding EXACTLY, or the
        total_frames = max(9, q(r(float(self.v("seconds")) * fps)))   # backend plans one more segment than
        safe = max(9, q(int(self.SAFE_PX / (W * H))))       # the studio -> phantom-seg suspend zombie
        if self.v("mode") == "director":
            seg_frames = min(q(r(float(self.v("seg")) * fps)), safe)
        else:
            seg_frames = min(total_frames, safe)
        seg_frames = max(9, seg_frames)
        chain = total_frames > seg_frames
        overlap = min(9, seg_frames - 8)                    # matches director.py
        nseg = (1 + -(-(total_frames - seg_frames) // max(1, seg_frames - overlap))) if chain else 1
        return W, H, fps, total_frames, seg_frames, nseg, chain

    def build(self):
        W, H, fps, total_frames, seg_frames, nseg, chain = self._plan()
        seg_sec = round(seg_frames / fps, 2)
        slug = slugify(self.v("name")) or ("job_" + time.strftime("%H%M%S"))
        if os.path.exists(os.path.join(REPO, f"outputs/{slug}.mp4")):   # collision-safe
            slug = f"{slug}_{time.strftime('%H%M%S')}"
            base, n = slug, 2
            while os.path.exists(os.path.join(REPO, f"outputs/{slug}.mp4")):
                slug = f"{base}-{n}"; n += 1
        out, fdir = f"outputs/{slug}.mp4", f"outputs/{slug}_frames"
        prompt = (self.v("prompt") or "").strip()
        neg = (self.v("n_prompt") or "").strip() or NEG
        director = self.v("mode") == "director"
        backend = self.v("backend") or "ltx"
        steps_s = self.v("steps")
        if backend == "wan-turbo":
            try:                                             # the distill runs few-step; clamp at the SOURCE so
                steps_s = str(min(int(float(steps_s)), 8))   # [[STEP]] totals / previews / ETA all stay honest
            except Exception:
                steps_s = "6"
        common = ["--steps", steps_s, "--cfg", self.v("cfg"), "--seed", self.v("seed"),
                  "--width", str(W), "--height", str(H), "--fps", str(fps), "--out", out, "--frames_dir", fdir]
        img = (self.v("image") or "").strip()
        if chain or director or backend in ("wan", "wan-turbo"):     # chained gen; Wan always routes through director.py
            cmd = [FP_PY, "director.py", "--prompt", prompt, "--n_prompt", neg,
                   "--total", self.v("seconds"), "--seg", str(seg_sec),
                   "--cond_strength", (self.v("cond_strength") or "1.0"), "--backend", backend] + common
            if backend == "ltx":
                cmd += ["--latent_chain"]                   # LTX-only (Wan has no latent-chain equivalent)
            anchors = (self.v("anchors") or "").strip()
            if anchors:                                     # style leash applies to ANY chained run, not just director
                cmd += ["--anchors", anchors]
            if director:
                cmd += ["--vlm", "--directive", (self.v("directive") or prompt),
                        "--steadiness", self.v("steadiness")]
            if img:
                cmd += ["--image", img]
            title = ((self.v("directive") if director else "") or prompt)[:40]
        else:                                               # short enough to fit one clip
            cmd = [FP_PY, "run_ltx.py", "--prompt", prompt, "--n_prompt", neg, "--seconds", self.v("seconds")] + common
            if img:
                cmd += ["--image", img]
            title = prompt[:40]
        kind = "director" if director else ("chained" if chain else "single")
        params = dict(
            mode=kind, prompt=prompt, steps=steps_s, cfg=self.v("cfg"), seed=self.v("seed"),
            fps=str(fps), seconds=self.v("seconds"), seg_sec=(seg_sec if (chain or director) else ""),
            res=self.v("res"), width=W, height=H, nseg=nseg, seg_frames=seg_frames, total_frames=total_frames,
            directive=(self.v("directive") if director else ""), anchors=(self.v("anchors") or ""),
            steadiness=(self.v("steadiness") if director else ""),
            image=img, out=out, frames_dir=fdir, name=slug, n_prompt=neg, backend=backend,
            cond_strength=(self.v("cond_strength") or "1.0"),
        )
        return title, kind, cmd, params

    def _apply_config(self, c):
        """Fill the NEW RUN form from a config dict keyed by FORM FIELD IDS. Shared by CONSULT + CLONE."""
        if not c:
            return
        for wid in ("name", "prompt", "directive", "anchors", "image", "seconds", "seg",
                    "steps", "cfg", "seed", "fps", "n_prompt", "backend", "cond_strength"):
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
            # Wan-VACE is much heavier per step + a multi-minute decode (and runs at 16fps internally);
            # the per-seam director is now the A1 resident CPU daemon (~90s/seam: no reload, no GPU
            # eviction; was ~300 for the per-seam GPU reload). Backend-independent.
            if (self.v("backend") or "ltx") in ("wan", "wan-turbo"):
                LOAD, COEF, WARM, DECODE, SEAM, SEG_REF = 40, 3.5, 60, 120, 90, 29
            else:
                LOAD, COEF, WARM, DECODE, SEAM, SEG_REF = 150, 1.5, 70, 20, 90, 49
            ff = seg_frames / SEG_REF              # per-shot gen + decode scale with frame count -> fps/seg now move the ETA
            nseam = max(0, nseg - 1)
            if director and (self.v("steadiness") or "hold") != "evolve":
                nseam = -(-nseam // 3)             # hold/balanced redirect every 3rd seam (director.py cadence)
            secs = (LOAD + nseg * (steps * COEF * px * ff + WARM + DECODE * ff)
                    + (SEAM * nseam if director else 0))
            mode = "DIRECTOR" if director else ("auto-chained" if chain else "single clip")
            warn = "  [b]!! lower resolution[/b]" if seg_frames < 17 else ""
            est.update(f"[#ffcf5c]plan: {nseg} shot(s) x {round(seg_frames / fps, 1)}s = {round(total_frames / fps, 1)}s  ::  {mode}  ::  ~{fmt(secs)}{warn}[/#ffcf5c]")
        except Exception:
            est.update("[dim]plan: enter numbers[/dim]")

    def _winpath(self, linux_abs):
        r"""WSL abs path -> \\wsl.localhost UNC, pasteable into Windows Explorer."""
        distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
        return r"\\wsl.localhost" + "\\" + distro + linux_abs.replace("/", "\\")

    def _selected(self, table_id):
        t = self.query_one(table_id, DataTable)
        if t.row_count == 0:
            return None
        try:
            return t.get_row_at(t.cursor_row)[0]
        except Exception:
            return None

    def _queue_current_run(self):
        """Build the NEW RUN form into a job and enqueue it. Shared by QUEUE RUN + RE-ROLL.
        Returns the queued Job, or None if blocked (empty prompt / GPU budget)."""
        if not (self.v("prompt") or "").strip():
            self.query_one("#newinfo", Static).update("[#ffcf5c]Enter a PROMPT first.[/#ffcf5c]")
            return None
        try:
            title, kind, cmd, params = self.build()
        except Exception as ex:   # bad numbers must never crash the app (a crash kills a live render)
            self.query_one("#newinfo", Static).update(
                f"[#ff6d6d]Can't plan this run — check LENGTH / FPS / SEGMENT / STEPS are numbers ({type(ex).__name__}).[/#ff6d6d]")
            return None
        # GPU-budget gate: budget_ok() runs nvidia-smi, so ONLY probe when the board is idle
        # (nvidia-smi during live CUDA crashes the WSL VM). An active run -> this just queues behind it.
        if not self._cuda_busy():
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

    def on_button_pressed(self, e: Button.Pressed):
        b = e.button.id
        if b == "queuebtn":
            self._queue_current_run()
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
                            face="gfpgan", deflicker="0")

            def _go(d):
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
                summary += f"\n\n[#ffcf5c]⚠ {len(kids)} enhanced version(s) from this run will be KEPT.[/#ffcf5c]"

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
    def _render_inspect(self):
        """Repaint #inspectinfo with whichever archive view is active (inspect | timing)."""
        job = self.mgr.jobs.get(self._insp_jid)
        fn = self._fmt_provenance if self._insp_view == "timing" else self._fmt_inspect
        self.query_one("#inspectinfo", Static).update(fn(job))

    def _fmt_inspect(self, job):
        if job is None:
            return "[dim]run not found[/dim]"
        p = job.params

        def row(k, v):
            return f"  {k:<12}{v}"

        _KGLYPH = {"single": "▭", "chained": "▥", "director": "✦", "enhance": "▲"}
        L = [f"[b]{job.id}[/b]    {_status_glyph(job.status)} [{job.status.upper()}]",
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
        L += ["", "[#6dffab]SETTINGS[/#6dffab]",
              row("mode", job.kind + (f" · {p['steadiness']}" if p.get("steadiness") else "")),
              row("resolution", p.get("res", "?")),
              row("length", f"{p.get('seconds', '?')}s")]
        if int(job.nseg or 1) > 1:
            L.append(row("shots", f"{job.nseg}  ×  {p.get('seg_sec', '?')}s each"))
        L += [row("steps", p.get("steps", "?")), row("guidance", p.get("cfg", "?")),
              row("seed", p.get("seed", "?")), row("fps", p.get("fps", "?"))]
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
                L.append(row("frames", fd if os.path.isabs(fd) else os.path.join(REPO, fd)))
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
        kids = []
        if getattr(self, "mgr", None):
            kids = [j for j in self.mgr.jobs.values()
                    if j.params.get("source_id") == job.id and j.kind == "enhance"]
        if kids:
            L += ["", "[#6dffab]ENHANCEMENTS[/#6dffab]"]
            for k in sorted(kids, key=lambda j: j.created):
                L.append(row("→", f"{_status_glyph(k.status)} {os.path.basename(k.out or '')}"))
        L += ["", "[dim]⏱ TIMING for provenance · » TERMINAL (or 't') for the raw log.[/dim]"]
        return "\n".join(L)

    def _fmt_provenance(self, job):
        if job is None:
            return "[dim]run not found[/dim]"
        p = job.params

        def row(k, v):
            return f"  {k:<13}{v}"

        def ts(t):
            return time.strftime("%b %d  %H:%M:%S", time.localtime(t)) if t else "—"

        L = [f"[b]{job.id}[/b]    {_status_glyph(job.status)} [{job.status.upper()}]",
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
        self.query_one("#livetermbtn", Button).label = "» HIDE TERM" if w.display else "» TERMINAL"

    def _toggle_arc_term(self):
        log = self.query_one("#inspectlog", RichLog)
        panel = self.query_one("#inspectpanel")
        show = not log.display
        log.display = show
        panel.display = not show
        self.query_one("#arctermbtn", Button).label = "» DETAILS" if show else "» TERMINAL"
        if show:
            log.clear()
            job = self.mgr.jobs.get(self._insp_jid)
            if job:
                for ln in job.tail[-400:]:
                    log.write(ln)

    def action_cycle_preview(self):
        """Cycle the preview glyph density (sextant -> quadrant -> half). Use if sextants
        show as boxes/tofu on an older Cascadia — quadrant is universally font-safe."""
        global PREVIEW_MODE
        order = ["sextant", "quadrant", "half"]
        PREVIEW_MODE = order[(order.index(PREVIEW_MODE) + 1) % 3] if PREVIEW_MODE in order else "sextant"
        self._preview_mtime = 0.0    # force a re-render on the next tick
        self.notify(f"Preview style: {PREVIEW_MODE}")

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

    def action_suspend(self):
        self.mgr.suspend()

    def action_resume(self):
        jid = self._selected("#qtable")
        if jid:
            self.mgr.resume_suspended(jid)


if __name__ == "__main__":
    Studio().run()
