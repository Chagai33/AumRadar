"""The release gate (DESIGN: "silence is never a sin"): a 🔴 artist is a cleanup
candidate only after `min_releases` own singles in the measured /release weeks.
Manual 'remove' overrides the gate; 'protect' / an active pin still win over everything."""
from backend.core.health import HealthEngine, DEFAULT_BANDS
from backend.core import release

ENGINE = HealthEngine()            # constructor does no I/O
RED = {"red"}


def resolve(raw, rec=None, releases=0, min_releases=4):
    return ENGINE._resolve(raw, DEFAULT_BANDS, RED, rec or {}, releases, min_releases)


def test_red_without_enough_releases_is_held_back():
    r = resolve(0.0, releases=3)
    assert r["band"] == "red" and r["candidate"] is False and r["gated"] is True


def test_red_with_enough_releases_is_a_candidate():
    r = resolve(0.0, releases=4)
    assert r["candidate"] is True and r["gated"] is False


def test_gate_off_restores_old_behaviour():
    assert resolve(0.0, releases=0, min_releases=0)["candidate"] is True


def test_gate_only_applies_inside_candidate_bands():
    r = resolve(1.0, releases=0)                     # green: never a candidate anyway
    assert r["band"] == "green" and r["candidate"] is False and r["gated"] is False


def test_manual_remove_ignores_the_gate():
    r = resolve(0.0, {"disposition": "remove"}, releases=0)
    assert r["candidate"] is True and r["band"] == "red"


def test_protect_still_wins():
    r = resolve(0.0, {"disposition": "protect"}, releases=10)
    assert r["candidate"] is False and r["gated"] is False


def test_primary_release_counts_match_release_quality():
    ev = [
        {"artist_uri": "a", "role": "primary", "outcome": "miss", "release_week": 330},
        {"artist_uri": "a", "role": "primary", "outcome": "hit", "release_week": 331},
        {"artist_uri": "a", "role": "secondary", "outcome": "miss", "release_week": 332},
        {"artist_uri": "a", "role": "primary", "outcome": "album", "release_week": 333},
        {"artist_uri": "b", "role": "secondary", "outcome": "miss", "release_week": 333},
    ]
    counts = release.primary_release_counts(ev)
    q = release.release_quality(ev, {"a", "b"}, current_week=335)
    assert counts == {"a": 2}
    assert q["a"]["primary_releases"] == counts["a"]
    assert "b" not in counts and q["b"]["primary_releases"] == 0
    assert release.primary_release_counts(ev, followed_uris={"b"}) == {}
