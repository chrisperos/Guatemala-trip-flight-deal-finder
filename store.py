"""Persistence: append-only price history, plus alert de-duplication state.

CSV rather than SQLite on purpose -- it diffs cleanly in git, so when the
scanner runs in CI the daily commit is a readable record of how the price
moved, and you can open it in a spreadsheet without tooling.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone

import config

FIELDS = [
    "scanned_at", "kind", "route", "origin", "return_airport",
    "depart_date", "return_date", "trip_days",
    "fare_usd", "bag_usd", "all_in_usd",
    "airlines", "total_stops", "max_layover_min", "overnight_layover",
    "all_december", "bag_confidence", "url",
]


def _ensure_dir() -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)


# ------------------------------------------------------- raw quote cache
# Scanning costs ~7 minutes; re-ranking costs milliseconds. Caching the raw
# quotes means Claude can research baggage policy, correct the bag costs, and
# re-rank the SAME fares -- rather than re-scanning and comparing against a
# subtly different set of prices.

RAW_CACHE = os.path.join(config.DATA_DIR, "raw_quotes.json.gz")


def save_raw_quotes(outbound, inbound) -> None:
    import gzip
    from dataclasses import asdict

    _ensure_dir()
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "outbound": [asdict(q) for q in outbound],
        "inbound": [asdict(q) for q in inbound],
    }
    with gzip.open(RAW_CACHE, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)


def load_raw_quotes():
    """-> (outbound, inbound, saved_at) or (None, None, None)."""
    import gzip
    from search import Leg, Quote

    if not os.path.exists(RAW_CACHE):
        return None, None, None
    try:
        with gzip.open(RAW_CACHE, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError, EOFError):
        return None, None, None

    def rebuild(rows):
        out = []
        for r in rows:
            legs = [Leg(**l) for l in r.get("legs", [])]
            r = dict(r, legs=legs)
            out.append(Quote(**r))
        return out

    try:
        return (rebuild(payload.get("outbound", [])),
                rebuild(payload.get("inbound", [])),
                payload.get("saved_at"))
    except TypeError:
        # cache written by an older schema -- ignore rather than crash
        return None, None, None


def option_row(opt) -> dict:
    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": opt.kind,
        "route": opt.route,
        "origin": opt.outbound.origin,
        "return_airport": opt.inbound.destination if opt.inbound
                          else opt.outbound.origin,
        "depart_date": opt.depart_date,
        "return_date": opt.return_date,
        "trip_days": opt.trip_days,
        "fare_usd": opt.fare_usd,
        "bag_usd": opt.bag_usd,
        "all_in_usd": opt.all_in_usd,
        "airlines": "|".join(opt.airlines),
        "total_stops": opt.total_stops,
        "max_layover_min": opt.max_layover_min,
        "overnight_layover": int(opt.has_overnight_layover),
        "all_december": int(opt.all_december),
        "bag_confidence": opt.bag_confidence,
        "url": opt.outbound.url,
    }


def append_history(options) -> int:
    _ensure_dir()
    exists = os.path.exists(config.HISTORY_CSV)
    with open(config.HISTORY_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        for opt in options:
            w.writerow(option_row(opt))
    return len(options)


def all_time_low() -> int | None:
    if not os.path.exists(config.HISTORY_CSV):
        return None
    lo = None
    with open(config.HISTORY_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                v = int(row["all_in_usd"])
            except (KeyError, ValueError):
                continue
            lo = v if lo is None else min(lo, v)
    return lo


def load_alert_state() -> dict:
    if not os.path.exists(config.ALERT_STATE):
        return {"best_alerted_usd": None, "history": []}
    try:
        with open(config.ALERT_STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {"best_alerted_usd": None, "history": []}


def save_alert_state(state: dict) -> None:
    _ensure_dir()
    with open(config.ALERT_STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def record_alert(state: dict, opt, price: int) -> None:
    state["best_alerted_usd"] = price
    state.setdefault("history", []).append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "all_in_usd": price,
        "route": opt.route,
        "depart_date": opt.depart_date,
        "return_date": opt.return_date,
    })
    state["history"] = state["history"][-100:]


def should_alert(opt, state: dict, prior_low: int | None) -> tuple[bool, str]:
    """-> (send_it, reason). Kept boring and explicit; alert spam kills trust."""
    price = opt.all_in_usd
    best = state.get("best_alerted_usd")

    if price <= config.ALERT_THRESHOLD_USD:
        if best is None or price <= best - config.MIN_IMPROVEMENT_USD:
            return True, f"at or under your ${config.ALERT_THRESHOLD_USD} target"

    if config.ALERT_ON_NEW_LOW and prior_low is not None:
        if price <= prior_low - config.MIN_IMPROVEMENT_USD:
            if best is None or price <= best - config.MIN_IMPROVEMENT_USD:
                return True, f"new all-time low (was ${prior_low})"

    return False, ""


def should_alert_preferred(opt, state: dict) -> tuple[bool, str]:
    """A second, independent alert for a trip that matches taste AND is cheap.

    Deliberately separate from should_alert so the two can never cannibalise
    each other. The cheapest option is always evaluated on its own terms --
    a trip matching none of the preferences still alerts if it is under
    target. This one only ADDS a notification; it never withholds one.
    """
    if opt.pref_score < config.PREF_ALERT_MIN_SCORE:
        return False, ""
    if opt.all_in_usd > config.ALERT_THRESHOLD_USD:
        return False, ""
    best = state.get("best_pref_alerted_usd")
    if best is None or opt.all_in_usd <= best - config.MIN_IMPROVEMENT_USD:
        return True, (f"{opt.pref_label} match for your preferences, and under "
                      f"${config.ALERT_THRESHOLD_USD}")
    return False, ""


def record_pref_alert(state: dict, price: int) -> None:
    state["best_pref_alerted_usd"] = price
