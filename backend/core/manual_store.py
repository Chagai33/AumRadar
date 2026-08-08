# -*- coding: utf-8 -*-
"""Artist Health Engine — the manual-override store (STAGE3.md §4ג / §13.4 / §12.7).

`cache/artist_manual.json` holds the user's per-artist overrides that sit BESIDE the
computed RANK (never rewriting the truth):

    { "<artist_uri>": { "disposition": "protect|remove|none",
                        "score_floor": <float>, "pinned_until": "<iso>",
                        "notes": "<str>", "tags": [<str>, ...] } }

This module is the SINGLE safe write path for that file. Both `POST /api/health/manual`
(now) and the migrated `/cleanup/protect` (Stage 3ג) go through `update()` so two
writers can't clobber each other: it does a targeted read-merge-write (read the whole
map → touch ONE artist's record → write back), which keeps the single-writer window
tiny. Real concurrency control arrives with the SQLite DB in Stage 4; for one user this
is enough (STAGE3.md §12.7).

Conflict resolution (protect-wins) and soft-prune (ignore non-followed records in
memory but NEVER auto-delete them) are READ-time concerns applied when the overrides
are consumed — this module only stores what it is given.
"""
from typing import Optional

from .storage_manager import storage

MANUAL_FILE = "cache/artist_manual.json"

# One absolute, mutually-exclusive intent per artist so "protect AND remove" can't
# exist (STAGE3.md §10.2). The orthogonal fields (score_floor / pinned_until / notes /
# tags) can coexist with any disposition.
VALID_DISPOSITIONS = ("protect", "remove", "none")
_FIELDS = ("disposition", "score_floor", "pinned_until", "notes", "tags")


def load() -> dict:
    """The whole overrides map (empty dict if the file doesn't exist yet — the default
    pattern used everywhere, so a first run never crashes; STAGE3.md §13.6)."""
    return storage.load_json(MANUAL_FILE, default={}) or {}


def get(uri: str) -> dict:
    """One artist's override record, or {} if none."""
    return (load().get(uri) or {}) if uri else {}


def update(uri: str, fields: dict) -> dict:
    """Targeted read-merge-write for ONE artist (§13.4). Only the keys present with a
    non-None value are changed; anything omitted / None is left exactly as it was, so a
    partial update (e.g. just a note) never wipes an existing floor or disposition.
    `disposition` is validated to the allowed set; an unknown value is dropped rather
    than stored. Returns the artist's merged record."""
    if not uri:
        raise ValueError("artist_uri is required")
    data = load()
    rec = dict(data.get(uri) or {})
    for k in _FIELDS:
        v = fields.get(k)
        if v is None:
            continue
        if k == "disposition" and v not in VALID_DISPOSITIONS:
            continue                       # ignore garbage, don't persist it
        rec[k] = v
    data[uri] = rec
    if not storage.save_json(MANUAL_FILE, data):
        raise RuntimeError("could not save artist_manual.json")
    return rec
