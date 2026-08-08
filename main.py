"""Entry point. Scans, recombines, shortlists, persists, notifies.

    python main.py --tier 1                 # daily scan, ~30 origins
    python main.py --tier 2 --mode both     # wider weekend sweep
    python main.py --tier 1 --max-dates 5   # quick smoke test

Writes to data/:
    shortlist.json   <- the file Claude reads and reasons over
    latest_report.md <- human-readable summary
    history.csv      <- append-only price record
    dead_routes.json <- learned city pairs with no Guatemala service
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import date as _date, datetime, timedelta, timezone

import config
import airports
import bagfees
import deadroutes
import notify
import search
import store
import strategy


def _log(msg: str) -> None:
    print(msg, flush=True)


def _progress(done: int, total: int, stats: search.ScanStats) -> None:
    pct = 100.0 * done / max(total, 1)
    sys.stdout.write(f"\r    {done}/{total} ({pct:5.1f}%)  {stats}      ")
    sys.stdout.flush()
    if done == total:
        sys.stdout.write("\n")


def _ok_routes(quotes: list[search.Quote]) -> list[str]:
    """Keys must match deadroutes.route_key -- per (pair, mode), not per pair."""
    return sorted({f"{q.origin}->{q.destination}:{q.kind}" for q in quotes})


def _warn_if_degraded(stats: search.ScanStats) -> None:
    if stats.total <= 50:
        return
    if stats.block_rate > 0.15:
        _log(f"  !! {100*stats.block_rate:.0f}% of requests were REFUSED by "
             f"Google. Lower REQUESTS_PER_SECOND in config.py, or set "
             f"FLIGHTWATCH_PROXY.")
    if stats.error_rate > 0.02:
        _log(f"  !! {100*stats.error_rate:.0f}% of requests crashed in OUR "
             f"parsing code -- this is a bug, not rate-limiting:")
        for s in stats.error_samples:
            _log(f"       {s}")


# ------------------------------------------------------------------- scanning

def scan_oneway(origins: list[str], max_dates: int | None,
                dead: dict) -> list[strategy.TripOption]:
    deps = strategy.depart_dates()
    rets = strategy.return_dates()
    if max_dates:
        deps = deps[:max_dates]
        rets = rets[:max_dates + config.MAX_TRIP_DAYS]

    out_tasks = [(o, d, day) for o in origins
                 for d in config.DESTINATIONS for day in deps]
    in_tasks = [(d, o, day) for d in config.DESTINATIONS
                for o in origins for day in rets]

    out_tasks, sk1 = deadroutes.filter_tasks(out_tasks, dead)
    in_tasks, sk2 = deadroutes.filter_tasks(in_tasks, dead)
    if sk1 or sk2:
        _log(f"  skipped {sk1 + sk2} queries on known-dead routes")

    _log(f"  outbound legs: {len(out_tasks)} queries")
    t0 = time.time()
    outbound, s1 = search.parallel(out_tasks, search.search_oneway,
                                   on_progress=_progress)
    _log(f"    -> {len(outbound)} quotes in {time.time()-t0:.0f}s")
    _warn_if_degraded(s1)

    _log(f"  return legs:   {len(in_tasks)} queries")
    t0 = time.time()
    inbound, s2 = search.parallel(in_tasks, search.search_oneway,
                                  on_progress=_progress)
    _log(f"    -> {len(inbound)} quotes in {time.time()-t0:.0f}s")
    _warn_if_degraded(s2)

    deadroutes.update(dead, _ok_routes(outbound) + _ok_routes(inbound),
                      s1.empty_routes + s2.empty_routes)

    if not outbound or not inbound:
        _log("  !! no usable one-way quotes; skipping recombination")
        return []

    store.save_raw_quotes(outbound, inbound)
    opts = strategy.combine_oneways(outbound, inbound)
    _log(f"  recombined into {len(opts):,} valid trip options")
    return opts


def rerank_from_cache() -> list[strategy.TripOption]:
    """Re-cost and re-rank the cached fares without re-querying Google.

    This is what makes the research loop cheap: scan once, then after
    correcting a baggage policy, re-rank the SAME fares instantly. Re-scanning
    instead would compare against subtly different prices and muddy the effect
    of the correction.
    """
    outbound, inbound, saved_at = store.load_raw_quotes()
    if not outbound or not inbound:
        _log("  !! no cached quotes -- run a scan first (without --rerank)")
        return []
    _log(f"  loaded {len(outbound) + len(inbound):,} cached quotes "
         f"from {saved_at}")
    opts = strategy.combine_oneways(outbound, inbound)
    _log(f"  recombined into {len(opts):,} valid trip options")
    return opts


def scan_roundtrip(origins: list[str], max_dates: int | None,
                   dead: dict) -> list[strategy.TripOption]:
    deps = strategy.depart_dates()
    if max_dates:
        deps = deps[:max_dates]

    # Stage 1: coarse -- every origin/date at the coarse trip length(s).
    coarse = []
    for o in origins:
        for d in config.DESTINATIONS:
            for day in deps:
                for L in config.COARSE_TRIP_LENGTHS:
                    ret = day + timedelta(days=L)
                    if ret <= config.LATEST_RETURN:
                        coarse.append((o, d, day, ret))
    coarse, sk = deadroutes.filter_tasks(coarse, dead)
    if sk:
        _log(f"  skipped {sk} known-dead round-trip pairs")

    _log(f"  coarse round-trip sweep: {len(coarse)} queries")
    q1, s1 = search.parallel(coarse, search.search_roundtrip,
                             on_progress=_progress)
    _log(f"    -> {len(q1)} quotes")
    _warn_if_degraded(s1)
    deadroutes.update(dead, _ok_routes(q1), s1.empty_routes)

    opts = strategy.wrap_roundtrips(q1)
    if not opts:
        return []

    # Stage 2: refine -- revisit the best (origin, depart) pairs at other
    # trip lengths, since the cheapest 19-day trip and the cheapest 25-day
    # trip are rarely the same itinerary.
    best = sorted(opts, key=lambda o: o.all_in_usd)[:config.TOP_N_REFINE]
    seen = {(o.outbound.origin, o.depart_date) for o in best}
    refine = []
    for origin, dep_str in seen:
        dep = _date.fromisoformat(dep_str)
        for L in config.REFINE_TRIP_LENGTHS:
            if L in config.COARSE_TRIP_LENGTHS:
                continue
            ret = dep + timedelta(days=L)
            if ret > config.LATEST_RETURN:
                continue
            for d in config.DESTINATIONS:
                refine.append((origin, d, dep, ret))

    if refine:
        _log(f"  refine sweep: {len(refine)} queries")
        q2, s2 = search.parallel(refine, search.search_roundtrip,
                                 on_progress=_progress)
        _log(f"    -> {len(q2)} quotes")
        _warn_if_degraded(s2)
        opts += strategy.wrap_roundtrips(q2)

    _log(f"  {len(opts):,} round-trip options")
    return opts


# -------------------------------------------------------------------- outputs

def _picks(short: list[strategy.TripOption]) -> dict:
    """Name the rows worth arguing about, by rank in the options list.

    The traveler explicitly wants BOTH the outright cheapest and the closest
    match surfaced every time, then wants to make the call themselves. So the
    scanner names them rather than leaving Claude to infer which is which.
    """
    if not short:
        return {}

    def ref(o):
        return {
            "rank": short.index(o) + 1,
            "all_in_usd": o.all_in_usd,
            "route": o.route,
            "dates": f"{o.depart_date} -> {o.return_date}",
            "trip_days": o.trip_days,
            "preference_score": o.pref_score,
        }

    cheapest = min(short, key=lambda o: o.all_in_usd)
    best_pref = max(short, key=lambda o: (o.pref_score, -o.all_in_usd))
    near = [o for o in short
            if o.all_in_usd <= cheapest.all_in_usd + config.IDEAL_PREMIUM_USD]
    best_value = max(near, key=lambda o: (o.pref_score, -o.all_in_usd))

    picks = {"cheapest": ref(cheapest),
             "best_preference_match": ref(best_pref),
             "best_value_within_premium": ref(best_value)}

    home = [o for o in short
            if config.HOME_AIRPORT in (o.outbound.origin,
                                       o.inbound.destination if o.inbound
                                       else o.outbound.origin)]
    picks["cheapest_touching_home_airport"] = (
        ref(min(home, key=lambda o: o.all_in_usd)) if home
        else f"no {config.HOME_AIRPORT} itinerary in this scan")

    picks["premium_for_preference_usd"] = (
        best_pref.all_in_usd - cheapest.all_in_usd)
    return picks


def export_shortlist(short: list[strategy.TripOption], meta: dict) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    all_air = sorted({a for o in short for a in o.airlines})
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "meta": meta,
        "traveler_constraints": {
            "min_trip_days": config.MIN_TRIP_DAYS,
            "preferred_min_trip_days": config.PREFERRED_MIN_TRIP_DAYS,
            "max_trip_days": config.MAX_TRIP_DAYS,
            "earliest_depart": config.EARLIEST_DEPART.isoformat(),
            "latest_return": config.LATEST_RETURN.isoformat(),
            "ideal": "entire trip inside December 2026",
            "origin_flexibility": "any airport in the lower 48; may return to a "
                                  "different airport than departed from",
            "checked_bags_required": config.CHECKED_BAGS,
            "personal_item_required": True,
            "carryon_preferred_not_required": True,
            "target_all_in_usd": config.ALERT_THRESHOLD_USD,
        },
        "soft_preferences": {
            "note": "SOFT. These never filter or suppress anything. Report the "
                    "cheapest option ALWAYS, even if it matches none of these. "
                    "Then separately report the closest match, and let the "
                    "traveler choose.",
            "home_airport": config.HOME_AIRPORT,
            "second_best": f"any airport within {config.HOME_RADIUS_MILES} "
                           f"miles of {config.HOME_AIRPORT}",
            "ideal_trip_days": config.IDEAL_TRIP_DAYS,
            "whole_trip_inside": "December 2026",
            "include_christmas_day": True,
            "home_before_new_years_eve": True,
            "roughly_worth_paying_extra_usd": config.IDEAL_PREMIUM_USD,
            "weighting": "airport location matters slightly more than any "
                         "other single factor",
        },
        "picks": _picks(short),
        "airlines_seen": all_air,
        "airlines_needing_policy_lookup": bagfees.airlines_needing_lookup(set(all_air)),
        "bag_estimate_warning":
            "estimated_bag_usd is a PLACEHOLDER for any airline listed in "
            "airlines_needing_policy_lookup. Verify the real published policy "
            "before trusting estimated_all_in_usd.",
        "options": [
            {
                "rank": i + 1,
                "kind": o.kind,
                "route": o.route,
                "origin": o.outbound.origin,
                "return_airport": (o.inbound.destination if o.inbound
                                   else o.outbound.origin),
                "depart_date": o.depart_date,
                "return_date": o.return_date,
                "trip_days": o.trip_days,
                "fare_usd": o.fare_usd,
                "estimated_bag_usd": o.bag_usd,
                "estimated_all_in_usd": o.all_in_usd,
                "bag_estimate_confidence": o.bag_confidence,
                "free_carryon_included": o.carryon_free,
                "baggage_notes": o.bag_notes,
                "preference_score": o.pref_score,
                "preference_label": o.pref_label,
                "preference_matched": o.pref_matched,
                "preference_missed": o.pref_missed,
                "airlines": o.airlines,
                "total_stops": o.total_stops,
                "max_layover_min": o.max_layover_min,
                "overnight_layover": o.has_overnight_layover,
                "all_december": o.all_december,
                "tags": o.tags,
                "outbound_legs": [asdict(l) for l in o.outbound.legs],
                "inbound_legs": [asdict(l) for l in o.inbound.legs] if o.inbound else [],
                "booking_url_outbound": o.outbound.url,
                "booking_url_inbound": o.inbound.url if o.inbound else "",
            }
            for i, o in enumerate(short)
        ],
    }
    path = os.path.join(config.DATA_DIR, "shortlist.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    _log(f"  wrote {path}")


def write_report(short: list[strategy.TripOption], meta: dict) -> str:
    lines = [
        "# Guatemala flight scan",
        "",
        f"{meta['options_considered']:,} trip combinations across "
        f"{meta['origins']} US airports, {meta['scanned_at']} UTC.",
        "",
        "All-in = Google Flights fare + ESTIMATED checked bag. Rows marked "
        "`guess` use a pessimistic placeholder and need verifying.",
        "",
        "| # | All-in | Match | Fare | Bag | Route | Dates | Days | Airlines | Stops | Bag est |",
        "|--:|-------:|------:|-----:|----:|-------|-------|-----:|----------|------:|---------|",
    ]
    for i, o in enumerate(short, 1):
        lines.append(
            f"| {i} | **${o.all_in_usd}** | {o.pref_score} {o.pref_label} "
            f"| ${o.fare_usd} | ${o.bag_usd} "
            f"| {o.route} | {o.depart_date} -> {o.return_date} | {o.trip_days} "
            f"| {', '.join(o.airlines)} | {o.total_stops} "
            f"| {o.bag_confidence} |"
        )
    text = "\n".join(lines) + "\n"
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.LATEST_REPORT, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


# ----------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, default=1, choices=[1, 2, 3])
    ap.add_argument("--mode", default="oneway",
                    choices=["oneway", "roundtrip", "both"])
    ap.add_argument("--max-dates", type=int, default=None,
                    help="limit date grid (smoke testing)")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--rerank", action="store_true",
                    help="re-cost cached fares using the current baggage "
                         "policies; performs no network queries")
    args = ap.parse_args()

    origins = airports.origins_for_tier(args.tier)
    dead = deadroutes.load()
    started = time.time()

    options: list[strategy.TripOption] = []

    if args.rerank:
        _log("flightwatch: re-ranking cached fares (no network queries)\n")
        options = rerank_from_cache()
    else:
        _log(f"flightwatch: tier {args.tier} ({len(origins)} origins), "
             f"mode={args.mode}, dest={config.DESTINATIONS}")
        _log(f"window {config.EARLIEST_DEPART} .. {config.LATEST_RETURN}, "
             f"{config.MIN_TRIP_DAYS}-{config.MAX_TRIP_DAYS} day trips\n")

        if args.mode in ("oneway", "both"):
            _log("[one-way legs, recombined]")
            options += scan_oneway(origins, args.max_dates, dead)
        if args.mode in ("roundtrip", "both"):
            _log("[round-trip grid]")
            options += scan_roundtrip(origins, args.max_dates, dead)

        deadroutes.save(dead)
        retired = deadroutes.summary(dead)
        if retired:
            _log(f"  retired routes ({len(retired)}): {', '.join(retired[:12])}"
                 + (" ..." if len(retired) > 12 else ""))

    if not options:
        _log("\nNo options found. If unexpected, Google may be rate-limiting "
             "this IP -- lower MAX_WORKERS or set FLIGHTWATCH_PROXY.")
        return 1

    short = strategy.shortlist(options, config.TOP_N_REPORT)
    prior_low = store.all_time_low()

    meta = {
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origins": len(origins),
        "tier": args.tier,
        "mode": args.mode,
        "options_considered": len(options),
        "prior_all_time_low_usd": prior_low,
    }

    export_shortlist(short, meta)
    report = write_report(short, meta)
    _log("\n" + report)

    if not args.no_history:
        store.append_history(short)
        _log(f"  appended {len(short)} rows to history")

    if not args.no_notify and short:
        state = store.load_alert_state()

        # 1. The cheapest option, always evaluated on price alone. A trip that
        #    matches none of the soft preferences still alerts if it is cheap.
        cheapest = min(short, key=lambda o: o.all_in_usd)
        send, reason = store.should_alert(cheapest, state, prior_low)
        if send:
            ok = notify.alert(cheapest, reason)
            # Only remember it if it ACTUALLY went out. Recording a failed
            # send would raise the bar for future alerts based on a price the
            # traveler was never told about -- silently suppressing the next
            # one that matters.
            if ok:
                store.record_alert(state, cheapest, cheapest.all_in_usd)
            _log(f"  ALERT sent={ok}: ${cheapest.all_in_usd} ({reason})"
                 + ("" if ok else "  [NOT recorded - delivery failed]"))
        else:
            _log(f"  no price alert (cheapest ${cheapest.all_in_usd}, "
                 f"threshold ${config.ALERT_THRESHOLD_USD})")

        # 2. Separately, the best preference match -- but only ever as an
        #    ADDITIONAL notification, never as a substitute for the one above.
        best_pref = max(short, key=lambda o: (o.pref_score, -o.all_in_usd))
        if best_pref.key() != cheapest.key():
            send2, reason2 = store.should_alert_preferred(best_pref, state)
            if send2:
                ok2 = notify.alert(best_pref, reason2)
                if ok2:
                    store.record_pref_alert(state, best_pref.all_in_usd)
                _log(f"  PREF ALERT sent={ok2}: ${best_pref.all_in_usd} "
                     f"(score {best_pref.pref_score})"
                     + ("" if ok2 else "  [NOT recorded - delivery failed]"))

        store.save_alert_state(state)

    _log(f"\ndone in {time.time()-started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
