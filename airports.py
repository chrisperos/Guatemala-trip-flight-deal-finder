"""US origin airports, tiered by how plausibly they produce a cheap GUA fare.

Tier 1  - gateways with nonstop GUA service, plus the budget-carrier hubs that
          actually move the price floor. ~95% of real deals land here.
Tier 2  - large/medium airports where a legacy through-fare (AA/UA/DL one-stop)
          can occasionally undercut the gateways.
Tier 3  - the long tail. Rarely wins, but you said "any airport," so it exists.

Scanning Alaska and Hawaii is intentionally omitted per your constraint.
"""

from __future__ import annotations

TIER1 = [
    # Nonstop or near-nonstop GUA gateways
    "MIA", "FLL", "IAH", "LAX", "JFK", "EWR", "ORD", "ATL", "DFW", "MCO",
    "IAD", "MSY", "TPA",
    # Budget-carrier hubs (Spirit / Frontier / Volaris / Arajet feed)
    "LAS", "MDW", "DEN", "PHX", "SAT", "AUS", "HOU", "DAL", "BWI", "PHL",
    "SFO", "SAN", "SJC", "OAK", "ONT", "RSW", "PBI",
]

TIER2 = [
    "BOS", "LGA", "DCA", "SEA", "PDX", "SLC", "MSP", "DTW", "CLT", "STL",
    "MCI", "CLE", "CMH", "IND", "MKE", "PIT", "SMF", "SNA", "BUR", "LGB",
    "TUS", "ELP", "ABQ", "OKC", "TUL", "MEM", "JAX", "SRQ", "RDU", "BNA",
    "RIC", "ORF", "GSO", "CHS", "SAV", "BHM", "HSV", "LIT", "OMA", "DSM",
    "BOI", "RNO", "FAT", "PVD", "BDL", "ALB", "BUF", "ROC", "SYR", "MHT",
    "GRR", "DAY", "CVG", "SDF", "LEX", "GSP", "TYS", "MYR", "PNS", "VPS",
]

TIER3 = [
    "PWM", "BTV", "PIA", "SPI", "CID", "MSN", "GRB", "FSD", "RAP", "BIL",
    "MSO", "GEG", "EUG", "MFR", "COS", "GJT", "ICT", "SGF", "XNA", "SHV",
    "BTR", "LFT", "JAN", "MOB", "TLH", "GNV", "DAB", "MLB", "PIE", "PGD",
    "SFB", "TRI", "CHA", "AVL", "CAE", "FAY", "ILM", "MGM", "DHN", "CRW",
    "ROA", "CHO", "SBY", "AVP", "ABE", "ERI", "TOL", "FWA", "SBN", "EVV",
    "TVC", "MBS", "LAN", "AZO", "CWA", "DLH", "LSE", "MLI", "BMI", "DEC",
    "AMA", "LBB", "MAF", "CRP", "HRL", "BRO", "GRK", "TYR", "ACT", "SJT",
]

TIERS = {1: TIER1, 2: TIER1 + TIER2, 3: TIER1 + TIER2 + TIER3}


def origins_for_tier(tier: int) -> list[str]:
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {sorted(TIERS)}, got {tier!r}")
    # dict.fromkeys preserves order while removing any accidental duplicate
    return list(dict.fromkeys(TIERS[tier]))
