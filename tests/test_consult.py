"""CONSULT / CHAT modal regressions, against a fake daemon (no model, no GPU):
Ctrl+Enter sends instead of queueing a run, bracketed text never crashes the app, a daemon that
dies after 'ready' is re-warmed, a load error stays visible, SEND stays off mid-reply, and RESET
mid-reply drops the late answer."""
import sys, os, asyncio, time, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["STUDIO_NO_ANIM"] = "1"
import studio
from textual.widgets import TextArea

ok = True
def check(name, cond, detail=""):
    global ok; ok &= bool(cond)
    print(("PASS" if cond else "FAIL"), "::", name, ("" if cond else str(detail)))

enqueued = []
class FakeMgr:
    def __init__(self): self.jobs, self.paused, self.vram_reserve_gb = {}, False, 1.0
    def queued(self): return []
    def suspended(self): return []
    def archived(self): return []
    def active(self): return None
    def counts(self): return (0, 0, 0, 0)
    def enhance_children(self, jid): return []
    def enqueue(self, *a, **k):
        enqueued.append(a); return types.SimpleNamespace(id="jX")
    def shutdown(self): pass
studio.JobManager = FakeMgr
studio.save_studio_config = lambda cfg: None
studio.load_studio_config = lambda: {}
studio.gpu_budget.budget_ok = lambda *a, **k: (True, 99999)

class FakeDaemon:
    def __init__(self):
        self.ready, self.up, self.info, self.last_error, self.cpu_mode = True, True, "fake", "", False
        self.warms, self.reply, self.delay = 0, {"reply": "ok"}, 0.05
    def alive(self): return self.up
    def warm(self, cpu=False): self.warms += 1
    def kill(self): pass
    def ask_stream(self, history, image, on_chunk, raw=False):
        time.sleep(self.delay)
        return dict(self.reply)

def text(w):
    return str(w.render())

async def run(screen_cls, msg_id, send_id, status_id, log_id):
    tag = screen_cls.__name__
    app = studio.Studio()
    d = app.consult = FakeDaemon()
    async with app.run_test(size=(179, 52)) as pilot:
        await pilot.pause()
        app.query_one("#prompt", TextArea).text = "a fox in snow"
        scr = screen_cls()
        app.push_screen(scr); await pilot.pause()
        # 1. Ctrl+Enter sends the chat message; nothing is queued from the hidden form
        ta = scr.query_one(msg_id, TextArea); ta.focus(); ta.text = "make it moodier"
        await pilot.press("ctrl+enter"); await pilot.pause(0.4)
        check(f"{tag}: ctrl+enter queues no run", not enqueued, enqueued)
        check(f"{tag}: ctrl+enter sends the message", scr.history[:1] == [{"role": "user", "text": "make it moodier"}], scr.history)
        # 2. markup-looking text from the user AND the model never crashes
        d.reply = {"reply": "try [/INST] this [red] or [/b]", "config": {"prompt": "x [/i] y"}}
        ta.text = "make it look [/b] like film"
        scr._send(); await pilot.pause(0.4)
        check(f"{tag}: bracketed user + model text survives", app.is_running and scr.history[-1]["text"].startswith("try [/INST]"), scr.history[-1:])
        # 3. SEND stays disabled and the thinking status holds while a reply streams
        d.delay = 1.2
        ta.text = "slow one"; scr._send(); await pilot.pause(0.6)
        check(f"{tag}: SEND stays off mid-reply", scr.query_one(send_id).disabled and scr._inflight)
        check(f"{tag}: 'thinking' status not clobbered", "thinking" in text(scr.query_one(status_id)), text(scr.query_one(status_id)))
        # 4. RESET mid-reply: the late answer is dropped, not appended to the fresh chat
        scr._on_chunk("partial old answer", scr._gen); await pilot.pause(0.05)
        scr.action_reset(); await pilot.pause(0.05)
        sp = scr.query_one("#streampreview" if tag == "ConsultScreen" else "#rawstream")
        check(f"{tag}: RESET hides the old answer's stream preview", not sp.display and "partial" not in text(sp))
        await pilot.pause(1.0)
        check(f"{tag}: RESET mid-reply drops the late answer", scr.history == [], scr.history)
        check(f"{tag}: SEND usable again after the dropped reply", not scr.query_one(send_id).disabled)
        d.delay = 0.05
        # 5. daemon dies after reporting ready -> re-warmed, SEND disabled until it is back
        d.up = False; await pilot.pause(0.6)
        check(f"{tag}: dead-after-ready daemon is re-warmed", d.warms >= 1, d.warms)
        check(f"{tag}: SEND disabled while the daemon is down", scr.query_one(send_id).disabled)
        # 6. a load error stays visible (not overwritten by 'loading…')
        d.ready = False; d.last_error = "CUDA out of memory"; await pilot.pause(0.6)
        check(f"{tag}: load error stays visible", "out of memory" in text(scr.query_one(status_id)), text(scr.query_one(status_id)))
        # 6b. the retry respawned (alive, loading) but the last failure is still the news -> keep it up
        d.up = True; await pilot.pause(0.6)
        check(f"{tag}: load error stays up while the retry loads", "out of memory" in text(scr.query_one(status_id)), text(scr.query_one(status_id)))
        # 6c. keyboard send can't bypass a disabled SEND while the daemon is down
        d.ready, d.up, d.last_error = True, False, ""      # reported ready, then died
        n = len(scr.history)
        ta.text = "should not send"; ta.focus()
        await pilot.press("ctrl+enter"); await pilot.pause(0.3)
        check(f"{tag}: ctrl+enter can't send to a dead daemon", len(scr.history) == n, scr.history[n:])
        # 7. back up -> ready line + SEND re-enabled
        d.ready, d.up, d.last_error = True, True, ""; await pilot.pause(0.6)
        check(f"{tag}: recovers to ready", not scr.query_one(send_id).disabled and "ready" in text(scr.query_one(status_id)))
    check(f"{tag}: app exited without an exception", getattr(app, "_exception", None) is None, getattr(app, "_exception", None))

def daemon_kill_mid_load():
    """An intentional kill() while the model loads (CONSULT closed / a render took the GPU) is not a
    load failure: no 'failed to load' may be left behind for the next load to show."""
    import subprocess, threading, tempfile
    real_repo = studio.REPO
    studio.REPO = tempfile.mkdtemp(prefix="consultrepo_")     # consult_daemon.err lives under REPO
    try:
        open(os.path.join(studio.REPO, "consult_daemon.err"), "w").write("Loading checkpoint shards:  50%\n")
        d = studio.ConsultDaemon()
        d.proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        t = threading.Thread(target=d._await_ready, daemon=True); t.start()
        time.sleep(0.3); d.kill(); t.join(5)
        check("daemon: kill() mid-load leaves no false load error", not d.last_error and not d.ready, d.last_error)
    finally:
        studio.REPO = real_repo

async def main():
    daemon_kill_mid_load()
    await run(studio.ConsultScreen, "#chatmsg", "#sendbtn", "#consultstatus", "#chatlog")
    await run(studio.ChatScreen, "#rawmsg", "#rawsendbtn", "#rawstatus", "#rawlog")
    # Ctrl+Enter on the NEW RUN form itself still queues
    app = studio.Studio(); app.consult = FakeDaemon()
    async with app.run_test(size=(179, 52)) as pilot:
        await pilot.pause()
        app.query_one("#prompt", TextArea).text = "a fox in snow"
        n = len(enqueued)
        await pilot.press("ctrl+enter"); await pilot.pause(0.4)
        check("form ctrl+enter still queues", len(enqueued) == n + 1, len(enqueued) - n)

asyncio.run(main())
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
