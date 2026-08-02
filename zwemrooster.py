#!/usr/bin/env python3
"""
Genereert twee ICS-bestanden met banenzwemmen in Rotterdam:

  banenzwemmen.ics         -> Sportcentrum Feijenoord (voorkeur)
  banenzwemmen-overig.ics  -> Zwemcentrum Rotterdam, De Wilgenring, Oostelijk Zwembad

Abonneer je in Apple Agenda op beide URL's; elk abonnement is een eigen
agenda met een eigen kleur.
"""

import hashlib
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("Europe/Amsterdam")

# ---------------- CONFIG ----------------

# Locaties met een roosterpagina op sportbedrijfrotterdam.nl
WEB_LOCATIES = [
    {
        "naam": "Feijenoord",
        "url": "https://www.sportbedrijfrotterdam.nl/locatie/sportcentrum-feijenoord",
        "adres": "Sportcentrum Feijenoord, Laan op Zuid 1055, 3072 DB Rotterdam",
        "uitsluiten": ["dames", "55+"],
        "bestand": "voorkeur",
    },
    {
        "naam": "Zwemcentrum",
        "url": "https://www.sportbedrijfrotterdam.nl/locatie/zwemcentrum-rotterdam",
        "adres": "Zwemcentrum Rotterdam, Annie M.G. Schmidtplein 8, 3083 NZ Rotterdam",
        "uitsluiten": ["dames", "55+", "50 meter"],   # alleen het 25m doelgroepenbad
        "bestand": "overig",
    },
    {
        "naam": "Wilgenring",
        "url": "https://www.sportbedrijfrotterdam.nl/locatie/sportcentrum-de-wilgenring",
        "adres": "Sportcentrum de Wilgenring, Melanchtonweg 70, 3052 KV Rotterdam",
        "uitsluiten": ["dames", "55+"],
        "bestand": "overig",
    },
]

# Oostelijk Zwembad publiceert geen roosterpagina maar een PDF zonder datums.
# Daarom hier het vaste weekpatroon, geldig tot en met GELDIG_TOT.
# Bron: Zomerrooster_Oost_2026.3.pdf (20 juli t/m 30 augustus 2026).
OOST = {
    "naam": "Oost",
    "adres": "Oostelijk Zwembad, Gerdesiaweg 480, 3061 RA Rotterdam",
    "pagina": "https://oostelijkzwembad.sportfondsen.nl/tijden-tarieven/",
    "pdf_bekend": "Zomerrooster_Oost_2026.3.pdf",
    "geldig_tot": "2026-08-30",
    "patroon": {          # 0 = maandag
        0: [("07:00", "08:00"), ("12:00", "13:00"), ("15:00", "16:00"), ("20:00", "21:00")],
        1: [("07:00", "08:00"), ("12:00", "13:00"), ("15:00", "16:00")],
        2: [("07:00", "08:00"), ("12:00", "13:00"), ("17:30", "18:30"), ("20:00", "21:00")],
        3: [],
        4: [("08:00", "09:00"), ("12:00", "13:00"), ("18:30", "19:30")],
        5: [("12:00", "13:00")],
        6: [],
    },
    "bestand": "overig",
}

BESTANDEN = {
    "voorkeur": ("banenzwemmen.ics", "Banenzwemmen - Feijenoord"),
    "overig": ("banenzwemmen-overig.ics", "Banenzwemmen - overige baden"),
}

WEKEN_VOORUIT = 3   # hoe ver het vaste patroon van Oost vooruit wordt gezet
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
SLOT_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})\s*(.*)$")
BANEN_RE = re.compile(r"^banen\s?zwemmen\b", re.IGNORECASE)


def fetch(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (rooster-ics)"})
    r.raise_for_status()
    return r.text


def to_lines(html: str) -> list[str]:
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def parse_roster(html: str, loc: dict, today: datetime) -> list[dict]:
    """
    Een tijdvak start een blok; het label staat op dezelfde regel of is over
    de volgende regels verdeeld (de site zet de activiteit in een aparte link).
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
        if any(w in label.lower() for w in loc["uitsluiten"]):
            continue

        start = current_date.replace(hour=int(h1), minute=int(m1), tzinfo=TZ)
        end = current_date.replace(hour=int(h2), minute=int(m2), tzinfo=TZ)
        if end <= start:
            end += timedelta(days=1)
        events.append({
            "start": start, "end": end, "label": label,
            "locatie": loc["naam"], "adres": loc["adres"],
        })
    return events


def oost_events(today: datetime) -> list[dict]:
    """Zet het vaste weekpatroon om in losse afspraken, tot de einddatum."""
    tot = datetime.strptime(OOST["geldig_tot"], "%Y-%m-%d").replace(tzinfo=TZ)
    events = []
    dag = today.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(WEKEN_VOORUIT * 7):
        if dag > tot:
            break
        for h1, h2 in OOST["patroon"].get(dag.weekday(), []):
            s = dag.replace(hour=int(h1[:2]), minute=int(h1[3:]))
            e = dag.replace(hour=int(h2[:2]), minute=int(h2[3:]))
            events.append({
                "start": s, "end": e, "label": "Banenzwemmen",
                "locatie": OOST["naam"], "adres": OOST["adres"],
            })
        dag += timedelta(days=1)

    # waarschuw als het rooster bijna verlopen is of als de PDF is vervangen
    resterend = (tot - today).days
    if resterend < 14:
        print(f"LET OP: patroon Oost verloopt over {resterend} dagen "
              f"({OOST['geldig_tot']}) - nieuw rooster invoeren.", file=sys.stderr)
    try:
        if OOST["pdf_bekend"] not in fetch(OOST["pagina"]):
            print("LET OP: Oost linkt naar een andere rooster-PDF dan bekend "
                  f"({OOST['pdf_bekend']}) - patroon controleren.", file=sys.stderr)
    except Exception as exc:
        print(f"Kon pagina Oost niet controleren: {exc}", file=sys.stderr)
    return events


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def build_ics(events: list[dict], naam: str) -> str:
    def utc(dt):
        return dt.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")

    stamp = datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    out = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//zwemrooster//NL",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(naam)}", "X-WR-TIMEZONE:Europe/Amsterdam",
        "X-PUBLISHED-TTL:PT12H", "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
    ]
    for e in events:
        uid = hashlib.sha1(
            f"{e['locatie']}|{e['start'].isoformat()}|{e['end'].isoformat()}".encode()
        ).hexdigest()
        out += [
            "BEGIN:VEVENT",
            f"UID:{uid}@zwemrooster-rotterdam",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{utc(e['start'])}",
            f"DTEND:{utc(e['end'])}",
            f"SUMMARY:{esc(e['locatie'])} - banenzwemmen",
            f"LOCATION:{esc(e['adres'])}",
            f"DESCRIPTION:{esc(e['label'])}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def dump_debug(html: str, naam: str) -> None:
    lines = to_lines(html)
    idx = next((n for n, ln in enumerate(lines) if ln.lower() == "deze week"), None)
    print(f"--- debug {naam}: regels rond de roostersectie ---", file=sys.stderr)
    for ln in (lines[idx:idx + 60] if idx is not None else lines[:40]):
        print(repr(ln), file=sys.stderr)


def main() -> int:
    today = datetime.now(TZ)
    per_bestand: dict[str, list[dict]] = {k: [] for k in BESTANDEN}
    mislukt = []

    for loc in WEB_LOCATIES:
        try:
            html = fetch(loc["url"])
        except Exception as exc:
            print(f"{loc['naam']}: ophalen mislukt ({exc})", file=sys.stderr)
            mislukt.append(loc["naam"])
            continue
        ev = parse_roster(html, loc, today)
        print(f"{loc['naam']}: {len(ev)} blokken")
        if not ev:
            mislukt.append(loc["naam"])
            dump_debug(html, loc["naam"])
        per_bestand[loc["bestand"]] += ev

    ev = oost_events(today)
    print(f"Oost: {len(ev)} blokken (vast patroon)")
    per_bestand[OOST["bestand"]] += ev

    for sleutel, (bestandsnaam, kalendernaam) in BESTANDEN.items():
        events = per_bestand[sleutel]
        if not events:
            print(f"{bestandsnaam}: niets te schrijven, bestand blijft ongewijzigd",
                  file=sys.stderr)
            continue
        seen, uniek = set(), []
        for e in sorted(events, key=lambda e: (e["start"], e["locatie"])):
            key = (e["locatie"], e["start"], e["end"])
            if key not in seen:
                seen.add(key)
                uniek.append(e)
        with open(bestandsnaam, "w", encoding="utf-8") as f:
            f.write(build_ics(uniek, kalendernaam))
        print(f"{bestandsnaam}: {len(uniek)} afspraken")

    # faalt alleen als een locatie niets opleverde, zodat je het in de log ziet
    return 1 if mislukt else 0


if __name__ == "__main__":
    raise SystemExit(main())
