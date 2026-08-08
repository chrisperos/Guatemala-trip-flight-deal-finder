"""Learned cache of city pairs that never return an itinerary.

Guatemala has no service whatsoever from a meaningful slice of US airports --
RSW and PBI produced 0 usable results out of 14 attempts each during testing.
Re-querying them every day is pure waste, and worse, the retries they provoke
are what tip the scanner into real rate-limiting.

So the scanner learns. A pair is retired after `DEAD_AFTER` consecutive empty
results with no successes, and is re-tested occasionally in case an airline
adds the route (they do -- seasonal Guatemala service appears and disappears).
"""

from __future__ import annotations

import json
import os
import random
from datetime import date

import config

DEAD_FILE = os.path.join(config.DATA_DIR, "dead_routes.json")

DEAD_AFTER = 8          # consecutive empties before retiring a pair
RESURRECT_CHANCE = 0.04  # ~1 in 25 runs, re-test a retired pair anyway


def load() -> dict:
    if not os.path.exists(DEAD_FILE):
        return {}
    try:
        with open(DEAD_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def save(data: dict) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(DEAD_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def is_dead(data: dict, origin: str, dest: str) -> bool:
    rec = data.get(f"{origin}->{dest}")
    if not rec or rec.get("ok", 0) > 0:
        return False
    if rec.get("empty", 0) < DEAD_AFTER:
        return False
    return random.random() > RESURRECT_CHANCE


def update(data: dict, ok_routes: list[str], empty_routes: list[str]) -> dict:
    """Fold one run's outcomes into the cache.

    A route that produced ANY result this run is healthy, full stop -- its
    empty streak resets and its empties are not counted. Without that guard a
    seasonal route like DEN-GUA, which is served on some dates and not others,
    accumulates empties from the unserved dates and gets retired despite
    working fine.
    """
    today = date.today().isoformat()
    healthy = set(ok_routes)

    for r in healthy:
        rec = data.setdefault(r, {"ok": 0, "empty": 0})
        rec["ok"] = rec.get("ok", 0) + 1
        rec["empty"] = 0
        rec["last_ok"] = today

    for r in empty_routes:
        if r in healthy:
            continue
        rec = data.setdefault(r, {"ok": 0, "empty": 0})
        rec["empty"] = rec.get("empty", 0) + 1
        rec["last_empty"] = today
    return data


def filter_tasks(tasks: list[tuple], data: dict) -> tuple[list[tuple], int]:
    """Drop (origin, dest, ...) tasks whose city pair is retired."""
    keep, skipped = [], 0
    for t in tasks:
        if len(t) >= 2 and is_dead(data, t[0], t[1]):
            skipped += 1
        else:
            keep.append(t)
    return keep, skipped


def summary(data: dict) -> list[str]:
    return sorted(r for r, v in data.items()
                  if v.get("ok", 0) == 0 and v.get("empty", 0) >= DEAD_AFTER)
