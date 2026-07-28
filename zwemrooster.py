#!/usr/bin/env python3
"""
Genereert banenzwemmen.ics uit het rooster van Zwemcentrum Rotterdam.

Filtert op: Banenzwemmen in het 25 meter doelgroepenbad.
Standaard zonder '55+' en zonder het 50m wedstrijdbad (zie CONFIG).

Het rooster staat gewoon in de HTML van de pagina (server-rendered), in
een vaste structuur:

    div.block-roster__day
        h3.block-roster__title                    -> "Maandag 27 juli"
        li.block-roster__program-item
            span.block-roster__program-item-time      -> "07:00 - 11:00"
            a.block-roster__program-item-activity     -> "Banenzwemmen"
            span.block-roster__program-item-location  -> "25 meter doelgroepenbad"

Gebruik:
    python zwemrooster.py            # schrijft banenzwemmen.ics
    python zwemrooster.py --debug    # toont wat er is gevonden en genegeerd
    python zwemrooster.py --dump     # schrijft rooster_debug.html (ruwe pagina)
"""

import hashlib
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# ---------------- CONFIG ----------------
URL = "https://www.sportbedrijfrotterdam.nl/locatie/zwemcentrum-rotterdam"
OUTFILE = "banenzwemmen.ics"
CALENDAR_NAME = "Banenzwemmen 25m - Zwemcentrum Rotterdam"
LOCATION = "Zwemcentrum Rotterdam, Annie M.G. Schmidtplein 8, 3083 NZ Rotterdam"
INCLUDE_55PLUS = False
INCLUDE_50M = False
MIN_EVENTS = 5  # minder gevonden = waarschijnlijk site gewijzigd -> foutmelding
TZ = ZoneInfo("Europe/Amsterdam")
# ----------------------------------------

MAANDEN = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10,
    "november": 11, "december": 12,
}
DAGEN = ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag")

DATUM_RE = re.compile(
    r"(?:%s)\s+(\d{1,2})\s+(%s)" % ("|".join(DAGEN), "|".join(MAANDEN)), re.I
)
TIJD_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(?:[-–]|tot)\s*(\d{1,2}):(\d{2})")


def fetch_html(url: str) -> str:
    r = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
            "Accept-Language": "nl-NL,nl;q=0.9",
        },
    )
    r.raise_for_status()
    return r.text


def tekst(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def parse_datum(titel: str, today: datetime) -> datetime | None:
    m = DATUM_RE.search(titel)
    if not m:
        return None
    dag, maand = int(m.group(1)), MAANDEN[m.group(2).lower()]
    jaar = today.year
    # het rooster loopt vooruit: een maand ver in het verleden hoort bij volgend jaar
    if maand < today.month - 6:
        jaar += 1
    return datetime(jaar, maand, dag)


def parse_roster(html: str, today: datetime, debug: bool = False) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    dagen = soup.select(".block-roster__day")

    if not dagen:
        print("Geen enkel .block-roster__day-element gevonden - de opzet van de "
              "pagina is gewijzigd. Draai met --dump en bekijk rooster_debug.html.",
              file=sys.stderr)
        return []

    events, genegeerd = [], []

    for dagblok in dagen:
        datum = parse_datum(tekst(dagblok.select_one(".block-roster__title")), today)
        if datum is None:
            continue

        for item in dagblok.select(".block-roster__program-item"):
            tijd = tekst(item.select_one('[class*="program-item-time"]'))
            activiteit = tekst(item.select_one('[class*="program-item-activity"]'))
            bad = tekst(item.select_one('[class*="program-item-location"]'))

            m = TIJD_RE.search(tijd)
            if not m or not activiteit:
                continue

            reden = None
            if not activiteit.lower().startswith("banenzwemmen"):
                reden = "geen banenzwemmen"
            elif not INCLUDE_55PLUS and "55+" in activiteit:
                reden = "55+"
            elif not INCLUDE_50M and not re.search(r"25\s*meter", bad, re.I):
                reden = f"ander bad ({bad or 'onbekend'})"

            if reden:
                genegeerd.append((datum.date(), tijd, activiteit, reden))
                continue

            h1, m1, h2, m2 = (int(g) for g in m.groups())
            start = datum.replace(hour=h1, minute=m1, tzinfo=TZ)
            eind = datum.replace(hour=h2, minute=m2, tzinfo=TZ)
            if eind <= start:
                eind += timedelta(days=1)

            events.append({"start": start, "end": eind,
                           "label": f"{activiteit} - {bad}" if bad else activiteit})

    # dedupe (voor het geval een blok twee keer op de pagina staat)
    gezien, uniek = set(), []
    for e in events:
        sleutel = (e["start"], e["end"])
        if sleutel not in gezien:
            gezien.add(sleutel)
            uniek.append(e)
    uniek.sort(key=lambda e: e["start"])

    if debug:
        print(f"\n{len(dagen)} dagen op de pagina", file=sys.stderr)
        print(f"\nGEVONDEN ({len(uniek)}):", file=sys.stderr)
        for e in uniek:
            print(f"  {e['start']:%a %d-%m %H:%M}-{e['end']:%H:%M}  {e['label']}",
                  file=sys.stderr)
        print(f"\nGENEGEERD ({len(genegeerd)}):", file=sys.stderr)
        for d, t, act, reden in genegeerd:
            print(f"  {d} {t}  {act}  -> {reden}", file=sys.stderr)

    return uniek


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
            f"UID:{uid}@zwemcentrum-rotterdam",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{utc(e['start'])}",
            f"DTEND:{utc(e['end'])}",
            "SUMMARY:Banenzwemmen 25m",
            f"LOCATION:{esc(LOCATION)}",
            f"DESCRIPTION:{esc(e['label'])}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def main() -> int:
    debug = "--debug" in sys.argv
    html = fetch_html(URL)

    if "--dump" in sys.argv:
        with open("rooster_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Ruwe pagina weggeschreven naar rooster_debug.html")

    events = parse_roster(html, datetime.now(TZ), debug=debug)

    if len(events) < MIN_EVENTS:
        print(
            f"Slechts {len(events)} blokken gevonden (drempel {MIN_EVENTS}) - "
            "opzet website waarschijnlijk gewijzigd. Bestand NIET overschreven. "
            "Draai met --debug --dump om te zien wat de pagina teruggeeft.",
            file=sys.stderr,
        )
        return 1

    with open(OUTFILE, "w", encoding="utf-8") as f:
        f.write(build_ics(events))
    eerste, laatste = events[0]["start"], events[-1]["start"]
    print(f"{len(events)} blokken weggeschreven naar {OUTFILE} "
          f"({eerste:%d-%m} t/m {laatste:%d-%m})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
