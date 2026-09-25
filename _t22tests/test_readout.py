#!/usr/bin/env python
"""T22 CPU tests for readout.py — torch-free, textual-free, GPU-free.

Run: venv/bin/python _t22tests/test_readout.py   (from the repo root)
Prints ALL_T22_CHECKS_PASS and exits 0 on success; raises AssertionError otherwise.
"""
import os
import sys
import json
import tempfile

from rich.text import Text
from rich.cells import cell_len

# running `python _t22tests/test_readout.py` puts _t22tests/ (not the repo root) on sys.path[0]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import readout  # noqa: E402

def strip(s):
    """Rich markup -> the plain text the panel actually shows (the studio renders via
    Text.from_markup, so a MarkupError here is a real crash, and escaped '\\[██]' boxes stay)."""
    return Text.from_markup(s).plain


# the narrowest content width the studio can hand render_readout(): #readout sits in #rleft
# (min-width 30) minus its round border (2) and padding (2) -> 26 cells
MIN_STUDIO_WIDTH = 26


def base_cfg(**over):
    cfg = {
        "backend": "ltx", "mode": "single", "steadiness": "hold",
        "W": 704, "H": 480, "fps": 24, "seg_frames": 49, "total_frames": 49,
        "nseg": 1, "chain": False, "steps": 40, "cfg": 3.0, "cond_strength": 1.0,
        "ltx_variant": None, "wan_ref_anchor": "off", "latent_adain": 0,
        "reserve_gb": 1.0, "consult": False,
    }
    cfg.update(over)
    return cfg


VARIANTS = {
    "ltx-single": base_cfg(),
    "ltx-director": base_cfg(mode="director", chain=True, nseg=4, seg_frames=49,
                             total_frames=150, W=768, H=512, consult=True),
    "wan-chained": base_cfg(backend="wan", cfg=5.0, chain=True, nseg=3, seg_frames=45,
                            total_frames=129, wan_ref_anchor="on", steadiness="balanced"),
    "wan-turbo": base_cfg(backend="wan-turbo", cfg=1.0, steps=8, seg_frames=57),
}

TITLES = ("VRAM", "CLIP", "RAM", "SHOTS", "QUAL", "DRIFT")
ABSOLUTE_WORDS = ("guarantee", "guaranteed", "perfect", "certain", "always", "100%", "flawless", "best")


def check_1_render_titles():
    for name, cfg in VARIANTS.items():
        for secs in (600.0, None):
            out = readout.render_readout(cfg, secs, None)
            assert out and out.strip(), "%s/%s: empty render" % (name, secs)
            plain = strip(out)
            for t in TITLES:
                assert t in plain, "%s/%s: missing gauge %r" % (name, secs, t)
            assert "READOUT" in plain, "%s: missing header" % name
            assert "rough guide" in plain, "%s: quality gauge missing 'rough guide'" % name


def check_2_line_widths():
    # a fit with an active time-fit forces the SHOTS caption's "(fit ...)" annotation, and a
    # wildly wrong COEF makes that delta huge -> widest possible caption line. The long-chain
    # variants force the '┄+N' tail + a long 'asked→actual' SHOTS value.
    tf = {"COEF": 40.0, "WARM": 4000.0, "DECODE": 900.0, "LOAD": 9000.0, "SEAM": 90.0, "rows": 6}
    annot_fit = {"vram": {}, "time": {"ltx": dict(tf, SEG_REF=49.0), "wan": dict(tf, SEG_REF=29.0),
                                      "wan-turbo": dict(tf, SEG_REF=29.0)}}
    variants = dict(VARIANTS)
    variants["wan-112-shots"] = base_cfg(backend="wan", mode="director", W=832, H=480, fps=16,
                                         seg_frames=45, total_frames=4001, nseg=112, chain=True,
                                         seconds="250.25", steadiness="evolve")
    variants["ltx-14-shots"] = base_cfg(mode="director", W=768, H=512, seg_frames=49,
                                        total_frames=577, nseg=14, chain=True, seconds="24.03")
    variants["thin-quality"] = base_cfg(W=512, H=320, steps=4, cfg=9.0)
    for name, cfg in variants.items():
        for fit in (None, annot_fit):
            for secs in (600.0, 90000.0, None):
                for width in [None] + list(range(MIN_STUDIO_WIDTH, 81)) + [100, 130]:
                    out = readout.render_readout(cfg, secs, fit, width=width)
                    budget = width or 48
                    for ln in strip(out).split("\n"):   # MarkupError -> the test fails loudly
                        w = cell_len(ln)
                        assert w <= budget, "%s/%s/width=%s: line %d cells > %d: %r" % (
                            name, secs, width, w, budget, ln)
                    if width:                            # the fit note is dropped whole, never clipped
                        for ln in strip(out).split("\n"):
                            assert "(fit" not in ln or ln.rstrip().endswith(")"), \
                                "%s/width=%s: clipped fit note %r" % (name, width, ln)


def check_2c_value_tags_stay_visible():
    """On a narrow panel the BARS give way, not the numbers: every gauge row still ends with its
    complete value tag (the same tag the default-width render shows) at every studio width."""
    for name, cfg in VARIANTS.items():
        for secs in (600.0, None):
            ref = strip(readout.render_readout(cfg, secs, None)).split("\n")
            tags = {ln.split()[0]: ln.split()[-1] for ln in ref if ln.split() and ln.split()[0] in TITLES}
            for width in range(MIN_STUDIO_WIDTH, 81):
                for ln in strip(readout.render_readout(cfg, secs, None, width=width)).split("\n"):
                    parts = ln.split()
                    if parts and parts[0] in tags:
                        assert parts[-1] == tags[parts[0]], "%s/width=%d: %s value tag %r, want %r" % (
                            name, width, parts[0], parts[-1], tags[parts[0]])


def check_2b_chain_boxes_fit_their_cell():
    """The SHOTS chain is padded to the bar width so the value tag lines up with the gauges above;
    its '┄+N' overflow tail must be budgeted too (a multi-digit N used to push it 1+ cells over)."""
    for width in range(10, 40):
        for ns in list(range(1, 130)) + [999, 1000, 1001, 1234, 99999]:
            if ns > 1 and 4 + len("┄+%d" % (ns - 1)) > width:
                continue                             # not even one box + tail fits: nothing to budget
            plain = strip(readout._chain_boxes(ns, width, "#ffffff"))
            assert cell_len(plain) == width, "chain ns=%d width=%d -> %d cells: %r" % (
                ns, width, cell_len(plain), plain)


def check_3_vram_monotonic_and_anchor():
    small = readout.vram_est(base_cfg(W=512, H=320, seg_frames=49))[0]
    bigpx = readout.vram_est(base_cfg(W=768, H=512, seg_frames=49))[0]
    assert bigpx > small, "vram not monotonic in W*H (%s !> %s)" % (bigpx, small)
    fewf = readout.vram_est(base_cfg(W=704, H=480, seg_frames=25))[0]
    manyf = readout.vram_est(base_cfg(W=704, H=480, seg_frames=97))[0]
    assert manyf > fewf, "vram not monotonic in seg_frames (%s !> %s)" % (manyf, fewf)
    est = readout.vram_est(base_cfg(backend="ltx", W=704, H=480, seg_frames=49))[0]
    assert 4.0 <= est <= 5.5, "ltx@704x480x49 vram %s not in 4.0-5.5" % est


def check_4_ram_ordering():
    dir_ltx_consult = readout.ram_est(base_cfg(mode="director", backend="ltx", chain=True, consult=True))[0]
    plain_ltx = readout.ram_est(base_cfg(backend="ltx", mode="single", chain=False))[0]
    plain_wan = readout.ram_est(base_cfg(backend="wan", mode="single", chain=False))[0]
    assert dir_ltx_consult > plain_ltx > plain_wan, \
        "ram ordering wrong: %s > %s > %s" % (dir_ltx_consult, plain_ltx, plain_wan)
    dir_ltx = readout.ram_est(base_cfg(mode="director", backend="ltx", chain=True, consult=False))[0]
    assert 20.0 <= dir_ltx <= 24.0 + 1e-9, "director+ltx ram %s not in 20-24" % dir_ltx


def check_5_drift():
    single = readout.drift_risk(base_cfg(chain=False, nseg=1))[0]
    assert single == 0, "single clip drift %s != 0" % single
    two = readout.drift_risk(base_cfg(backend="wan", chain=True, nseg=2, seg_frames=45,
                                      steadiness="balanced", wan_ref_anchor="off"))[0]
    eight = readout.drift_risk(base_cfg(backend="wan", chain=True, nseg=8, seg_frames=45,
                                        steadiness="balanced", wan_ref_anchor="off"))[0]
    assert eight > two, "8-shot drift %s !> 2-shot %s" % (eight, two)
    eight_anch = readout.drift_risk(base_cfg(backend="wan", chain=True, nseg=8, seg_frames=45,
                                             steadiness="balanced", wan_ref_anchor="on"))[0]
    assert eight_anch < eight, "anchors did not reduce 8-shot drift (%s !< %s)" % (eight_anch, eight)


def check_6_quality_range():
    for name, cfg in VARIANTS.items():
        q, note = readout.quality_score(cfg)
        assert 5 <= q <= 95, "%s: quality %s outside [5,95]" % (name, q)
        low = note.lower()
        for w in ABSOLUTE_WORDS:
            assert w not in low, "%s: note claims absolute %r: %r" % (name, w, note)


def _synth_rows():
    """10 planted ltx rows (k=0.20, COEF=1.0, WARM=100, DECODE=25) + 1 wild outlier."""
    geoms = [(512, 320, 49, 30, 1), (704, 480, 49, 40, 2), (768, 512, 49, 40, 4),
             (512, 320, 73, 25, 1), (704, 480, 57, 30, 2), (704, 480, 49, 35, 3),
             (512, 320, 121, 40, 2), (768, 512, 57, 40, 1), (704, 480, 45, 25, 2),
             (512, 320, 49, 20, 1)]
    K, COEF, WARM, DECODE, BASE, SEGREF = 0.20, 1.0, 100.0, 25.0, 2.3, 49.0
    rows = []
    for (W, H, sf, steps, nseg) in geoms:
        mpxf = W * H * sf / 1e6
        px = W * H / (512 * 320)
        ff = sf / SEGREF
        rows.append({
            "schema": 2, "backend": "ltx", "width": W, "height": H, "seg_frames": sf,
            "steps": steps, "nseg": nseg,
            "peak_vram_mb": (BASE + K * mpxf) * 1024.0,
            "phase_secs": {"generating": COEF * steps * px * ff * nseg,
                           "warmup": WARM * nseg, "decoding": DECODE * ff * nseg},
        })
    # wild outlier — the real 21646s decode / an absurd peak — median must survive it
    rows.append({"schema": 1, "backend": "ltx", "width": 704, "height": 480, "seg_frames": 49,
                 "steps": 40, "nseg": 1, "peak_vram_mb": 99999.0,
                 "phase_secs": {"generating": 21646.0, "warmup": 100.0, "decoding": 25.0}})
    return rows, K, COEF


def _write_jsonl(path, rows, corrupt=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        if corrupt:
            f.write("{ this is not valid json ]]]\n")


def check_7_refit_recovers_and_caches():
    repo = tempfile.mkdtemp(prefix="t22refit_")
    exp = os.path.join(repo, readout.EXPERIMENTS)
    rows, K, COEF = _synth_rows()
    _write_jsonl(exp, rows, corrupt=True)   # + 1 corrupt line

    fit = readout.maybe_refit(repo, min_new_rows=5)
    assert fit is not None, "refit returned None on good history"
    kv = fit["vram"]["ltx"]
    assert kv["rows"] >= 3, "vram rows %s < 3" % kv["rows"]
    assert abs(kv["k_gb_per_mpxf"] - K) / K <= 0.25, \
        "recovered k %s not within 25%% of %s" % (kv["k_gb_per_mpxf"], K)
    tv = fit["time"]["ltx"]
    assert tv["rows"] >= 3, "time rows %s < 3" % tv["rows"]
    assert abs(tv["COEF"] - COEF) / COEF <= 0.25, \
        "recovered COEF %s not within 25%% of %s" % (tv["COEF"], COEF)
    assert os.path.exists(os.path.join(repo, readout.FIT_CACHE)), "fit cache not written"

    # second call with (forced) older experiments mtime must return the cache WITHOUT re-parsing
    cache_mtime = os.path.getmtime(os.path.join(repo, readout.FIT_CACHE))
    os.utime(exp, (cache_mtime - 10, cache_mtime - 10))
    calls = {"n": 0}
    orig = readout._read_experiments

    def spy(path):
        calls["n"] += 1
        return orig(path)

    readout._read_experiments = spy
    try:
        fit2 = readout.maybe_refit(repo, min_new_rows=5)
    finally:
        readout._read_experiments = orig
    assert calls["n"] == 0, "maybe_refit re-parsed the jsonl despite unchanged mtime"
    assert fit2 is not None and abs(fit2["vram"]["ltx"]["k_gb_per_mpxf"] - K) / K <= 0.25, \
        "cached fit lost the recovered k"


def check_8_sparse_history_keeps_hand():
    repo = tempfile.mkdtemp(prefix="t22sparse_")
    exp = os.path.join(repo, readout.EXPERIMENTS)
    rows = [{"backend": "ltx", "width": 704, "height": 480, "seg_frames": 49,
             "steps": 40, "nseg": 1, "peak_vram_mb": 4800.0},
            {"backend": "ltx", "width": 512, "height": 320, "seg_frames": 49,
             "steps": 40, "nseg": 1, "peak_vram_mb": 2600.0}]
    _write_jsonl(exp, rows)
    fit = readout.maybe_refit(repo, min_new_rows=5)
    assert fit is not None, "sparse refit returned None"
    kv = fit["vram"]["ltx"]
    assert kv["rows"] == 2 and kv["rows"] < 3, "expected 2 sparse rows, got %s" % kv["rows"]
    hand_base, hand_k = readout.HAND_VRAM["ltx"]
    assert kv["k_gb_per_mpxf"] == hand_k, \
        "sparse history should keep hand k %s, got %s" % (hand_k, kv["k_gb_per_mpxf"])


def check_9_missing_and_corrupt_no_raise():
    empty_repo = tempfile.mkdtemp(prefix="t22missing_")
    res = readout.maybe_refit(empty_repo)          # no experiments.jsonl at all
    assert res is None or isinstance(res, dict), "missing history: bad return %r" % (res,)

    corrupt_repo = tempfile.mkdtemp(prefix="t22corrupt_")
    exp = os.path.join(corrupt_repo, readout.EXPERIMENTS)
    os.makedirs(os.path.dirname(exp), exist_ok=True)
    with open(exp, "w") as f:
        f.write("not json at all\n{ still not json ]\n\n")
    res2 = readout.maybe_refit(corrupt_repo)       # exists but all lines corrupt
    assert res2 is None or isinstance(res2, dict), "corrupt history: bad return %r" % (res2,)
    # and rendering with a None fit / weird cfg must never raise either
    out = readout.render_readout({"backend": "ltx"}, None, res2)
    assert out and "READOUT" in strip(out)


def check_10_sub_threshold_growth_parses_once():
    """jsonl newer than the cache but < min_new_rows new rows leaves the cache untouched; the
    memo must stop that state from re-parsing the whole history on every call (every keystroke)."""
    repo = tempfile.mkdtemp(prefix="t22memo_")
    exp = os.path.join(repo, readout.EXPERIMENTS)
    rows, _K, _COEF = _synth_rows()
    _write_jsonl(exp, rows)
    first = readout.maybe_refit(repo, min_new_rows=5)
    with open(exp, "a") as f:                      # one new run: below the refit threshold
        f.write(json.dumps(rows[0]) + "\n")
    cache = os.path.join(repo, readout.FIT_CACHE)
    t = os.path.getmtime(cache) + 10
    os.utime(exp, (t, t))                          # jsonl strictly newer than the cache
    calls = {"n": 0}
    orig = readout._read_experiments

    def spy(path):
        calls["n"] += 1
        return orig(path)

    readout._read_experiments = spy
    try:
        outs = [readout.maybe_refit(repo, min_new_rows=5) for _ in range(5)]
    finally:
        readout._read_experiments = orig
    assert calls["n"] == 1, "sub-threshold history re-parsed %d times (want 1)" % calls["n"]
    assert all(o == first for o in outs), "memoized fit differs from the cached one"


def check_11_failed_runs_excluded_from_refit():
    """experiment_log records FAILED runs too; their peak VRAM / phase times describe a crash,
    not the model. Only status=="done" rows (or rows with no status) may feed the fit."""
    rows, K, COEF = _synth_rows()                   # no "status" field -> kept
    done = [dict(r, status="done") for r in rows[:5]]
    failed = []
    for r in rows[:10] + rows[:10]:                  # 20 failed rows, all wildly off -> would own the median
        f = dict(r, status="failed", peak_vram_mb=r["peak_vram_mb"] * 3.0)
        f["phase_secs"] = {k: v * 7.0 for k, v in r["phase_secs"].items()}
        failed.append(f)
    fit = readout._refit(rows + done + failed)
    kv, tv = fit["vram"]["ltx"], fit["time"]["ltx"]
    assert kv["rows"] == len(rows) + len(done), "vram fit used %s rows (failed rows leaked?)" % kv["rows"]
    assert abs(kv["k_gb_per_mpxf"] - K) / K <= 0.25, "failed rows skewed k: %s" % kv["k_gb_per_mpxf"]
    assert abs(tv["COEF"] - COEF) / COEF <= 0.25, "failed rows skewed COEF: %s" % tv["COEF"]
    assert tv["rows"] == len(rows) + len(done), "time fit used %s rows" % tv["rows"]


def check_12_nonpositive_k_keeps_hand():
    """Rows whose peak sits BELOW the hand base give k<=0; accepting it would make longer clips
    read LESS VRAM. The fit must fall back to the hand k and the estimate stay monotonic."""
    rows = [{"backend": "ltx", "status": "done", "width": W, "height": H, "seg_frames": sf,
             "steps": 30, "nseg": 1, "peak_vram_mb": 1.5 * 1024.0}             # 1.5 GB < base 2.3
            for (W, H, sf) in ((512, 320, 49), (704, 480, 49), (768, 512, 97), (704, 480, 121))]
    fit = readout._refit(rows)
    kv = fit["vram"]["ltx"]
    assert kv["k_gb_per_mpxf"] > 0, "accepted a non-positive k %s" % kv["k_gb_per_mpxf"]
    assert kv["k_gb_per_mpxf"] == readout.HAND_VRAM["ltx"][1], "expected the hand k, got %s" % kv
    short = readout.vram_est(base_cfg(seg_frames=25), fit)[0]
    long_ = readout.vram_est(base_cfg(seg_frames=121), fit)[0]
    assert long_ > short, "longer clips must not read less VRAM (%s !> %s)" % (long_, short)


def check_13_log_shrink_refits():
    """A rotated/reset experiments.jsonl has FEWER rows than the cache saw; len(rows)-prev goes
    negative and the fit used to never update again. Shrinkage must refit immediately."""
    repo = tempfile.mkdtemp(prefix="t22shrink_")
    exp = os.path.join(repo, readout.EXPERIMENTS)
    rows, K, _COEF = _synth_rows()
    _write_jsonl(exp, rows * 2)                      # 22 rows -> cached fit, row_count 22
    first = readout.maybe_refit(repo, min_new_rows=5)
    assert first and first.get("row_count") == 22, first and first.get("row_count")
    new_rows = []                                     # rotated log: 6 fresh rows at k = 2*K
    for r in rows[:6]:
        mpxf = r["width"] * r["height"] * r["seg_frames"] / 1e6
        new_rows.append(dict(r, peak_vram_mb=(2.3 + 2 * K * mpxf) * 1024.0))
    _write_jsonl(exp, new_rows)
    t = os.path.getmtime(os.path.join(repo, readout.FIT_CACHE)) + 10
    os.utime(exp, (t, t))
    fit = readout.maybe_refit(repo, min_new_rows=5)
    assert fit.get("row_count") == 6, "shrunken log not refit (row_count %s)" % fit.get("row_count")
    assert abs(fit["vram"]["ltx"]["k_gb_per_mpxf"] - 2 * K) / (2 * K) <= 0.25, \
        "refit after rotation didn't pick up the new rows: k=%s" % fit["vram"]["ltx"]["k_gb_per_mpxf"]


def _director_redirects(nseg, every):
    """Mirror of director.py's main loop redirect gate: after shot 1 and after every continuation,
    redirect iff the clip is still short of target (i.e. more shots follow) and
    (seg_idx - last_redirect) >= redirect_every."""
    last, n, seg = 0, 0, 1
    if seg < nseg and (seg - last) >= every:
        n, last = n + 1, seg
    while seg < nseg:
        seg += 1
        if seg < nseg and (seg - last) >= every:
            n, last = n + 1, seg
    return n


def check_14_fitted_time_director_seams():
    """The '(fit ...)' ETA must charge SEAM once per ACTUAL director redirect: hold/balanced
    redirect every 3rd shot (nseam // 3), evolve every shot."""
    tf = {"COEF": 1.0, "WARM": 0.0, "DECODE": 0.0, "LOAD": 0.0, "SEAM": 90.0, "SEG_REF": 49.0, "rows": 6}
    fit = {"vram": {}, "time": {"ltx": tf}}
    for steady, every in (("hold", 3), ("balanced", 3), ("evolve", 1)):
        for nseg in range(1, 30):
            cfg = base_cfg(mode="director", chain=nseg > 1, nseg=nseg, steadiness=steady)
            base = readout._fitted_time(dict(cfg, mode="single"), fit)
            got = readout._fitted_time(cfg, fit) - base
            want = 90.0 * _director_redirects(nseg, every)
            assert abs(got - want) < 1e-6, "%s nseg=%d: seam time %s != %s" % (steady, nseg, got, want)


def main():
    checks = [
        check_1_render_titles, check_2_line_widths, check_2b_chain_boxes_fit_their_cell,
        check_2c_value_tags_stay_visible,
        check_3_vram_monotonic_and_anchor,
        check_4_ram_ordering, check_5_drift, check_6_quality_range,
        check_7_refit_recovers_and_caches, check_8_sparse_history_keeps_hand,
        check_9_missing_and_corrupt_no_raise, check_10_sub_threshold_growth_parses_once,
        check_11_failed_runs_excluded_from_refit, check_12_nonpositive_k_keeps_hand,
        check_13_log_shrink_refits, check_14_fitted_time_director_seams,
    ]
    for c in checks:
        c()
        print("ok  %s" % c.__name__)
    print("ALL_T22_CHECKS_PASS")


if __name__ == "__main__":
    main()
