"""Event sound harness — play a (custom) sound effect on studio events.

MINIMAL + EXTENSIBLE. Right now studio.py only fires "run_done", but play(event, repo) accepts ANY
event name, so wiring "run_start" / "run_error" / "run_queued" / etc. later is a one-line call each.

CONFIG (all optional) — runs/studio_config.json:
  {"sounds": {
     "enabled": true,                              # master on/off (default: on)
     "events": {"run_done": "sfx/my_chime.wav"},   # per-event file; path is repo-relative or absolute
     "player": ""                                   # optional explicit command; "{file}" is substituted,
  }}                                                #   else the file is appended (e.g. "paplay" / "aplay -q")

ZERO-CONFIG: if "events" has no entry for an event, it falls back to sfx/<event>.wav — so just dropping
a WAV at  sfx/run_done.wav  makes it play, no JSON editing needed.

Robustness contract: a missing file, absent player, or ANY error is a SILENT no-op. This must never raise
into or block the TUI — playback runs on a daemon thread and is fire-and-forget.

Audio on WSL2/Windows: tries native Linux players first (paplay/pw-play/aplay/ffplay), then falls back to
handing a .wav to the Windows host via powershell Media.SoundPlayer (reliable when WSLg audio isn't set up).
Pure stdlib; no torch/textual/studio imports.
"""
import os
import json
import shlex
import shutil
import threading
import subprocess

DEFAULT_DIR = "sfx"


def _cfg(repo):
    try:
        with open(os.path.join(repo, "runs", "studio_config.json")) as fh:
            return (json.load(fh) or {}).get("sounds") or {}
    except Exception:
        return {}


def _resolve_file(repo, event, cfg):
    """-> absolute path to the sound file for `event`, or None if it doesn't exist."""
    path = (cfg.get("events") or {}).get(event) or os.path.join(DEFAULT_DIR, "%s.wav" % event)
    if not os.path.isabs(path):
        path = os.path.join(repo, path)
    return path if os.path.isfile(path) else None


def _player_cmd(path, player_tmpl):
    """-> argv list to play `path`, or None if no player is available."""
    if player_tmpl:
        try:
            if "{file}" in player_tmpl:
                return shlex.split(player_tmpl.replace("{file}", shlex.quote(path)))
            return shlex.split(player_tmpl) + [path]
        except Exception:
            return None
    for exe, args in (("paplay", []), ("pw-play", []), ("aplay", ["-q"]),
                      ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"])):
        if shutil.which(exe):
            return [exe, *args, path]
    # WSL2 -> Windows host fallback: SoundPlayer plays a WAV through native Windows audio.
    if shutil.which("powershell.exe") and path.lower().endswith(".wav"):
        try:
            win = subprocess.check_output(["wslpath", "-w", path], text=True, timeout=5).strip()
            return ["powershell.exe", "-NoProfile", "-Command",
                    "(New-Object Media.SoundPlayer '%s').PlaySync()" % win.replace("'", "''")]
        except Exception:
            return None
    return None


def _spawn(cmd):
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)


def _play_blocking(event, repo):
    """Resolve + launch the player synchronously (RESPECTS the enabled toggle). Returns True if a player
    was spawned. Never raises."""
    try:
        cfg = _cfg(repo)
        if not cfg.get("enabled", True):
            return False
        path = _resolve_file(repo, event, cfg)
        if not path:
            return False
        cmd = _player_cmd(path, cfg.get("player") or "")
        if not cmd:
            return False
        _spawn(cmd)
        return True
    except Exception:
        return False


def preview(event, repo):
    """Play `event` IGNORING the enabled toggle — for a UI 'test' button (you want to hear it even when
    sound is off). Non-blocking. Returns a short status string for the UI (what played, or why not)."""
    try:
        cfg = _cfg(repo)
        path = _resolve_file(repo, event, cfg)
        if not path:
            return "no sound file — drop one at sfx/%s.wav" % event
        cmd = _player_cmd(path, cfg.get("player") or "")
        if not cmd:
            return "no audio player found (see sounds.py header)"
        threading.Thread(target=lambda c=cmd: _spawn(c), daemon=True).start()
        return "playing %s · %s" % (event, os.path.basename(path))
    except Exception:
        return "sound error"


def play(event, repo):
    """Fire-and-forget the sound for `event` on a daemon thread — never blocks or raises into the UI."""
    try:
        threading.Thread(target=_play_blocking, args=(event, repo), daemon=True).start()
    except Exception:
        pass


if __name__ == "__main__":     # `python sounds.py test [event]` — verify audio without waiting for a run
    import sys
    repo = os.path.dirname(os.path.abspath(__file__))
    event = sys.argv[2] if len(sys.argv) > 2 else "run_done"
    cfg = _cfg(repo)
    resolved = _resolve_file(repo, event, cfg)
    print("event:   ", event)
    print("enabled: ", cfg.get("enabled", True))
    print("file:    ", resolved or "(none found — put one at sfx/%s.wav or set sounds.events)" % event)
    print("player:  ", _player_cmd(resolved, cfg.get("player") or "") if resolved else "(n/a)")
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print("result:  ", "PLAYED" if _play_blocking(event, repo) else "no sound (see above)")
