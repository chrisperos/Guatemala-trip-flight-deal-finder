"""All the knobs. Edit this file, not the others."""

from __future__ import annotations

import os
from datetime import date

# ---------------------------------------------------------------- destination
# Guatemala's only airport with meaningful US service is GUA (La Aurora,
# Guatemala City). FRS (Flores/Tikal) is domestic + a thin Belize route, so it
# is off by default -- turn it on only if you want to see the rare oddity.
DESTINATIONS = ["GUA"]

# ---------------------------------------------------------------- date window
# Departure may fall anywhere in [EARLIEST_DEPART, LATEST_DEPART]; the return
# must land on or before LATEST_RETURN.
EARLIEST_DEPART = date(2026, 11, 10)
LATEST_DEPART = date(2027, 1, 15)
LATEST_RETURN = date(2027, 2, 8)

# 21+ days is the goal, but a big enough saving justifies 19. Options below 21
# are tagged "short-NNd" so the tradeoff is always visible rather than silently
# swapped in -- a 19-day trip should only ever win by being dramatically
# cheaper, and you get to decide what "dramatically" means.
MIN_TRIP_DAYS = 19
MAX_TRIP_DAYS = 28
PREFERRED_MIN_TRIP_DAYS = 21

# Round-trip TICKETS are searched separately from recombined one-ways, because
# neither reliably wins: measured on the same routes and dates, a round-trip
# ticket came in $222 cheaper than two one-ways from LAX and $196 cheaper from
# MIA, while two one-ways beat the round trip by $166 from MSY. Searching only
# one of the two modes is how you miss the actual cheapest trip.
#
# The round-trip grid is too large to sweep at every length, so it runs in two
# stages: COARSE sweeps every origin at these lengths, then the best candidates
# are refined across REFINE lengths. Coarse defaults to the preferred 21 days
# rather than the 19-day minimum, so the broad sweep optimises for the trip you
# actually want.
COARSE_TRIP_LENGTHS = [21]
REFINE_TRIP_LENGTHS = [19, 20, 22, 23, 24, 25, 26, 28]

# Step in days across the departure window. 1 = every single day (recommended;
# it is cheap). Raise to 2 or 3 only if you are being rate limited.
DEPART_STEP_DAYS = 1

# --------------------------------------------------------------------- budget
# Two independent triggers, because they answer different questions.
#
# ALERT_THRESHOLD_USD is "this is objectively a good deal, go look now" and
# fires at high priority. As of the first full scan the real floor for this
# route with a checked bag was ~$464 all-in, so $400 is a genuine bargain
# rather than routine noise.
ALERT_THRESHOLD_USD = 400

# ALERT_ON_NEW_LOW is "the floor moved" -- lower priority, and how you learn
# what this route actually costs instead of guessing. Expect several in the
# first week as history fills in, then near silence.
ALERT_ON_NEW_LOW = True

# Never re-alert unless it beats the best already-alerted price by at least
# this much. Stops the "$347 -> $346" notification spiral.
MIN_IMPROVEMENT_USD = 20

# ------------------------------------------------------------------- baggage
# You need 1 checked bag (0-50 lb). Set to 0 if you decide to go carry-on only.
CHECKED_BAGS = 1

# ------------------------------------------------------------------ execution
MAX_WORKERS = 8
REQUEST_RETRIES = 3
RETRY_BACKOFF_SEC = 3.0  # doubles each retry

# Measured ~19 q/s clean at 8 workers with zero refusals. This cap is polite
# headroom, not a workaround: watch the "blocked" counter in the scan output
# and only lower it if that number is actually climbing.
REQUESTS_PER_SECOND = 10.0
PROXY = os.environ.get("FLIGHTWATCH_PROXY") or None

# ------------------------------------------------------------------- reporting
TOP_N_REPORT = 25        # rows in the console/markdown report
TOP_N_REFINE = 30        # round-trip candidates promoted to the refine stage

# A trip is "all-December" if both the departure and the return fall in
# December 2026. These get flagged in the report since it is your stated ideal.
IDEAL_MONTH = (2026, 12)

# ---------------------------------------------------------------- notification
# Discord webhook. Server Settings -> Integrations -> Webhooks -> New Webhook,
# then copy the URL. NEVER commit it: the URL alone lets anyone post to your
# channel. Set it as an environment variable / repo secret instead.
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")

# For phone push to actually reach you, open the channel on mobile and set
# Notification Settings to "All Messages" -- Discord's default suppresses
# non-mention notifications, which is the usual reason alerts go unnoticed.
# Set this to your Discord user id to get an @mention on high-priority hits.
DISCORD_MENTION_USER_ID = os.environ.get("DISCORD_MENTION_USER_ID", "")

# Optional fallback: ntfy.sh (free, no account). Leave blank if unused.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")

# --------------------------------------------------------------------- storage
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")
ALERT_STATE = os.path.join(DATA_DIR, "alert_state.json")
LATEST_REPORT = os.path.join(DATA_DIR, "latest_report.md")
