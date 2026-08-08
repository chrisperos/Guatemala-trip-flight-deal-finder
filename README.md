# flightwatch — cheap flights, US → Guatemala

A daily watcher for a specific trip: **~3 weeks in Guatemala, sometime between
November 2026 and January 2027, departing from and returning to any airport in
the lower 48, with one checked bag, as cheaply as possible.**

Python does the searching. Claude does the judging. Discord does the shouting.

---

## Why it's built this way

**One-way legs, recombined.** Searching round trips means one query per
(origin × depart-date × trip-length) — thousands of queries per trip length.
Searching *one-way legs* costs ~4,200 queries total and yields **every** valid
combination for free, including asymmetric trips like `MSY→GUA / GUA→MCO` that
no round-trip search will ever show you. That flexibility is only usable
because you can return to a different airport than you left from.

**Bag cost is part of the price.** In the first full scan the cheapest *fare*
in the entire dataset — $367 on Frontier — finished **11th** on all-in cost,
because Frontier charges for the checked bag *and* the carry-on, while a $374
United fare included more. Ranking on fare alone gets this backwards, which is
why baggage policy is researched rather than guessed.

**Unverified bag costs are priced pessimistically** at $75/bag, so an airline
nobody has checked yet looks *worse* than it is. Errors push toward verifying,
never toward booking something worse than advertised.

---

## Setup

### 1. Push to GitHub

The cloud routine runs in Anthropic's cloud and cannot see your PC, so the code
has to live in a repo it can clone.

```bash
cd C:\Users\wills\thing\flightwatch
git init -b main
git add -A
git commit -m "flightwatch: initial"
```

Create an **empty** repo at <https://github.com/new> (private is fine — the
Claude GitHub app can clone private repos), then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/flightwatch.git
git push -u origin main
```

### 2. Create the Discord webhook

Server Settings → Integrations → Webhooks → **New Webhook** → pick a channel →
**Copy Webhook URL**.

Then, so alerts actually reach your phone: open that channel on mobile →
Notification Settings → **All Messages**. Discord suppresses non-mention
notifications by default, which is the usual reason people never see these.

> The webhook URL is a credential. Anyone holding it can post to your channel.
> Never commit it — `.gitignore` already blocks `.env` and `secrets.py`.
> If it leaks, delete the webhook in Discord and make a new one.

Optional: to get an `@mention` on high-priority alerts, enable Developer Mode
in Discord (Settings → Advanced), right-click your name → Copy User ID.

### 3. Create the routine

Ask Claude Code:

> Create a daily cloud routine that runs ANALYZE.md from
> `github.com/YOUR_USERNAME/flightwatch`, with DISCORD_WEBHOOK set to `<url>`.

Or manage it yourself at <https://claude.ai/code/routines>.

---

## Running it by hand

```bash
pip install -r requirements.txt
python main.py --tier 1
```

| Command | What it does |
|---|---|
| `python main.py --tier 1` | Daily scan, 30 gateway airports, ~7 min |
| `python main.py --tier 2` | 90 airports — slower, wider net |
| `python main.py --tier 3` | 150 airports, the full long tail |
| `python main.py --rerank` | Re-cost cached fares after fixing a bag policy. No network. Instant. |
| `python main.py --max-dates 5` | Smoke test |
| `python main.py --mode both` | Adds a direct round-trip sweep alongside the recombined one-ways |

### Reading the scan health line

```
ok=1936 empty=224 blocked=0
```

- **empty** — that route genuinely has no Guatemala service on that date. Normal.
  Pairs that stay empty get retired into `data/dead_routes.json` and skipped.
- **blocked** — Google refused us. Lower `REQUESTS_PER_SECOND` in `config.py`.
- **ERRORS** — a bug in *this* code, not rate limiting. These are reported
  separately on purpose: they were once lumped in with `blocked`, and a
  `TypeError` in date parsing quietly destroyed **40% of all results** while
  looking exactly like throttling. Never conflate the two again.

---

## Files

| | |
|---|---|
| `config.py` | Every knob. Dates, trip length, budget, thresholds. Edit this one. |
| `airports.py` | US origins in 3 tiers by how plausibly they produce a cheap fare |
| `search.py` | Google Flights wrapper: retries, rate limiting, itinerary parsing |
| `strategy.py` | Date grids, one-way recombination, shortlist selection |
| `bagfees.py` | Baggage cost lookup over the Claude-maintained policy cache |
| `deadroutes.py` | Learns which city pairs never return anything, stops asking |
| `store.py` | Price history, alert de-duplication, raw-quote cache |
| `notify.py` | Discord webhook (ntfy.sh fallback) |
| `ANALYZE.md` | The daily instructions Claude follows |
| `data/history.csv` | Append-only price record — what makes "all-time low" mean anything |
| `data/baggage_policies.json` | Verified bag policies, each with a source URL and date |

## The shortlist isn't just the 30 cheapest

Handing Claude the 30 cheapest rows is nearly useless — they tend to be 30
near-identical itineraries differing by $4. `strategy.shortlist()` deliberately
mixes in the cheapest *all-December* option, the cheapest that keeps a full 21
days, the cheapest nonstop, and the cheapest on each distinct airline. Those
are the rows where a real tradeoff exists.

## Limits worth knowing

- Prices come from Google Flights and are **estimates**. Confirm on the
  airline's own site before paying.
- Bag fees are route-, date-, and fare-class-specific. The cache is a good
  approximation, not a quote.
- Guatemala may ask for proof of onward travel. Two one-ways can attract more
  scrutiny at check-in than a round trip.
- `fast-flights` scrapes Google Flights. If Google changes their page shape it
  can break; watch the ERRORS counter.
- Datacenter IPs get refused more than home connections. If the cloud routine
  reports high `blocked`, that is the cause.
