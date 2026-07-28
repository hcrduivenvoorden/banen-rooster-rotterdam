#!/usr/bin/env python3
"""
Genereert banenzwemmen.ics uit het rooster van Zwemcentrum Rotterdam.

Filtert op: Banenzwemmen in het 25 meter doelgroepenbad.
Standaard zonder '55+' en zonder het 50m wedstrijdbad (zie CONFIG).

Gebruik:
    python zwemrooster.py            # schrijft banenzwemmen.ics
    python zwemrooster.py --debug    # toont ook wat er is gevonden en genegeerd
    python zwemrooster.py --dump     # schrijft rooster_debug.txt met de ruwe paginatekst
"""

import hashlib
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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

DATE_RE = re.compile(
    r"^(?:%s)\s+(\d{1,2})\s+(%s)\b" % ("|".join(DAGEN), "|".join(MAANDEN)),
    re.IGNORECASE,
)
SLOT_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*(?:[-–]|tot)\s*(\d{1,2}):(\d{2})\s*(.*)$")
BAD_RE = re.compile(r"(25\s*m|50\s*m|doelgroepenbad|wedstrijdbad|instructiebad)", re.I)


def fetch_html(url: str) -> str:
    """Rendert de pagina met een echte browser.

    Het rooster wordt door JavaScript ingeladen: een gewone requests.get()
    levert een pagina ZONDER roosterregels op. Vandaar Playwright.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="nl-NL")
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        # wachten tot er daadwerkelijk tijdblokken in de tekst staan
        try:
            page.wait_for_function(
                "() => /\\d{2}:\\d{2}\\s*[-–]\\s*\\d{2}:\\d{2}/.test(document.body.innerText)",
                timeout=30_000,
            )
        except Exception:
            print("Waarschuwing: geen tijdpatroon gezien binnen 30s; "
                  "ga toch door met wat er staat.", file=sys.stderr)
        page.wait_for_timeout(2_000)  # laatste XHR's laten landen
        html = page.content()
        browser.close()
    return html


def page_lines(html: str) -> list[str]:
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def classify_bad(s: str) -> str | None:
    """'25m', '50m' of None als er geen badnaam in de tekst staat."""
    if not s:
        return None
    low = s.lower()
    if re.search(r"50\s*m|wedstrijdbad", low):
        return "50m"
    if re.search(r"25\s*m|doelgroepenbad", low):
        return "25m"
    return None


def parse_roster(html: str, today: datetime, debug: bool = False) -> list[dict]:
    """Tekst-gebaseerde parser: robuust tegen wijzigingen in de DOM.

    De badnaam kan op drie plekken staan: achter de activiteit op dezelfde regel,
    op de regel eronder, of als los kopje erboven. Alle drie worden afgevangen.
    """
    lines = page_lines(html)
    page_has_bad_info = any(BAD_RE.search(ln) for ln in lines)

    events, skipped = [], []
    current_date, current_bad = None, None

    for i, line in enumerate(lines):
        m = DATE_RE.match(line)
        if m:
            day, month = int(m.group(1)), MAANDEN[m.group(2).lower()]
            year = today.year
            # jaarwissel: rooster loopt vooruit, dus een maand ver in het verleden = volgend jaar
            if month < today.month - 6:
                year += 1
            current_date = datetime(year, month, day)
            continue

        # losse regel die alleen een badnaam is -> geldt als kopje voor wat volgt
        if not SLOT_RE.match(line) and BAD_RE.search(line) and len(line) < 60:
            current_bad = classify_bad(line) or current_bad
            continue

        m = SLOT_RE.match(line)
        if not m or current_date is None:
            continue

        h1, m1, h2, m2, label = m.groups()
        label = label.strip()
        if not label:
            continue

        if not label.lower().startswith("banenzwemmen"):
            skipped.append((current_date.date(), f"{h1}:{m1}", label, "geen banenzwemmen"))
            continue

        # badnaam: 1) op de regel zelf, 2) op de regel eronder, 3) uit het laatste kopje.
        # De regel eronder telt alleen als hij niet het kopje van een NIEUWE sectie is
        # (herkenbaar doordat er direct een datumregel op volgt).
        volgende = lines[i + 1] if i + 1 < len(lines) else ""
        daarna = lines[i + 2] if i + 2 < len(lines) else ""
        bad_onder = classify_bad(volgende) if not DATE_RE.match(daarna) else None
        bad = classify_bad(label) or bad_onder or current_bad

        if not INCLUDE_55PLUS and "55+" in label:
            skipped.append((current_date.date(), f"{h1}:{m1}", label, "55+"))
            continue
        if not INCLUDE_50M and bad == "50m":
            skipped.append((current_date.date(), f"{h1}:{m1}", label, "50m bad"))
            continue
        if not INCLUDE_50M and bad is None and page_has_bad_info:
            # pagina noemt baden wel, maar niet bij dit blok -> niet gokken
            skipped.append((current_date.date(), f"{h1}:{m1}", label, "bad onbekend"))
            continue

        start = current_date.replace(hour=int(h1), minute=int(m1), tzinfo=TZ)
        end = current_date.replace(hour=int(h2), minute=int(m2), tzinfo=TZ)
        if end <= start:
            end += timedelta(days=1)

        events.append({"start": start, "end": end, "label": label, "bad": bad or "onbekend"})

    # dedupe (rooster toont soms overlap tussen 'deze week' en 'volgende week')
    seen, unique = set(), []
    for e in events:
        key = (e["start"], e["end"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    unique.sort(key=lambda e: e["start"])

    if debug:
        print(f"\nPagina noemt badnamen: {page_has_bad_info}", file=sys.stderr)
        print(f"\nGEVONDEN ({len(unique)}):", file=sys.stderr)
        for e in unique:
            print(f"  {e['start']:%a %d-%m %H:%M}-{e['end']:%H:%M}  {e['label']}  [{e['bad']}]",
                  file=sys.stderr)
        print(f"\nGENEGEERD ({len(skipped)}):", file=sys.stderr)
        for d, t, lab, reden in skipped:
            print(f"  {d} {t}  {lab}  -> {reden}", file=sys.stderr)

    return unique


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
        with open("rooster_debug.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(page_lines(html)))
        print("Ruwe paginatekst weggeschreven naar rooster_debug.txt")

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
