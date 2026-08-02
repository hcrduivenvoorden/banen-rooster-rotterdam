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
# tijdvak, met optioneel het label erachter op dezelfde regel
SLOT_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\s*(.*)$")
BANEN_RE = re.compile(r"^banen\s?zwemmen\b", re.IGNORECASE)


def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (rooster-ics)"})
    r.raise_for_status()
    return r.text


def to_lines(html: str) -> list[str]:
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def parse_roster(html: str, today: datetime) -> list[dict]:
    """
    Loopt regel voor regel. Een tijdvak start een blok; het label kan op
    dezelfde regel staan of over de volgende regels verdeeld zijn (de site
    zet de activiteitsnaam in een aparte link).
    """
    lines = to_lines(html)
    events, current_date = [], None
    i = 0
    while i < len(lines):
        line = lines[i]

        m = DATE_RE.match(line)
        if m:
            day, month = int(m.group(1)), MAANDEN[m.group(2).lower()]
            year = today.year
            if month < today.month - 6:   # jaarwissel: rooster loopt vooruit
                year += 1
            current_date = datetime(year, month, day)
            i += 1
            continue

        m = SLOT_RE.match(line)
        if not m or current_date is None:
            i += 1
            continue

        h1, m1, h2, m2, rest = m.groups()
        delen = [rest.strip()] if rest.strip() else []

        # label aanvullen met volgende regels tot het volgende tijdvak of de
        # volgende dag; maximaal 3 stukjes zodat losse tekst niet meelift
        j = i + 1
        while j < len(lines) and len(delen) < 3:
            nxt = lines[j]
            if DATE_RE.match(nxt) or SLOT_RE.match(nxt) or len(nxt) > 60:
                break
            delen.append(nxt)
            j += 1

        label = " ".join(delen).strip()
        i = j

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


def dump_debug(html: str) -> None:
    """Print bij 0 treffers de roostersectie, zodat de log bruikbaar is."""
    lines = to_lines(html)
    idx = next((n for n, ln in enumerate(lines) if ln.lower() == "deze week"), None)
    print("--- debug: regels rond de roostersectie ---", file=sys.stderr)
    if idx is None:
        print("Kop 'Deze week' niet gevonden in de pagina.", file=sys.stderr)
        for ln in lines[:40]:
            print(repr(ln), file=sys.stderr)
    else:
        for ln in lines[idx:idx + 60]:
            print(repr(ln), file=sys.stderr)


def main() -> int:
    html = fetch_html(URL)
    events = parse_roster(html, datetime.now(TZ))
    if not events:
        print("Geen banenzwemmen-blokken gevonden.", file=sys.stderr)
        dump_debug(html)
        return 1
    with open(OUTFILE, "w", encoding="utf-8") as f:
        f.write(build_ics(events))
    print(f"{len(events)} blokken weggeschreven naar {OUTFILE}")
    for e in events:
        print("  ", e["start"].strftime("%a %d-%m %H:%M"), "-",
              e["end"].strftime("%H:%M"), e["label"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
