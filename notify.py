"""Discord webhook alerts, with ntfy.sh as an optional fallback.

Two tiers on purpose. A "new all-time low" is informational -- the floor
moved, worth knowing, not worth interrupting dinner. Breaking the price target
is actionable and gets an @mention, because cheap fares routinely disappear
within hours and a notification you read tomorrow is worthless.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import config

GREEN = 0x2ECC71    # under target -- go book it
BLUE = 0x3498DB     # new low -- informational


def _webhook() -> str:
    """Private channel wins whenever one is configured."""
    return config.DISCORD_WEBHOOK_PRIVATE or config.DISCORD_WEBHOOK


def _post_json(url: str, payload: dict) -> bool:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "flightwatch/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        print(f"  discord HTTP {e.code}: {e.read()[:200]!r}")
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  discord error: {e}")
        return False


def _fmt_layover(minutes: int) -> str:
    if not minutes:
        return "-"
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _describe(opt) -> str:
    airlines = ", ".join(opt.airlines) or "unknown carrier"
    bag_flag = {
        "verified": "",
        "stale": "  (bag cost needs re-check)",
        "guess": "  **bag cost is a GUESS**",
    }[opt.bag_confidence]

    lines = [
        f"**${opt.all_in_usd} all-in**  =  ${opt.fare_usd} fare  "
        f"+  ~${opt.bag_usd} checked bag{bag_flag}",
        f"`{opt.route}`",
        f"{opt.depart_date}  →  {opt.return_date}   ({opt.trip_days} days)",
        f"{airlines} · {opt.total_stops} stop(s) · "
        f"longest layover {_fmt_layover(opt.max_layover_min)}",
    ]
    # No preference scores, no matched/missed lists, no "under your target"
    # phrasing. This channel has other readers, and the traveler's planning
    # criteria are not theirs to see. Only neutral, factual flight attributes
    # go out. See DISCLOSURE in config.py.
    notes = []
    if opt.carryon_free:
        notes.append("free carry-on included")
    else:
        notes.append("personal item only unless you pay for a carry-on")
    if opt.has_overnight_layover:
        notes.append("overnight/8h+ layover")
    if "open-jaw" in opt.tags:
        notes.append("returns to a different airport")
    if notes:
        lines.append("· " + "\n· ".join(notes))
    return "\n".join(lines)


def send_discord(opt, reason: str, urgent: bool) -> bool:
    hook = _webhook()
    if not hook:
        return False

    # Neutral titles. "under your $400 target" announces a budget to a room
    # that does not need to know it.
    title = (f"${opt.all_in_usd} all-in to Guatemala — price drop"
             if urgent else f"${opt.all_in_usd} all-in to Guatemala")

    content = ""
    if urgent and config.DISCORD_MENTION_USER_ID:
        content = f"<@{config.DISCORD_MENTION_USER_ID}>"

    payload = {
        "content": content,
        "username": "flightwatch",
        "embeds": [{
            "title": title[:250],
            "description": _describe(opt)[:4000],
            "color": GREEN if urgent else BLUE,
            "url": opt.outbound.url or None,
            "fields": [
                {"name": "Book outbound",
                 "value": f"[Google Flights]({opt.outbound.url})",
                 "inline": True},
            ] + ([{"name": "Book return",
                   "value": f"[Google Flights]({opt.inbound.url})",
                   "inline": True}] if opt.inbound and opt.inbound.url else []),
            "footer": {"text": "Prices are estimates. Verify the bag fee and "
                               "weight limit on the airline's own site before booking."},
        }],
    }
    return _post_json(hook, payload)


def send_ntfy(opt, reason: str, urgent: bool) -> bool:
    if not config.NTFY_TOPIC:
        return False
    url = f"{config.NTFY_SERVER.rstrip('/')}/{config.NTFY_TOPIC}"
    body = _describe(opt).replace("**", "").replace("`", "")
    headers = {
        "Title": f"${opt.all_in_usd} to Guatemala - {reason}"
                 .encode("ascii", "replace").decode(),
        "Priority": "high" if urgent else "default",
        "Tags": "airplane",
        "Content-Type": "text/plain; charset=utf-8",
    }
    if opt.outbound.url:
        headers["Click"] = opt.outbound.url
    req = urllib.request.Request(url, data=body.encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


def alert(opt, reason: str) -> bool:
    urgent = opt.all_in_usd <= config.ALERT_THRESHOLD_USD
    a = send_discord(opt, reason, urgent)
    b = send_ntfy(opt, reason, urgent)
    return a or b


def send_plain(text: str) -> bool:
    """For scan-health warnings that aren't about a specific fare."""
    hook = _webhook()
    if not hook:
        return False
    return _post_json(config.DISCORD_WEBHOOK,
                      {"username": "flightwatch", "content": text[:1900]})
