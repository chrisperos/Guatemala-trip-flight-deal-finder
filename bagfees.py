"""Baggage cost lookup, backed by a Claude-maintained JSON cache.

Deliberately NOT a hardcoded table of my guesses. Real policy varies by fare
class (a legacy carrier's Main Cabin to Central America often includes a free
checked bag while its Basic Economy does not), changes without notice, and is
the single biggest source of error when comparing a $340 Spirit fare against a
$395 Copa fare.

So: `data/baggage_policies.json` is the source of truth, Claude refreshes it
with live web search, and every entry carries a source URL and a verified date.
Anything missing falls back to a deliberately PESSIMISTIC default, so unknown
carriers look worse than they are rather than better -- errors push you toward
verifying, not toward booking something worse than advertised.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime

import config

POLICY_FILE = os.path.join(config.DATA_DIR, "baggage_policies.json")

# Used when a carrier is absent from the cache. Pessimistic on purpose.
UNKNOWN_CHECKED_BAG_USD = 75
UNKNOWN_CARRYON_USD = 55

# A policy older than this is reported as stale so Claude re-verifies it.
STALE_AFTER_DAYS = 21


@dataclass
class BagPolicy:
    airline: str
    checked_bag_usd: int          # first checked bag, ONE WAY, <=50lb
    carryon_free: bool            # full overhead-bin carry-on included?
    carryon_usd: int              # cost if not free
    personal_item_free: bool
    notes: str = ""
    source_url: str = ""
    verified_on: str = ""         # ISO date
    fare_class: str = ""          # e.g. "basic economy" -- what this describes

    @property
    def is_stale(self) -> bool:
        if not self.verified_on:
            return True
        try:
            age = (date.today() - datetime.fromisoformat(self.verified_on).date()).days
        except ValueError:
            return True
        return age > STALE_AFTER_DAYS

    @property
    def is_guess(self) -> bool:
        return not self.source_url


def _default(airline: str) -> BagPolicy:
    return BagPolicy(
        airline=airline,
        checked_bag_usd=UNKNOWN_CHECKED_BAG_USD,
        carryon_free=False,
        carryon_usd=UNKNOWN_CARRYON_USD,
        personal_item_free=True,
        notes="NOT VERIFIED - pessimistic placeholder. Needs Claude lookup.",
    )


def load_policies() -> dict[str, BagPolicy]:
    if not os.path.exists(POLICY_FILE):
        return {}
    with open(POLICY_FILE, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out: dict[str, BagPolicy] = {}
    for name, d in raw.items():
        out[name.strip().lower()] = BagPolicy(
            airline=d.get("airline", name),
            checked_bag_usd=int(d.get("checked_bag_usd", UNKNOWN_CHECKED_BAG_USD)),
            carryon_free=bool(d.get("carryon_free", False)),
            carryon_usd=int(d.get("carryon_usd", UNKNOWN_CARRYON_USD)),
            personal_item_free=bool(d.get("personal_item_free", True)),
            notes=d.get("notes", ""),
            source_url=d.get("source_url", ""),
            verified_on=d.get("verified_on", ""),
            fare_class=d.get("fare_class", ""),
        )
    return out


def save_policies(policies: dict[str, BagPolicy]) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    payload = {}
    for key, p in policies.items():
        payload[key] = {
            "airline": p.airline,
            "checked_bag_usd": p.checked_bag_usd,
            "carryon_free": p.carryon_free,
            "carryon_usd": p.carryon_usd,
            "personal_item_free": p.personal_item_free,
            "notes": p.notes,
            "source_url": p.source_url,
            "verified_on": p.verified_on,
            "fare_class": p.fare_class,
        }
    with open(POLICY_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def lookup(airline: str, policies: dict[str, BagPolicy] | None = None) -> BagPolicy:
    policies = policies if policies is not None else load_policies()
    key = (airline or "").strip().lower()
    if key in policies:
        return policies[key]
    # tolerate "COPA" vs "Copa Airlines", "American" vs "American Airlines"
    for name, p in policies.items():
        if name and (name in key or key in name):
            return p
    return _default(airline)


def bag_cost_for(airlines: list[str], directions: int,
                 policies: dict[str, BagPolicy] | None = None) -> tuple[int, list[BagPolicy]]:
    """Estimated baggage cost for an itinerary.

    `directions` is 1 for a one-way leg, 2 for a round trip. When several
    carriers appear on one itinerary the most expensive governs, since the
    operating carrier of the first leg usually sets the bag rules and we would
    rather over-estimate.
    """
    policies = policies if policies is not None else load_policies()
    if config.CHECKED_BAGS <= 0:
        return 0, []
    used = [lookup(a, policies) for a in (airlines or ["unknown"])]
    worst = max(used, key=lambda p: p.checked_bag_usd)
    return worst.checked_bag_usd * config.CHECKED_BAGS * directions, used


def airlines_needing_lookup(all_airlines: set[str]) -> list[str]:
    """Carriers with no cached policy, a stale one, or an unsourced guess."""
    policies = load_policies()
    todo = []
    for a in sorted(all_airlines):
        p = lookup(a, policies)
        if p.is_guess or p.is_stale:
            todo.append(a)
    return todo
