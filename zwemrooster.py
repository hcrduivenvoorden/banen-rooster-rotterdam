#!/usr/bin/env python3
"""
Genereert banenzwemmen.ics uit het rooster van Sportcentrum Feijenoord.
"""

import hashlib
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ---------------- CONFIG ----------------
URL = "https://www.sportbedrijfrotterdam.nl/locatie/sportcentrum-feijenoord"
OUTFILE = "banenzwemmen.ics"
CALENDAR_NAME = "Banenzwemmen - Sportcentrum Feijenoord"
LOCATION = "Sportcentrum Feijenoord, Laan op Zuid 1055, 3072 DB Rotterdam"

# Feijenoord noemt het bad niet in de roosterregel; het wedstrijdbad is 25m.
# Blokken waarvan het label een van deze woorden bevat, slaan we over:
UITSLUITEN = ["dames", "55+"]
TZ = ZoneInfo("Europe/Amsterdam")
# ----------------------------------------

MAANDEN = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10,
    "november": 11, "december": 12,
}
DAGEN = ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag")

DATE_RE = re.compile(
    r"^(?:%s)\s+(\d{1,2})\s+(%s)$" % ("|".join(DAGEN), "|".join(MAANDEN)),
    re.IGNORECASE,
)
SLOT_RE = re.compile(r"^(\d{2}):(\d{2})\s*[-–]\s*(\d{2}):(\d{2})\s+(.*)$")
# vangt zowel "Banenzwemmen" als "Banen zwemmen Dames"
BANEN_RE = re.compile(r"^banen\s?zwemmen\b", re.IGNORECASE)


def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (rooster-ics)"})
    r.raise_for_status()
    return r.text


def parse_roster(html: str, today: datetime) -> list[dict]:
    """Tekst-gebaseerde parser: robuust tegen wijzigingen in de DOM."""
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]

    events, current_date = [], None
    for line in lines:
        m = DATE_RE.match(line)
        if m:
            day, month = int(m.group(1)), MAANDEN[m.group(2).lower()]
            year = today.year
            if month < today.month - 6:   # jaarwissel: rooster loopt vooruit
                year += 1
            current_date = datetime(year, month, day)
            continue

        m = SLOT_RE.match(line)
        if not m or current_date is None:
            continue

        h1, m1, h2, m2, label = m.groups()
        if not BANEN_RE.match(label):
            continue
        if any(w in label.lower() for w in UITSLUITEN):
            continue

        start = current_date.replace(hour=int(h1), minute=int(m1), tzinfo=TZ)
        end = current_date.replace(hour=int(h2), minute=int(m2), tzinfo=TZ)
        if end <= start:
            end += timedelta(days=1)

        events.append({"start": start, "end": end, "label": label})

    seen, unique = set(), []
    for e in events:
        key = (e["start"], e["end"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return sorted(unique, key=lambda e: e["start"])


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def build_ics(events: list[dict]) -> str:
    def utc(dt):
        return dt.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")

    stamp = datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    out = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//zwemrooster//NL",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(CALENDAR_NAME)}",
        "X-WR-TIMEZONE:Europe/Amsterdam",
        "X-PUBLISHED-TTL:PT12H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
    ]
    for e in events:
        uid = hashlib.sha1(
            f"{e['start'].isoformat()}|{e['end'].isoformat()}".encode()
        ).hexdigest()
        out += [
            "BEGIN:VEVENT",
            f"UID:{uid}@sportcentrum-feijenoord",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{utc(e['start'])}",
            f"DTEND:{utc(e['end'])}",
            f"SUMMARY:{esc(e['label'])}",
            f"LOCATION:{esc(LOCATION)}",
            "DESCRIPTION:Sportcentrum Feijenoord - wedstrijdbad (25m)",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def main() -> int:
    html = fetch_html(URL)
    events = parse_roster(html, datetime.now(TZ))
    if not events:
        print("Geen banenzwemmen-blokken gevonden - opzet website gewijzigd?", file=sys.stderr)
        return 1
    with open(OUTFILE, "w", encoding="utf-8") as f:
        f.write(build_ics(events))
    print(f"{len(events)} blokken weggeschreven naar {OUTFILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
