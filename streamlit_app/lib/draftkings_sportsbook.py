"""DraftKings CFB team yard markets — website scrape only (no The Odds API)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .team_registry import resolve_canonical, teams_match

CFB_LEAGUE_ID = "87637"

_TEAM_YARDS_SUB_RE = re.compile(
    r"team\s+total\s+"
    r"(?:(passing|rushing|receiving|pass|rush|rec)\s+)?"
    r"yards?"
    r"(?:\s*(?:[-–—]|1st\s+half|\b1h\b|\b2h\b|\bq1\b|\b1st\s+qtr\b|\b1st\s+quarter\b))?",
    re.I,
)
_ALT_RE = re.compile(r"\balt(?:ernate)?\b", re.I)
_MILESTONE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*\+?")
_SIDE_RE = re.compile(r"^(over|under)$", re.I)

# DraftKings subcategory IDs → market key (CFB team yards hub)
_KNOWN_SUBCAT_MARKET_KEY: dict[str, str] = {
    "18914": "team_total_yds",
    "20073": "team_total_yds_h1",
    "18912": "team_total_rush_yds",
    "20075": "team_total_rush_yds_h1",
    "18913": "team_total_rec_yds",
    "20074": "team_total_rec_yds_h1",
}

# Max milestone (display) yards by period × category — drop obvious mis-tags
_LINE_CEILING: dict[tuple[str, str], float] = {
    ("q1", "total"): 175.0,
    ("q1", "rush"): 95.0,
    ("q1", "pass"): 115.0,
    ("q1", "rec"): 95.0,
    ("h1", "total"): 275.0,
    ("h1", "rush"): 185.0,
    ("h1", "pass"): 225.0,
    ("h1", "rec"): 200.0,
    ("game", "total"): 525.0,
    ("game", "rush"): 390.0,
    ("game", "pass"): 490.0,
    ("game", "rec"): 430.0,
}

_NAV_SLUG_TO_KEY: dict[str, str] = {
    "team-total-yards": "team_total_yds",
    "team-total-yards---1h": "team_total_yds_h1",
    "team-total-yards---q1": "team_total_yds_q1",
    "team-total-rushing-yards": "team_total_rush_yds",
    "team-total-rushing-yards---1h": "team_total_rush_yds_h1",
    "team-total-rushing-yards---q1": "team_total_rush_yds_q1",
    "team-total-passing-yards": "team_total_pass_yds",
    "team-total-passing-yards---1h": "team_total_pass_yds_h1",
    "team-total-passing-yards---q1": "team_total_pass_yds_q1",
    "team-total-receiving-yards": "team_total_rec_yds",
    "team-total-receiving-yards---1h": "team_total_rec_yds_h1",
    "team-total-receiving-yards---q1": "team_total_rec_yds_q1",
}

_LAST_FETCH_MODE: str = "unknown"
_LAST_FETCH_ERROR: str | None = None


def last_fetch_status() -> dict[str, str | None]:
    return {"mode": _LAST_FETCH_MODE, "error": _LAST_FETCH_ERROR}


def _subcategory_market_key(name: str, seo_id: str = "") -> str:
    if seo_id and seo_id in _NAV_SLUG_TO_KEY:
        key = _NAV_SLUG_TO_KEY[seo_id]
    else:
        key = _name_to_market_key(name)
    if _ALT_RE.search(name) and not key.startswith("alternate_"):
        key = f"alternate_{key}"
    return key


def _name_to_market_key(name: str) -> str:
    n = name.lower()
    category = "total"
    if re.search(r"\b(passing|pass)\b", n):
        category = "pass"
    elif re.search(r"\b(rushing|rush)\b", n):
        category = "rush"
    elif re.search(r"\b(receiving|rec)\b", n):
        category = "receiving"
    period = ""
    if re.search(r"\b(1st half|1h|first half)\b", n):
        period = "_h1"
    elif re.search(r"\b(1st quarter|1st qtr|q1)\b", n):
        period = "_q1"
    if category == "total":
        return f"team_total_yds{period}"
    if category == "receiving":
        return f"team_total_rec_yds{period}"
    return f"team_total_{category}_yds{period}"


def _is_team_yards_subcategory(name: str, seo_id: str = "") -> bool:
    blob = f"{name} {seo_id}".strip()
    if not blob:
        return False
    if _TEAM_YARDS_SUB_RE.search(blob):
        return True
    if seo_id in _NAV_SLUG_TO_KEY:
        return True
    low = blob.lower()
    return "team-yards" in low or ("team-total" in low and "yard" in low)


def discover_team_yard_subcategories(*, root: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    root = root or {}
    eg = root.get("eventGroup") or {}
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for cat in eg.get("offerCategories") or []:
        cat_id = cat.get("offerCategoryId")
        cat_name = str(cat.get("name") or "")
        for desc in cat.get("offerSubcategoryDescriptors") or []:
            sub_id = desc.get("subcategoryId")
            if sub_id is None:
                continue
            sub_name = str(desc.get("name") or desc.get("subcategoryName") or "")
            seo_id = str(desc.get("seoIdentifier") or desc.get("urlName") or "")
            if not _is_team_yards_subcategory(sub_name, seo_id):
                continue
            key = (str(cat_id), str(sub_id))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "category_id": cat_id,
                    "category_name": cat_name,
                    "subcategory_id": sub_id,
                    "subcategory_name": sub_name,
                    "seo_id": seo_id,
                    "market_key": _subcategory_market_key(sub_name, seo_id),
                }
            )
    return out


def _participants_to_sides(participants: list[dict[str, Any]]) -> tuple[str, str]:
    home = away = ""
    for p in participants or []:
        role = str(p.get("venueRole") or p.get("venue_role") or "").lower()
        name = str(p.get("name") or "").strip()
        if role == "home" and name:
            home = name
        elif role == "away" and name:
            away = name
    return home, away


def parse_dk_events_modern(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in payloads or []:
        body = item.get("body") or {}
        if not isinstance(body, dict):
            continue
        for ev in body.get("events") or []:
            eid = ev.get("id") or ev.get("eventId")
            if eid is None:
                continue
            home, away = _participants_to_sides(ev.get("participants") or [])
            if not away or not home:
                name = str(ev.get("name") or "")
                if " @ " in name:
                    away, home = name.split(" @ ", 1)
                elif re.search(r"\s+AT\s+", name, re.I):
                    parts = re.split(r"\s+AT\s+", name, maxsplit=1, flags=re.I)
                    if len(parts) == 2:
                        away, home = parts
            by_id[str(eid)] = {
                "event_id": str(eid),
                "home": str(home or "").strip(),
                "away": str(away or "").strip(),
                "name": ev.get("name"),
                "start_date": ev.get("startEventDate") or ev.get("startDate"),
            }
    return list(by_id.values())


def parse_dk_events(*, root: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    events = (root or {}).get("eventGroup", {}).get("events") or []
    out: list[dict[str, Any]] = []
    for ev in events:
        eid = ev.get("eventId")
        if eid is None:
            continue
        away = ev.get("teamName1") or ev.get("awayTeamName") or ""
        home = ev.get("teamName2") or ev.get("homeTeamName") or ""
        if not away or not home:
            name = str(ev.get("name") or "")
            if " @ " in name:
                away, home = name.split(" @ ", 1)
            elif re.search(r"\s+AT\s+", name, re.I):
                parts = re.split(r"\s+AT\s+", name, maxsplit=1, flags=re.I)
                if len(parts) == 2:
                    away, home = parts
        out.append(
            {
                "event_id": str(eid),
                "home": str(home or "").strip(),
                "away": str(away or "").strip(),
                "name": ev.get("name"),
                "start_date": ev.get("startDate"),
            }
        )
    return out


def _extract_all_offers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk JSON tree and collect any object that looks like a DK offer."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("outcomes") and (node.get("eventId") is not None or node.get("label")):
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def _parse_line(raw: Any, selection: str, description: str) -> float | None:
    if raw is not None and str(raw).strip() not in ("", "nan", "None"):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    for text in (selection, description):
        m = _MILESTONE_RE.search(str(text or ""))
        if m:
            val = float(m.group(1))
            if "+" in str(text) and val == int(val):
                return val - 0.5
            return val
    return None


def _row_team_and_side(selection: str, description: str) -> tuple[str, str | None]:
    sel = str(selection or "").strip()
    desc = str(description or "").strip()
    if _SIDE_RE.fullmatch(sel):
        return desc, sel.lower()
    if _SIDE_RE.fullmatch(desc):
        return sel, desc.lower()
    side = "over" if "+" in sel or "+" in desc else None
    team = desc if desc and not _SIDE_RE.fullmatch(desc) else sel
    return team, side


def _subcategory_id_from_url(url: str) -> str | None:
    m = re.search(r"templateVars=87637(?:%2C|,)(\d+)", url or "")
    if m:
        return m.group(1)
    m = re.search(r"/subcategories/(\d+)", url or "")
    return m.group(1) if m else None


def _period_suffix(market_key: str) -> str:
    mk = str(market_key or "").lower()
    if "_q1" in mk:
        return "_q1"
    if "_h1" in mk:
        return "_h1"
    return ""


def _market_category_period(market_key: str) -> tuple[str, str]:
    mk = str(market_key or "").lower()
    period = "game"
    if "_q1" in mk:
        period = "q1"
    elif "_h1" in mk:
        period = "h1"
    cat = "total"
    if "rush" in mk:
        cat = "rush"
    elif "pass" in mk:
        cat = "pass"
    elif "rec" in mk:
        cat = "rec"
    return cat, period


def _milestone_display_yards(line: float | None) -> float | None:
    if line is None:
        return None
    try:
        ln = float(line)
    except (TypeError, ValueError):
        return None
    return ln + 0.5 if abs(ln % 1 - 0.5) < 0.01 else ln


def _line_plausible_for_market(market_key: str, line: float | None) -> bool:
    display = _milestone_display_yards(line)
    if display is None:
        return False
    cat, period = _market_category_period(market_key)
    ceiling = _LINE_CEILING.get((period, cat), 600.0)
    floor = 10.0 if period == "q1" else 20.0
    return floor <= display <= ceiling


def _build_subcategory_map(payloads: list[dict[str, Any]]) -> dict[str, str]:
    """Learn subcategoryId → market_key from DK marketType names in captured payloads."""
    from collections import Counter

    votes: dict[str, Counter[str]] = {}
    for item in payloads or []:
        body = item.get("body") or {}
        if not isinstance(body, dict):
            continue
        for market in body.get("markets") or []:
            sid = str(market.get("subcategoryId") or "")
            if not sid:
                continue
            mt = str((market.get("marketType") or {}).get("name") or market.get("name") or "")
            mk = _name_to_market_key(mt)
            if not mk:
                continue
            votes.setdefault(sid, Counter())[mk] += 1

    learned = {sid: ctr.most_common(1)[0][0] for sid, ctr in votes.items() if ctr}
    merged = dict(learned)
    merged.update(_KNOWN_SUBCAT_MARKET_KEY)
    return merged


def _payload_trusted(item: dict[str, Any], subcat_map: dict[str, str]) -> bool:
    """Drop captures where the URL subcategory doesn't match the markets in the body."""
    url = str(item.get("url") or "")
    body = item.get("body") or {}
    if not isinstance(body, dict):
        return False
    url_sub = _subcategory_id_from_url(url) or str(item.get("subcategory_id") or "")
    if not url_sub:
        return True
    body_subs = {
        str(m.get("subcategoryId"))
        for m in (body.get("markets") or [])
        if isinstance(m, dict) and m.get("subcategoryId") is not None
    }
    if body_subs and url_sub not in body_subs:
        return False
    mapped = subcat_map.get(url_sub)
    tagged = str(item.get("market_key") or "")
    if mapped and tagged:
        if _period_suffix(tagged) != _period_suffix(mapped):
            return False
        t_cat, _ = _market_category_period(tagged)
        m_cat, _ = _market_category_period(mapped)
        if t_cat != m_cat:
            return False
    return True


def _american_odds(raw: Any) -> int | float | None:
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw).replace("−", "-").replace("–", "-").strip())
    except (TypeError, ValueError):
        return None


def _market_key_from_dk_market(
    market: dict[str, Any],
    *,
    subcat_map: dict[str, str] | None = None,
    payload_sub_id: str | None = None,
) -> str | None:
    sid = str(market.get("subcategoryId") or payload_sub_id or "")
    if payload_sub_id and sid and sid != str(payload_sub_id):
        return None
    mt = str((market.get("marketType") or {}).get("name") or market.get("name") or "")
    inferred = _name_to_market_key(mt)
    if subcat_map and sid in subcat_map:
        mapped = subcat_map[sid]
        if inferred and _period_suffix(inferred) and _period_suffix(inferred) != _period_suffix(mapped):
            return inferred
        if inferred:
            i_cat, i_per = _market_category_period(inferred)
            m_cat, m_per = _market_category_period(mapped)
            if i_cat != m_cat or (i_per != m_per and i_per in ("q1", "h1")):
                return inferred
        return mapped
    return inferred or None


def _parse_modern_markets_payload(
    body: dict[str, Any],
    *,
    subcat_map: dict[str, str] | None = None,
    payload_sub_id: str | None = None,
    subcategory_name: str = "",
) -> list[dict[str, Any]]:
    markets_by_id: dict[str, dict[str, Any]] = {}
    for market in body.get("markets") or []:
        if not isinstance(market, dict):
            continue
        mid = market.get("id")
        if mid is not None:
            markets_by_id[str(mid)] = market

    rows: list[dict[str, Any]] = []
    for sel in body.get("selections") or []:
        if not isinstance(sel, dict):
            continue
        market = markets_by_id.get(str(sel.get("marketId") or "")) or {}
        event_id = market.get("eventId") or sel.get("eventId")
        mk = _market_key_from_dk_market(
            market,
            subcat_map=subcat_map,
            payload_sub_id=payload_sub_id,
        )
        if not mk:
            continue
        mt_name = str((market.get("marketType") or {}).get("name") or market.get("name") or subcategory_name)

        participants = sel.get("participants") or market.get("participants") or []
        team = ""
        if participants and isinstance(participants[0], dict):
            team = str(participants[0].get("name") or "").strip()
        label = str(sel.get("label") or "")
        milestone = sel.get("milestoneValue")
        if milestone is not None:
            try:
                line = float(milestone) - 0.5
            except (TypeError, ValueError):
                line = _parse_line(None, label, team)
        else:
            line = _parse_line(sel.get("line"), label, team)

        if not _line_plausible_for_market(mk, line):
            continue

        odds = sel.get("displayOdds") or {}
        price = _american_odds(odds.get("american") or odds.get("americanOdds")) or _outcome_price(sel)

        rows.append(
            {
                "event_id": str(event_id) if event_id is not None else "",
                "market_key": mk,
                "market": mt_name or mk,
                "selection": label,
                "description": team,
                "line": line,
                "price": price,
                "side": "over" if "+" in label or milestone is not None else None,
                "book_id": "draftkings",
                "source": "draftkings.com",
            }
        )
    return rows


def discover_subcategories_modern(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in payloads or []:
        body = item.get("body") or {}
        tagged = str(item.get("market_key") or "")
        if not isinstance(body, dict):
            continue
        for market in body.get("markets") or []:
            sub_id = market.get("subcategoryId")
            if sub_id is None:
                continue
            mt_name = str((market.get("marketType") or {}).get("name") or market.get("name") or "")
            mk = _market_key_from_dk_market(market, subcat_map=_KNOWN_SUBCAT_MARKET_KEY)
            if not mk:
                continue
            key = (str(sub_id), mk)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "subcategory_id": sub_id,
                    "subcategory_name": mt_name,
                    "market_key": mk,
                }
            )
    return out


def _outcome_price(outcome: dict[str, Any]) -> int | float | None:
    for key in ("oddsAmerican", "oddsAmericanDisplay", "americanOdds", "price"):
        val = outcome.get(key)
        if val is None or val == "":
            continue
        try:
            return int(str(val).replace("−", "-").strip())
        except (TypeError, ValueError):
            continue
    try:
        dec = float(outcome.get("oddsDecimal") or outcome.get("decimalOdds") or 0)
        if dec >= 2.0:
            return int(round((dec - 1) * 100))
        if dec > 1.0:
            return int(round(-100 / (dec - 1)))
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def _offers_to_rows(
    offers: list[dict[str, Any]],
    *,
    market_key: str,
    subcategory_name: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for market in offers:
        event_id = market.get("eventId") or market.get("eventGroupId")
        label = str(market.get("label") or subcategory_name or market_key)
        for outcome in market.get("outcomes") or []:
            if not isinstance(outcome, dict):
                continue
            participant = str(outcome.get("participant") or outcome.get("participantName") or "")
            sel = str(outcome.get("label") or "")
            desc = participant or label
            team, side = _row_team_and_side(sel, desc)
            rows.append(
                {
                    "event_id": str(event_id) if event_id is not None else "",
                    "market_key": market_key,
                    "market": subcategory_name or label,
                    "selection": sel,
                    "description": team,
                    "line": _parse_line(outcome.get("line"), sel, desc),
                    "price": _outcome_price(outcome),
                    "side": side,
                    "book_id": "draftkings",
                    "source": "draftkings.com",
                }
            )
    return rows


def _market_key_from_payload_url(url: str, fallback: str | None) -> str:
    if fallback:
        return fallback
    m = re.search(r"nav_1=([a-z0-9-]+)", url, re.I)
    if m and m.group(1) in _NAV_SLUG_TO_KEY:
        return _NAV_SLUG_TO_KEY[m.group(1)]
    m = re.search(r"/subcategories/(\d+)", url)
    return fallback or f"team_total_yds_{m.group(1) if m else 'unknown'}"


def fetch_team_yard_quotes() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Pull all team-yard tabs from the DraftKings website via Edge/Chrome."""
    global _LAST_FETCH_MODE, _LAST_FETCH_ERROR

    from .draftkings_browser import browser_scrape_team_yards

    scrape = browser_scrape_team_yards()
    payloads = scrape.get("payloads") or []
    root = scrape.get("eventgroup") or {}
    subs = discover_team_yard_subcategories(root=root if root else None)
    if not subs:
        subs = discover_subcategories_modern(payloads)
    events = parse_dk_events(root=root if root else None)
    if not events:
        events = parse_dk_events_modern(payloads)

    subcat_map = _build_subcategory_map(payloads)
    sub_by_id = {str(s["subcategory_id"]): s for s in subs}
    rows: list[dict[str, Any]] = []
    seen_row: set[tuple] = set()

    for item in payloads:
        if not _payload_trusted(item, subcat_map):
            continue
        url = str(item.get("url") or "")
        body = item.get("body") or {}
        if not isinstance(body, dict):
            continue
        sub_id = _subcategory_id_from_url(url) or str(item.get("subcategory_id") or "") or None
        sub = sub_by_id.get(str(sub_id or "")) if sub_id else None
        sub_name = (sub or {}).get("subcategory_name") or ""

        if body.get("markets") and body.get("selections"):
            chunk = _parse_modern_markets_payload(
                body,
                subcat_map=subcat_map,
                payload_sub_id=sub_id,
                subcategory_name=sub_name,
            )
        else:
            chunk = []
            fallback_key = (sub or {}).get("market_key") or subcat_map.get(str(sub_id or ""), "team_total_yds")
            for offer in _extract_all_offers(body):
                chunk.extend(_offers_to_rows([offer], market_key=str(fallback_key), subcategory_name=sub_name))
            chunk = [r for r in chunk if _line_plausible_for_market(str(r.get("market_key") or ""), r.get("line"))]

        for r in chunk:
            key = (
                r.get("event_id"),
                r.get("market_key"),
                r.get("description"),
                r.get("selection"),
                r.get("line"),
            )
            if key in seen_row:
                continue
            seen_row.add(key)
            rows.append(r)

    if not rows:
        raise RuntimeError(
            "DraftKings website loaded but no team yard offer rows were parsed. "
            f"Captured {scrape.get('captured_count', 0)} HTTP responses."
        )

    _LAST_FETCH_MODE = "draftkings.com/browser"
    _LAST_FETCH_ERROR = None
    return rows, events, subs


def match_event(events: list[dict[str, Any]], home: str, away: str) -> dict[str, Any] | None:
    for ev in events:
        if teams_match(ev.get("home"), home) and teams_match(ev.get("away"), away):
            return ev
    return None


def canonical_team(name: str) -> str:
    return resolve_canonical(name) or name
