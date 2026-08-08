"""Soft preferences: what the traveler would pay a little extra for.

Kept rigorously separate from cost. `all_in_usd` decides alerts and never
changes; this module only *describes* how well a trip matches taste. Nothing
here can filter, suppress, or reorder an option out of view -- a trip that
matches zero preferences but is cheap still gets reported, loudly.

The stated tradeoff, in the traveler's own example: a $250 trip matching none
of these versus a $300 trip matching all of them, and they take the $300.
So the full set is worth roughly $50-75, not $200. IDEAL_PREMIUM_USD encodes
that so Claude has a number to reason against instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import asin, cos, radians, sin, sqrt

import config

# --------------------------------------------------------------- airport geo
# Only what's needed to measure distance from Denver. Airports absent from this
# table score zero for location rather than erroring -- unknown never becomes a
# silent bonus.
_COORDS: dict[str, tuple[float, float]] = {
    "DEN": (39.86, -104.67), "COS": (38.81, -104.70), "SLC": (40.79, -111.98),
    "ABQ": (35.04, -106.61), "LAS": (36.08, -115.15), "PHX": (33.43, -112.01),
    "TUS": (32.12, -110.94), "ELP": (31.81, -106.38), "OMA": (41.30, -95.89),
    "MCI": (39.30, -94.71), "ICT": (37.65, -97.43), "TUL": (36.20, -95.89),
    "OKC": (35.39, -97.60), "DSM": (41.53, -93.66), "AMA": (35.22, -101.71),
    "LBB": (33.66, -101.82), "MAF": (31.94, -102.20), "RAP": (44.05, -103.06),
    "BIL": (45.81, -108.54), "BOI": (43.56, -116.22), "GEG": (47.62, -117.53),
    "MSO": (46.92, -114.09), "GJT": (39.12, -108.53), "FSD": (43.58, -96.74),
    "MSP": (44.88, -93.22), "STL": (38.75, -90.37), "ORD": (41.98, -87.90),
    "MDW": (41.79, -87.75), "MKE": (42.95, -87.90), "IND": (39.72, -86.29),
    "CVG": (39.05, -84.67), "BNA": (36.13, -86.68), "MEM": (35.04, -89.98),
    "LIT": (34.73, -92.22), "SHV": (32.45, -93.83), "XNA": (36.28, -94.31),
    "SGF": (37.24, -93.39), "DFW": (32.90, -97.04), "DAL": (32.85, -96.85),
    "AUS": (30.19, -97.67), "SAT": (29.53, -98.47), "IAH": (29.98, -95.34),
    "HOU": (29.65, -95.28), "MSY": (29.99, -90.26), "LAX": (33.94, -118.41),
    "ONT": (34.06, -117.60), "SNA": (33.68, -117.87), "BUR": (34.20, -118.36),
    "LGB": (33.82, -118.15), "SAN": (32.73, -117.19), "SFO": (37.62, -122.38),
    "OAK": (37.72, -122.22), "SJC": (37.36, -121.93), "SMF": (38.70, -121.59),
    "FAT": (36.78, -119.72), "RNO": (39.50, -119.77), "PDX": (45.59, -122.60),
    "SEA": (47.45, -122.31), "EUG": (44.12, -123.21), "MFR": (42.37, -122.87),
    "JFK": (40.64, -73.78), "LGA": (40.78, -73.87), "EWR": (40.69, -74.17),
    "BOS": (42.36, -71.01), "PHL": (39.87, -75.24), "BWI": (39.18, -76.67),
    "DCA": (38.85, -77.04), "IAD": (38.95, -77.46), "ATL": (33.64, -84.43),
    "MIA": (25.79, -80.29), "FLL": (26.07, -80.15), "MCO": (28.43, -81.31),
    "TPA": (27.98, -82.53), "RSW": (26.54, -81.75), "PBI": (26.68, -80.10),
    "JAX": (30.49, -81.69), "SRQ": (27.40, -82.55), "CLT": (35.21, -80.94),
    "RDU": (35.88, -78.79), "RIC": (37.51, -77.32), "ORF": (36.89, -76.20),
    "GSO": (36.10, -79.94), "CHS": (32.90, -80.04), "SAV": (32.13, -81.20),
    "BHM": (33.56, -86.75), "HSV": (34.64, -86.78), "DTW": (42.21, -83.35),
    "CLE": (41.41, -81.85), "CMH": (40.00, -82.89), "PIT": (40.49, -80.23),
    "GRR": (42.88, -85.52), "DAY": (39.90, -84.22), "SDF": (38.17, -85.74),
    "LEX": (38.04, -84.61), "GSP": (34.90, -82.22), "TYS": (35.81, -83.99),
    "MYR": (33.68, -78.93), "PNS": (30.47, -87.19), "VPS": (30.48, -86.53),
    "PVD": (41.72, -71.43), "BDL": (41.94, -72.68), "ALB": (42.75, -73.80),
    "BUF": (42.94, -78.73), "ROC": (43.12, -77.67), "SYR": (43.11, -76.10),
    "MHT": (42.93, -71.44), "PWM": (43.65, -70.31), "BTV": (44.47, -73.15),
}


def miles_from_home(code: str) -> float | None:
    """Great-circle miles from the preferred home airport, or None if unknown."""
    home = _COORDS.get(config.HOME_AIRPORT)
    there = _COORDS.get(code)
    if not home or not there:
        return None
    lat1, lon1 = radians(home[0]), radians(home[1])
    lat2, lon2 = radians(there[0]), radians(there[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * asin(sqrt(a)) * 3958.8


def _endpoint_score(code: str) -> tuple[float, str]:
    """1.0 for home airport, 0.55 within the radius, 0 otherwise."""
    if code == config.HOME_AIRPORT:
        return 1.0, "home"
    d = miles_from_home(code)
    if d is None:
        return 0.0, "unknown"
    if d <= config.HOME_RADIUS_MILES:
        return 0.55, f"{int(d)}mi"
    return 0.0, f"{int(d)}mi"


# ------------------------------------------------------------------- scoring

@dataclass
class PreferenceMatch:
    score: int                       # 0-100, higher = closer to ideal
    location_note: str
    matched: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.score >= 85:
            return "ideal"
        if self.score >= 60:
            return "close"
        if self.score >= 35:
            return "partial"
        return "cheapest-only"


def evaluate(origin: str, return_airport: str,
             depart: date, back: date, trip_days: int) -> PreferenceMatch:
    matched: list[str] = []
    missed: list[str] = []

    # Location carries the most weight -- the traveler said the airport
    # matters slightly more than any other single factor.
    out_s, out_n = _endpoint_score(origin)
    back_s, back_n = _endpoint_score(return_airport)
    loc = 35.0 * (out_s + back_s) / 2
    if out_s == 1.0 and back_s == 1.0:
        matched.append(f"both ends at {config.HOME_AIRPORT}")
    elif out_s == 1.0 or back_s == 1.0:
        matched.append(f"one end at {config.HOME_AIRPORT}")
        missed.append(f"other end {out_n if out_s < 1 else back_n} away")
    elif out_s or back_s:
        matched.append(f"within {config.HOME_RADIUS_MILES}mi of "
                       f"{config.HOME_AIRPORT} ({out_n}/{back_n})")
    else:
        missed.append(f"far from {config.HOME_AIRPORT} ({out_n}/{back_n})")

    y, m = config.IDEAL_MONTH
    dec = (depart.year, depart.month) == (y, m) and (back.year, back.month) == (y, m)
    if dec:
        matched.append("entirely in December")
    else:
        missed.append("not entirely in December")

    xmas = date(y, 12, 25)
    has_xmas = depart <= xmas <= back
    if has_xmas:
        matched.append("includes Christmas Day")
    else:
        missed.append("misses Christmas Day")

    nye = date(y, 12, 31)
    before_nye = back < nye
    if before_nye:
        matched.append("home before New Year's Eve")
    else:
        missed.append("home on/after New Year's Eve")

    # Trip length: full credit at 21 days, tapering to zero 7 days either side.
    off = abs(trip_days - config.IDEAL_TRIP_DAYS)
    len_score = 15.0 * max(0.0, 1 - off / 7)
    if off == 0:
        matched.append("exactly 21 days")
    elif off <= 2:
        matched.append(f"{trip_days} days (near 21)")
    else:
        missed.append(f"{trip_days} days, not 21")

    score = loc + (20 if dec else 0) + (15 if has_xmas else 0) \
        + (15 if before_nye else 0) + len_score

    return PreferenceMatch(
        score=int(round(score)),
        location_note=f"{origin}({out_n}) / {return_airport}({back_n})",
        matched=matched, missed=missed,
    )
