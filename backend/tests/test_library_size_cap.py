"""Playlist-size cap on the library counts (2026-11-08): a song that sits only in a
backup dump (a playlist over the cap) is not "something I have of theirs"."""
from backend.core.library import LibraryEngine, DEFAULT_MAX_PLAYLIST_SIZE

ROWS = [
    {"i": "t1", "n": "liked, in dump only", "p": ["dump"], "l": 1},
    {"i": "t2", "n": "not liked, in dump only", "p": ["dump"]},
    {"i": "t3", "n": "not liked, in a real playlist + dump", "p": ["real", "dump"]},
    {"i": "t4", "n": "not liked, no playlist", "p": []},
]


def test_recount_without_cap_counts_everything():
    c = LibraryEngine._recount(ROWS, ignored={})
    assert c == {"liked": 1, "playlists": 2, "songs": 3}


def test_recount_drops_songs_that_live_only_in_a_dump():
    c = LibraryEngine._recount(ROWS, ignored={"dump": 9000})
    assert c == {"liked": 1, "playlists": 1, "songs": 2}   # t2 vanishes, t1 stays (liked)


def test_cap_parsing():
    assert LibraryEngine._cap(None) == DEFAULT_MAX_PLAYLIST_SIZE
    assert LibraryEngine._cap("abc") == DEFAULT_MAX_PLAYLIST_SIZE
    assert LibraryEngine._cap(0) is None            # 0 = off
    assert LibraryEngine._cap(100) == 100


def test_ignored_uses_index_sizes_when_present():
    eng = LibraryEngine()
    index = {"playlists": {"dump": {"n": "All", "s": 11000}, "real": {"n": "Wedding", "s": 98}}}
    assert eng._ignored(index, 500) == {"dump": 11000}
    assert eng._ignored(index, None) == {}
