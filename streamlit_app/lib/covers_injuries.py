"""Covers.com NCAAF injury report — primary feed when ESPN league endpoint is empty/stale."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .team_registry import resolve_canonical

COVERS_URL = "https://www.covers.com/sport/football/ncaaf/injuries"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

STATUS_RE = re.compile(
    r"^(Out|Questionable|Doubtful|Probable|IR|Injured Reserve|Suspended|PUP)\s*[-–—]?\s*(.*)$",
    re.I,
)
DATE_RE = re.compile(
    r"\(?\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+(\d{1,2})\s*\)?",
    re.I,
)
MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _clean_name(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip())


def _parse_status(raw: str) -> tuple[str, str, str | None]:
    text = re.sub(r"\s+", " ", str(raw or "").replace("\r", " ").replace("\n", " ")).strip()
    date_iso = None
    m_date = DATE_RE.search(text)
    if m_date:
        try:
            year = datetime.now(timezone.utc).year
            month = MONTHS[m_date.group(2).lower()[:3]]
            day = int(m_date.group(3))
            dt = datetime(year, month, day, tzinfo=timezone.utc)
            if dt > datetime.now(timezone.utc):
                dt = dt.replace(year=year - 1)
            date_iso = dt.isoformat()
        except (KeyError, ValueError):
            date_iso = None
        text = DATE_RE.sub("", text).strip(" ()")

    m = STATUS_RE.match(text)
    if m:
        status = m.group(1).upper()
        if status == "IR":
            status = "OUT"
        injury = (m.group(2) or "").strip(" -–—")
        return status, injury, date_iso

    upper = text.upper()
    if "QUESTION" in upper:
        return "QUESTIONABLE", text, date_iso
    if "DOUBT" in upper:
        return "DOUBTFUL", text, date_iso
    if "OUT" in upper or "IR" in upper:
        return "OUT", text, date_iso
    return "LISTED", text, date_iso


def fetch_covers_injuries() -> pd.DataFrame:
    r = SESSION.get(COVERS_URL, timeout=45)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rows: list[dict[str, Any]] = []

    for panel in soup.select('[id^="injuryCollapse"]'):
        abbr = panel.get("id", "").replace("injuryCollapse", "")
        if not abbr or re.search(r"AALA\d", abbr):
            continue
        table = panel.find("table")
        if not table:
            continue
        team_name = resolve_canonical(abbr) or abbr
        tbody = table.find("tbody") or table
        trs = tbody.find_all("tr")
        i = 0
        while i < len(trs):
            tr = trs[i]
            if "collapse" in (tr.get("class") or []):
                i += 1
                continue
            tds = tr.find_all("td")
            if len(tds) < 3:
                i += 1
                continue
            player = _clean_name(tds[0].get_text(" ", strip=True))
            pos = _clean_name(tds[1].get_text(" ", strip=True))
            status_raw = tds[2].get_text(" ", strip=True)
            note = ""
            if i + 1 < len(trs) and "collapse" in (trs[i + 1].get("class") or []):
                note = _clean_name(trs[i + 1].get_text(" ", strip=True))
                i += 1
            status, injury, updated = _parse_status(status_raw)
            if not player:
                i += 1
                continue
            rows.append(
                {
                    "player": player,
                    "position": pos,
                    "team": team_name,
                    "team_abbr": abbr,
                    "headshot": "",
                    "status": status,
                    "injury": injury,
                    "note": note or injury,
                    "updated": updated or datetime.now(timezone.utc).isoformat(),
                    "source": "covers",
                }
            )
            i += 1

    return pd.DataFrame(rows)
