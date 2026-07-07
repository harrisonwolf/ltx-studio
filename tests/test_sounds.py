"""Sound harness contract: enabled-gate suppression, per-event resolution, missing-file no-op,
preview bypasses the toggle. Never plays audio (temp config + silent 'true' player)."""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sounds

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "runs"))
os.makedirs(os.path.join(tmp, "sfx"))
open(os.path.join(tmp, "x.wav"), "wb").write(b"RIFF0000WAVE")
open(os.path.join(tmp, "sfx", "queue_empty.wav"), "wb").write(b"RIFF0000WAVE")

def write_cfg(enabled=True, events=None):
    with open(os.path.join(tmp, "runs", "studio_config.json"), "w") as f:
        json.dump({"sounds": {"enabled": enabled, "events": events or {}, "player": "true"}}, f)

write_cfg(enabled=False, events={"run_done": "x.wav"})
check("toggle OFF suppresses play", sounds._play_blocking("run_done", tmp) is False)
pr = sounds.preview("run_done", tmp)
check("preview bypasses the toggle", pr.startswith("playing"), pr)

write_cfg(enabled=True, events={"run_done": "x.wav"})
check("configured event resolves + plays", sounds._play_blocking("run_done", tmp) is True)
check("default fallback sfx/<event>.wav resolves",
      sounds._resolve_file(tmp, "queue_empty", sounds._cfg(tmp)).endswith("queue_empty.wav"))
check("unknown event with no file -> no-op", sounds._play_blocking("nope", tmp) is False)
check("missing-file preview says so", "no sound file" in sounds.preview("nope", tmp))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
lib = [f for f in os.listdir(os.path.join(REPO, "sfx")) if f.endswith(".wav")]
check("shipped library has the 4 event defaults", all(
    "%s.wav" % e in lib for e in ("run_done", "run_stall", "run_start", "queue_empty")), lib)

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
