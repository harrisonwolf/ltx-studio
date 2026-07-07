#!/usr/bin/env python
"""LTX Studio's small modal screens (confirm/rename/re-roll/pair/replicate/rate/theme/frames).

Split out of studio.py (2026-07-06 light restructuring): pure code motion, no behavior
change — imports are the only wiring. See tests/ for the regression net."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from preview_art import render_preview
from studio_config import load_studio_config, save_studio_config

class FrameScrollScreen(ModalScreen):
    """Scroll through a finished run's saved output frames as terminal art (reuses render_preview).
    ←/→ (or h/l) step one frame, PgUp/PgDn jump 10, Home/End first/last, Esc closes."""
    DEFAULT_CSS = """
    FrameScrollScreen { align: center middle; background: $background 90%; }
    #fsbox { width: auto; height: auto; border: round $primary; background: $surface-deep; padding: 1 2; }
    #fsart { width: auto; height: auto; content-align: center middle; }
    #fshelp { color: $secondary; height: 1; margin-top: 1; }
    """
    BINDINGS = [
        ("escape", "close", "Close"),
        ("left", "prev", "Prev"), ("h", "prev", ""),
        ("right", "next", "Next"), ("l", "next", ""),
        ("home", "first", "First"), ("end", "last", "Last"),
        ("pageup", "back10", "-10"), ("pagedown", "fwd10", "+10"),
    ]

    def __init__(self, frames, title):
        super().__init__()
        self.frames = list(frames)        # sorted absolute frame paths
        self.ftitle = title
        self.i = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="fsbox"):
            yield Static("", id="fsart")
            yield Static("", id="fshelp")

    def on_mount(self):
        self.query_one("#fsbox").border_title = "« FRAMES — %s »" % self.ftitle
        self._paint()

    # NB: named _paint, NOT _render — Widget._render() is a Textual-internal method that must return
    # a Visual; shadowing it with a None-returning helper crashes the compositor at first paint.
    def _paint(self):
        n = len(self.frames)
        art, help_ = self.query_one("#fsart", Static), self.query_one("#fshelp", Static)
        if not n:
            art.update("[dim]no frames saved for this run.[/dim]")
            help_.update("[dim]Esc to close[/dim]")
            return
        self.i = max(0, min(self.i, n - 1))
        cols = max(48, min(140, (getattr(self.app.size, "width", 0) or 120) - 8))
        art.styles.width = cols + 2
        art.update(render_preview(self.frames[self.i], cols=cols))
        help_.update("[#6dffab]frame %d / %d[/#6dffab]   [dim]←/→ step · PgUp/PgDn ±10 · Home/End · Esc[/dim]"
                     % (self.i + 1, n))

    def action_prev(self):  self.i -= 1;  self._paint()
    def action_next(self):  self.i += 1;  self._paint()
    def action_first(self): self.i = 0;   self._paint()
    def action_last(self):  self.i = len(self.frames) - 1; self._paint()
    def action_back10(self): self.i -= 10; self._paint()
    def action_fwd10(self):  self.i += 10; self._paint()
    def action_close(self): self.dismiss(None)


def _status_glyph(status):
    return {"done": "[#9dffce]✓[/#9dffce]", "failed": "[#ff6d6d]✕[/#ff6d6d]",
            "cancelled": "[dim]■[/dim]", "interrupted": "[#ffcf5c]‖[/#ffcf5c]",
            "suspended": "[#ffcf5c]▽[/#ffcf5c]"}.get(status, "·")


class ConfirmDeleteScreen(ModalScreen):
    """Confirm a permanent delete. dismiss(True) deletes, dismiss(False)/escape cancels."""
    DEFAULT_CSS = """
    ConfirmDeleteScreen { align: center middle; background: $background 80%; }
    #delbox { width: 70; height: auto; border: round $error; background: $background; padding: 1 2; }
    #deltitle { color: $error; text-style: bold; height: 1; }
    #delbody { height: auto; color: $error; margin: 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #del_yes { background: $background; color: $error; text-style: bold; }
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


class ThemePickerScreen(ModalScreen):
    """T13: browse every registered theme with LIVE PREVIEW -- arrowing to a theme applies it
    immediately; ENTER keeps it; ESCAPE (or closing without picking) reverts to whatever theme was
    active when the picker opened. Textual 8.2.7's own command-palette ThemeProvider only applies on
    select (no highlight-preview hook on Provider/Hit), so a dedicated OptionList screen is the
    cleanest way to get a true live preview in this version."""
    DEFAULT_CSS = """
    ThemePickerScreen { align: center middle; background: $background 80%; }
    #thbox { width: 50; height: auto; max-height: 80%; border: round $primary; background: $background; padding: 1 2; }
    #thtitle { color: $accent; text-style: bold; height: 1; }
    #thsub { color: $secondary; height: auto; margin: 0 0 1 0; }
    #thlist { height: auto; max-height: 20; border: round $border; background: $surface-deep; }
    """
    BINDINGS = [("escape", "cancel", "Revert + close")]

    def __init__(self):
        super().__init__()
        self._original_theme = None

    def compose(self) -> ComposeResult:
        with Vertical(id="thbox"):
            yield Static("◐  THEME", id="thtitle")
            yield Static("↑/↓ previews live · ENTER keeps it · ESC reverts", id="thsub")
            yield OptionList(id="thlist")

    def on_mount(self):
        self.query_one("#thbox").border_title = "« THEME PICKER »"
        self._original_theme = self.app.theme
        # CURATED list: only the hand-tuned pipboy family (builtin textual themes are cut —
        # quality over quantity). The active theme is always included so the highlight lands.
        names = sorted(n for n in self.app.available_themes
                       if n.startswith("pipboy") or n == self._original_theme)
        opts = self.query_one("#thlist", OptionList)
        for name in names:
            opts.add_option(Option(("» " if name == self._original_theme else "  ") + name, id=name))
        try:
            opts.highlighted = names.index(self._original_theme)
        except ValueError:
            pass
        opts.focus()

    def on_option_list_option_highlighted(self, e: OptionList.OptionHighlighted):
        if e.option and e.option.id:
            try:
                self.app.theme = e.option.id     # live preview as you arrow through
            except Exception:
                pass

    def on_option_list_option_selected(self, e: OptionList.OptionSelected):
        if e.option and e.option.id:
            self.app.theme = e.option.id
            try:      # ENTER = keep -> persist across restarts (studio_config.json "theme")
                save_studio_config({**load_studio_config(), "theme": e.option.id})
            except Exception:
                pass
        self.dismiss(True)

    def action_cancel(self):
        if self._original_theme:
            self.app.theme = self._original_theme    # revert -- dismissed without an explicit pick
        self.dismiss(False)


class RenameScreen(ModalScreen):
    """Prefilled Input; dismiss(str) renames, dismiss(None)/escape cancels."""
    DEFAULT_CSS = """
    RenameScreen { align: center middle; background: $background 80%; }
    #renbox { width: 64; height: auto; border: round $primary; background: $background; padding: 1 2; }
    #rentitle { color: $accent; text-style: bold; height: 1; }
    #rensub { color: $secondary; height: auto; margin: 0 0 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #ren_ok { background: $border-strong; color: $success; text-style: bold; }
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
    #rrbox { width: 64; height: auto; border: round $primary; background: $background; padding: 1 2; }
    #rrtitle { color: $accent; text-style: bold; height: 1; }
    #rrsub { color: $secondary; height: auto; margin: 0 0 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #rr_ok { background: $border-strong; color: $success; text-style: bold; }
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


class PairScreen(ModalScreen):
    """PAIR A/B (Q3): clone a job with ONE dial changed, same seed -- for a blind A/B comparison.
    dismiss({"dial": str, "value": str}) confirms; dismiss(None)/escape cancels."""
    DEFAULT_CSS = """
    PairScreen { align: center middle; background: $background 80%; }
    #pabox { width: 64; height: auto; border: round $primary; background: $background; padding: 1 2; }
    #patitle { color: $accent; text-style: bold; height: 1; }
    #pasub { color: $secondary; height: auto; margin: 0 0 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #pa_ok { background: $border-strong; color: $success; text-style: bold; }
    """
    DIALS = ("steps", "cfg", "res", "seg", "cond_strength", "steadiness", "backend", "fps")
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, summary):
        super().__init__()
        self.summary = summary

    def compose(self) -> ComposeResult:
        with Vertical(id="pabox"):
            yield Static("⇄  PAIR A/B RUN", id="patitle")
            yield Static(f"clone '{self.summary}' with ONE dial changed — same seed, for a blind A/B.\n"
                         f"dial: {', '.join(self.DIALS)}", id="pasub")
            yield Input(placeholder="dial name (e.g. cfg)", id="pa_dial")
            yield Input(placeholder="new value", id="pa_value")
            with Horizontal(classes="crow"):
                yield Button("⇄ PAIR", id="pa_ok")
                yield Button("✕ CANCEL", id="pa_close")

    def on_mount(self):
        self.query_one("#pabox").border_title = "« PAIR A/B »"
        self.query_one("#pa_dial", Input).focus()

    def _submit(self):
        self.dismiss({"dial": (self.query_one("#pa_dial", Input).value or "").strip().lower(),
                      "value": (self.query_one("#pa_value", Input).value or "").strip()})

    def on_input_submitted(self, e):
        self._submit()

    def on_button_pressed(self, e):
        if e.button.id == "pa_ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_close(self):
        self.dismiss(None)


class ReplicateScreen(ModalScreen):
    """×N REPLICATE (Q3): re-run a job N times with random seeds -- probes the seed noise floor.
    dismiss({"n": str}) confirms (blank/invalid -> 3, clamped 2-5); dismiss(None)/escape cancels."""
    DEFAULT_CSS = """
    ReplicateScreen { align: center middle; background: $background 80%; }
    #rebox { width: 64; height: auto; border: round $primary; background: $background; padding: 1 2; }
    #retitle { color: $accent; text-style: bold; height: 1; }
    #resub { color: $secondary; height: auto; margin: 0 0 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    #re_ok { background: $border-strong; color: $success; text-style: bold; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, summary, subtitle=None):
        super().__init__()
        self.summary = summary
        self.subtitle = subtitle or (
            f"re-run '{summary}' N times with random seeds — probes the seed noise floor.")

    def compose(self) -> ComposeResult:
        with Vertical(id="rebox"):
            yield Static("×N  REPLICATE RUN", id="retitle")
            yield Static(self.subtitle, id="resub")
            yield Input(placeholder="N (2-5, default 3)", id="re_n")
            with Horizontal(classes="crow"):
                yield Button("×N REPLICATE", id="re_ok")
                yield Button("✕ CANCEL", id="re_close")

    def on_mount(self):
        self.query_one("#rebox").border_title = "« REPLICATE »"
        self.query_one("#re_n", Input).focus()

    def _submit(self):
        self.dismiss({"n": (self.query_one("#re_n", Input).value or "").strip()})

    def on_input_submitted(self, e):
        self._submit()

    def on_button_pressed(self, e):
        if e.button.id == "re_ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_close(self):
        self.dismiss(None)


class RatePairScreen(ModalScreen):
    """≷ RATE PAIR (Q3): blind pair rating -- shows two output paths as '1'/'2' in random order (no
    metadata that could bias the vote). dismiss("1"|"2"|"tie") records the verdict; dismiss(None)/
    escape cancels."""
    DEFAULT_CSS = """
    RatePairScreen { align: center middle; background: $background 80%; }
    #rpbox { width: 74; height: auto; border: round $primary; background: $background; padding: 1 2; }
    #rptitle { color: $accent; text-style: bold; height: 1; }
    #rpsub { color: $secondary; height: auto; margin: 0 0 1 0; }
    .crow { height: 3; margin-top: 1; }
    .crow Button { margin-right: 2; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, path1, path2):
        super().__init__()
        self.path1, self.path2 = path1, path2

    def compose(self) -> ComposeResult:
        with Vertical(id="rpbox"):
            yield Static("≷  RATE PAIR (blind)", id="rptitle")
            yield Static(f"1: {self.path1}\n2: {self.path2}\n\nplay both yourself, then pick.", id="rpsub")
            with Horizontal(classes="crow"):
                yield Button("1 BETTER", id="rp_1")
                yield Button("2 BETTER", id="rp_2")
                yield Button("TIE", id="rp_tie")
                yield Button("✕ CANCEL", id="rp_close")

    def on_button_pressed(self, e):
        if e.button.id == "rp_1":
            self.dismiss("1")
        elif e.button.id == "rp_2":
            self.dismiss("2")
        elif e.button.id == "rp_tie":
            self.dismiss("tie")
        else:
            self.dismiss(None)

    def action_close(self):
        self.dismiss(None)


