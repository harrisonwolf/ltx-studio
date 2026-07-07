"""Stall-sentry ladder, event sounds, and the unified _time_left estimator — pure-unit (no TUI).
Drives the REAL Studio methods unbound with stub state + a fake clock."""
import sys, os, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import studio

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

calls = []
studio.sounds = types.SimpleNamespace(play=lambda ev, repo: calls.append(ev))
class Clock:
    t = 0.0
    @staticmethod
    def monotonic(): return Clock.t
    @staticmethod
    def time(): return 1_000_000.0
_real_time = studio.time
studio.time = Clock

def J(phase="generating", step=4, **kw):
    d = dict(id="j1", status="running", phase=phase, seg=1, step=step, load_step=5, preview=None)
    d.update(kw)
    return types.SimpleNamespace(**d)

state = {"active": None}
mgr = types.SimpleNamespace(archived=lambda: [], queued=lambda: [], active=lambda: state["active"],
                            paused=False, suspend=lambda: calls.append("susp"),
                            hard_interrupt=lambda: calls.append("kill"))
me = types.SimpleNamespace(mgr=mgr, _stall_secs=240.0, _stall_action="suspend",
                           _stall_grace=180.0, _stall_decode_secs=600.0, _stall_max_secs=7200.0)
def alert(): studio.Studio._alerts(me)

# --- stall ladder: gen fuse 240 / susp at +90 / kill at +180; hiccup recovery never latches ---
state["active"] = J(); Clock.t = 0; alert()
Clock.t = 250; alert(); check("gen: sound only at 250s", "susp" not in calls and calls.count("snd" if False else "run_stall") == 1, calls)
Clock.t = 340; alert(); check("gen: delayed suspend at 340s", calls.count("susp") == 1)
Clock.t = 430; alert(); check("gen: kill at 430s", calls.count("kill") == 1)
calls.clear(); state["active"] = J(step=9)
Clock.t = 1000; alert(); Clock.t = 1300; alert()
state["active"] = J(step=10); Clock.t = 1310; alert()
check("hiccup recovered before +90s: suspend never sent", "susp" not in calls)
# --- decode/save + warmup share the long fuse ---
calls.clear(); state["active"] = J("decoding")
Clock.t = 2000; alert(); Clock.t = 2500; alert()
check("decode: quiet at 8.3m", calls == [])
Clock.t = 2650; alert(); check("decode: fires at ~10.8m", "run_stall" in calls)
calls.clear(); state["active"] = J("warmup", step=0)
Clock.t = 4000; alert(); Clock.t = 4400; alert()
check("warmup: quiet at 6.6m (long fuse)", calls == [])
# --- any-phase catch-all ---
calls.clear(); state["active"] = J("loading", step=0)
Clock.t = 10000; alert(); Clock.t = 17300; alert()
check("loading: catch-all kill at >2h", "kill" in calls)
# --- event transitions: run_start on idle->running, queue_empty once on busy->idle ---
calls.clear(); state["active"] = None
me2 = types.SimpleNamespace(mgr=mgr, _stall_secs=240.0, _stall_action="suspend",
                            _stall_grace=180.0, _stall_decode_secs=600.0, _stall_max_secs=7200.0)
studio.Studio._alerts(me2)                       # baseline: idle
state["active"] = J(id="jX"); studio.Studio._alerts(me2)
check("run_start on idle->running", "run_start" in calls)
state["active"] = None; studio.Studio._alerts(me2)
check("queue_empty on busy->idle", "queue_empty" in calls)
n = calls.count("queue_empty"); studio.Studio._alerts(me2)
check("queue_empty fires once", calls.count("queue_empty") == n)
studio.time = _real_time

# --- _time_left: measured-first incl. in-flight shot; smart fallback; never raises ---
import time as _t
now = _t.time()
def JT(**kw):
    d = dict(seg=2, nseg=2, seg_secs=[859], seg_started=now - 300)
    d.update(kw)
    return types.SimpleNamespace(**d)
stub = types.SimpleNamespace(_run_budget=lambda j: {"gen": 600.0, "warm": 200.0},
                             _smart_pct=lambda j: 25.0)
TL = studio.Studio._time_left
left = TL(stub, JT())
check("final shot in flight != 0 (the screenshot bug)", 500 <= (left or -1) <= 600, left)
check("overrun clamps to 0", TL(stub, JT(seg_started=now - 5000)) == 0)
check("smart fallback before first shot", TL(stub, JT(seg_secs=[], seg=1)) == 600)
def _boom(j): raise ValueError()
check("estimator failure -> None", TL(types.SimpleNamespace(_run_budget=_boom, _smart_pct=lambda j: 0),
                                      types.SimpleNamespace()) is None)

# --- standby-gap absorber: baselines shift by the frozen span, slept accumulates ---
gapme = types.SimpleNamespace(_smart_step_wall=1000.0)
gj = types.SimpleNamespace(seg_started=5000.0, phase_started=5100.0, first_step_ts=4900.0)
studio.Studio._absorb_standby_gap(gapme, gj, 30000.0)
check("standby: all wall baselines shifted", gj.seg_started == 35000.0 and gj.phase_started == 35100.0
      and gj.first_step_ts == 34900.0 and gapme._smart_step_wall == 31000.0)
check("standby: slept tallied", gj.slept == 30000.0)
studio.Studio._absorb_standby_gap(gapme, gj, 500.0)
check("standby: repeat gaps accumulate", gj.slept == 30500.0)

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
