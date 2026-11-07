"""Regression: a week must be matched against EVERY legitimate weekly scan in history.

A cap of 10 candidates (2026-08 → 2026-11-07) silently dropped the older scans once
history grew past ten, so weeks 324–326 read as "no scan contains this playlist" while
their scan sat in history at a 100% match."""
from backend.core import release


def _scan(i):
    # 17 completed one-week scans, each big enough to count — newest last.
    return {"id": f"2026{i:04d}", "completed": True, "partial_scan": False,
            "start_date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "end_date": f"2026-{1 + (i + 6) // 28:02d}-{1 + (i + 6) % 28:02d}",
            "track_count": release.MIN_WEEK_TRACKS + i}


def test_every_valid_scan_is_a_candidate():
    history = [_scan(i) for i in range(17)]
    valid = release._valid_weekly_scans(history, release.MIN_WEEK_TRACKS)
    assert len(valid) == 17
    cands = release._candidate_scan_ids(valid[0]["id"], history)
    assert set(cands) == {s["id"] for s in valid}
    assert cands[0] == valid[0]["id"]          # the hint is still tried first


def test_proposed_hint_is_not_duplicated():
    history = [_scan(i) for i in range(3)]
    cands = release._candidate_scan_ids(history[1]["id"], history)
    assert len(cands) == len(set(cands)) == 3
