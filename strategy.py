"""Grid construction, one-way recombination, and shortlist selection.

The core trick: searching outbound and return legs SEPARATELY and recombining
them turns a combinatorial problem into a linear one. Scanning 72 origins over
67 departure dates as round trips would be ~4,800 queries per trip length. As
one-way legs it is ~4,800 queries TOTAL, and from those you can construct every
valid (origin, return-airport, depart-date, trip-length) combination for free --
including asymmetric trips like "fly out of Miami, fly home into LAX", which
your any-airport flexibility makes legal and which no round-trip search will
ever show you.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import config
from search import Quote


# ------------------------------------------------------------------ date grids

def depart_dates() -> list[date]:
    out, cur = [], config.EARLIEST_DEPART
    while cur <= config.LATEST_DEPART:
        out.append(cur)
        cur += timedelta(days=config.DEPART_STEP_DAYS)
    return out


def return_dates() -> list[date]:
    """Every date that could serve as a return, given the departure window."""
    lo = config.EARLIEST_DEPART + timedelta(days=config.MIN_TRIP_DAYS)
    hi = min(config.LATEST_DEPART + timedelta(days=config.MAX_TRIP_DAYS),
             config.LATEST_RETURN)
    out, cur = [], lo
    while cur <= hi:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def is_all_ideal_month(d1: date, d2: date) -> bool:
    y, m = config.IDEAL_MONTH
    return (d1.year, d1.month) == (y, m) and (d2.year, d2.month) == (y, m)


# ------------------------------------------------------------------ trip option

@dataclass
class TripOption:
    outbound: Quote
    inbound: Quote | None          # None => `outbound` is itself a round trip
    fare_usd: int
    bag_usd: int
    all_in_usd: int
    trip_days: int
    all_december: bool
    total_stops: int
    max_layover_min: int
    has_overnight_layover: bool
    bag_confidence: str            # "verified" | "stale" | "guess"
    carryon_free: bool = False     # full overhead carry-on included on ALL legs
    bag_notes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # Soft preference match. Purely descriptive -- never affects all_in_usd,
    # never filters anything out, never suppresses an alert.
    pref_score: int = 0
    pref_label: str = ""
    pref_matched: list[str] = field(default_factory=list)
    pref_missed: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "roundtrip" if self.inbound is None else "two-one-ways"

    @property
    def airlines(self) -> list[str]:
        a = list(self.outbound.airlines)
        if self.inbound:
            a += list(self.inbound.airlines)
        return list(dict.fromkeys(a))

    @property
    def depart_date(self) -> str:
        return self.outbound.depart_date

    @property
    def return_date(self) -> str:
        if self.inbound:
            return self.inbound.depart_date
        return self.outbound.return_date or ""

    @property
    def route(self) -> str:
        if self.inbound:
            return (f"{self.outbound.origin}->{self.outbound.destination}"
                    f" / {self.inbound.origin}->{self.inbound.destination}")
        return f"{self.outbound.origin}<->{self.outbound.destination}"

    def key(self) -> tuple:
        return (self.kind, self.route, self.depart_date, self.return_date,
                tuple(self.airlines))


def _bag_confidence(policies_used) -> str:
    if not policies_used:
        return "guess"
    if any(p.is_guess for p in policies_used):
        return "guess"
    if any(p.is_stale for p in policies_used):
        return "stale"
    return "verified"


def _make_option(out_q: Quote, in_q: Quote | None, policies) -> TripOption | None:
    import bagfees

    if in_q is None:
        if not out_q.return_date:
            return None
        d1 = date.fromisoformat(out_q.depart_date)
        d2 = date.fromisoformat(out_q.return_date)
        fare = out_q.price_usd
        bag, used = bagfees.bag_cost_for(out_q.airlines, 2, policies)
        stops = out_q.stops
        max_lay = max(out_q.layovers_min, default=0)
        overnight = out_q.overnight_layover
    else:
        d1 = date.fromisoformat(out_q.depart_date)
        d2 = date.fromisoformat(in_q.depart_date)
        fare = out_q.price_usd + in_q.price_usd
        bag_o, used_o = bagfees.bag_cost_for(out_q.airlines, 1, policies)
        bag_i, used_i = bagfees.bag_cost_for(in_q.airlines, 1, policies)
        bag, used = bag_o + bag_i, used_o + used_i
        stops = out_q.stops + in_q.stops
        max_lay = max(out_q.layovers_min + in_q.layovers_min, default=0)
        overnight = out_q.overnight_layover or in_q.overnight_layover

    days = (d2 - d1).days
    if not (config.MIN_TRIP_DAYS <= days <= config.MAX_TRIP_DAYS):
        return None

    opt = TripOption(
        outbound=out_q, inbound=in_q,
        fare_usd=fare, bag_usd=bag, all_in_usd=fare + bag,
        trip_days=days,
        all_december=is_all_ideal_month(d1, d2),
        total_stops=stops, max_layover_min=max_lay,
        has_overnight_layover=overnight,
        bag_confidence=_bag_confidence(used),
        # only true if EVERY carrier on the trip includes one -- a free
        # carry-on outbound is worth little if you must gate-check it home
        carryon_free=bool(used) and all(p.carryon_free for p in used),
        bag_notes=[f"{p.airline}: {p.notes}" for p in
                   {p.airline: p for p in used}.values() if p.notes],
    )

    import preferences
    ret_ap = in_q.destination if in_q is not None else out_q.origin
    pm = preferences.evaluate(out_q.origin, ret_ap, d1, d2, days)
    opt.pref_score = pm.score
    opt.pref_label = pm.label
    opt.pref_matched = pm.matched
    opt.pref_missed = pm.missed
    if pm.score >= 60:
        opt.tags.append(f"pref-{pm.label}")

    if opt.carryon_free:
        opt.tags.append("free-carryon")
    if opt.all_december:
        opt.tags.append("all-december")
    lo, hi = config.DESIRABLE_TRIP_DAYS
    if lo <= days <= hi:
        opt.tags.append(f"{days}d-in-band")
    else:
        opt.tags.append(f"short-{days}d")
    if stops == 0:
        opt.tags.append("nonstop")
    elif stops <= 1:
        opt.tags.append("1-stop")
    if overnight:
        opt.tags.append("overnight-layover")
    if in_q is not None and out_q.origin != in_q.destination:
        opt.tags.append("open-jaw")
    return opt


# ------------------------------------------------------------- recombination

def _leg_all_in(q: Quote, policies) -> int:
    """Fare plus this leg's own bag cost."""
    import bagfees
    bag, _ = bagfees.bag_cost_for(q.airlines, 1, policies)
    return q.price_usd + bag


def _index_by_date(quotes: list[Quote], keep_per_date: int,
                   policies) -> dict[str, list[Quote]]:
    """Cheapest N quotes per calendar date, ranked by ALL-IN cost.

    Ranking on bare fare here was a genuine blind spot. Bag fees span ~$30
    (Copa) to $75 (unverified), so a fare up to $45 more expensive can win on
    all-in cost -- and if the fares on a given date cluster within that spread,
    truncating by fare drops the actual winner before it is ever compared.
    """
    buckets: dict[str, list[Quote]] = {}
    for q in quotes:
        buckets.setdefault(q.depart_date, []).append(q)
    for d in buckets:
        # cheapest per airport first, so one dominant airport can't hog every
        # slot, then the cheapest N of those
        best_per_airport: dict[str, Quote] = {}
        for q in buckets[d]:
            ap = q.origin if q.destination in _dests() else q.destination
            cur = best_per_airport.get(ap)
            if cur is None or _leg_all_in(q, policies) < _leg_all_in(cur, policies):
                best_per_airport[ap] = q
        ranked = sorted(best_per_airport.values(),
                        key=lambda q: _leg_all_in(q, policies))
        buckets[d] = ranked[:keep_per_date]
    return buckets


def _dests() -> set[str]:
    return set(config.DESTINATIONS)


def combine_oneways(outbound: list[Quote], inbound: list[Quote],
                    keep_per_date: int = 8) -> list[TripOption]:
    import bagfees
    policies = bagfees.load_policies()

    out_idx = _index_by_date(outbound, keep_per_date, policies)
    in_idx = _index_by_date(inbound, keep_per_date, policies)

    options: list[TripOption] = []
    for dep_str, outs in out_idx.items():
        dep = date.fromisoformat(dep_str)
        for days in range(config.MIN_TRIP_DAYS, config.MAX_TRIP_DAYS + 1):
            ret = dep + timedelta(days=days)
            if ret > config.LATEST_RETURN:
                continue
            ins = in_idx.get(ret.isoformat())
            if not ins:
                continue
            for o in outs:
                for i in ins:
                    opt = _make_option(o, i, policies)
                    if opt:
                        options.append(opt)
    return options


def wrap_roundtrips(quotes: list[Quote]) -> list[TripOption]:
    import bagfees
    policies = bagfees.load_policies()
    out = []
    for q in quotes:
        opt = _make_option(q, None, policies)
        if opt:
            out.append(opt)
    return out


# -------------------------------------------------------------- shortlisting

def shortlist(options: list[TripOption], n: int = 30) -> list[TripOption]:
    """Pick a set that is cheap AND varied.

    Handing Claude the 30 cheapest rows is nearly useless -- they tend to be 30
    near-identical Spirit itineraries differing by $4. What earns its place in
    the reasoning step is contrast: the cheapest overall, the cheapest that
    keeps you entirely in December, the cheapest nonstop, the cheapest on each
    distinct airline, the cheapest that still hits a full 21 days. Those are
    the rows where a genuine tradeoff exists.
    """
    if not options:
        return []

    ranked = sorted(options, key=lambda o: (o.all_in_usd, o.total_stops))
    chosen: dict[tuple, TripOption] = {}

    def take(pool: list[TripOption], count: int) -> None:
        for o in pool[:count]:
            chosen.setdefault(o.key(), o)

    # 1. the straight-up cheapest
    take(ranked, max(n // 3, 6))

    # 2. cheapest that satisfies the stated ideal (entirely in December)
    take([o for o in ranked if o.all_december], 5)

    # 3. cheapest that keeps the full 21+ days
    take([o for o in ranked if config.DESIRABLE_TRIP_DAYS[0] <= o.trip_days
                                       <= config.DESIRABLE_TRIP_DAYS[1]], 5)

    # 4. cheapest with minimal connections
    take([o for o in ranked if o.total_stops <= 1], 4)

    # 5. cheapest per distinct airline -- this is what surfaces the
    #    "free checked bag" carriers that lose on fare but win all-in
    seen_air: set[str] = set()
    for o in ranked:
        sig = "+".join(sorted(o.airlines))
        if sig in seen_air:
            continue
        seen_air.add(sig)
        chosen.setdefault(o.key(), o)
        if len(seen_air) >= 10:
            break

    # 6. cheapest with no overnight layover, in case the bargains are all grim
    take([o for o in ranked if not o.has_overnight_layover], 3)

    # 7. the preference dimension. These are ADDITIVE -- they never displace
    #    the cheap rows above, they sit alongside them, so the report can show
    #    "cheapest" and "closest to what you actually want" side by side and
    #    let the traveler choose rather than choosing for them.
    by_pref = sorted(options, key=lambda o: (-o.pref_score, o.all_in_usd))
    take(by_pref, 4)

    #    best match that is also genuinely cheap -- the sweet spot, and the row
    #    most likely to be the actual answer
    cheapest = ranked[0].all_in_usd
    near_cheap = [o for o in options
                  if o.all_in_usd <= cheapest + config.IDEAL_PREMIUM_USD]
    take(sorted(near_cheap, key=lambda o: (-o.pref_score, o.all_in_usd)), 4)

    #    best match that clears the price target outright
    under = [o for o in options if o.all_in_usd <= config.ALERT_THRESHOLD_USD]
    take(sorted(under, key=lambda o: (-o.pref_score, o.all_in_usd)), 3)

    #    anything touching the home airport, at any price, so its true cost is
    #    always visible rather than inferred from its absence
    home = [o for o in options
            if config.HOME_AIRPORT in (o.outbound.origin,
                                       o.inbound.destination if o.inbound
                                       else o.outbound.origin)]
    take(sorted(home, key=lambda o: o.all_in_usd), 3)

    # The final cut sorts by price, so a pricey-but-ideal option (a Denver
    # round trip, say) would be truncated away -- precisely the row worth
    # seeing. Pin the best of each dimension so it always survives.
    guaranteed: dict[tuple, TripOption] = {}
    for pool in (ranked, by_pref,
                 sorted(near_cheap, key=lambda o: (-o.pref_score, o.all_in_usd)),
                 sorted(home, key=lambda o: o.all_in_usd)):
        if pool:
            guaranteed[pool[0].key()] = pool[0]

    out = sorted(chosen.values(), key=lambda o: o.all_in_usd)[:n]
    seen = {o.key() for o in out}
    out += [o for k, o in guaranteed.items() if k not in seen]
    return sorted(out, key=lambda o: o.all_in_usd)
