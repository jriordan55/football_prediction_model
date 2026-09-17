"""Sportsbook & prediction-market logos — 4C Odds books + US retail."""
from __future__ import annotations

import html
from urllib.parse import quote

from .config import book_label, normalize_book_id

# domain → Google favicon CDN; abbr/bg/fg → SVG fallback when favicon fails.
_BOOK_REGISTRY: dict[str, dict[str, str]] = {
    # US retail
    "draftkings": {"domain": "draftkings.com", "abbr": "DK", "bg": "#059669", "fg": "#ffffff"},
    "fanduel": {"domain": "fanduel.com", "abbr": "FD", "bg": "#2563eb", "fg": "#ffffff"},
    "betmgm": {"domain": "betmgm.com", "abbr": "MGM", "bg": "#ca8a04", "fg": "#111827"},
    "caesars": {"domain": "caesars.com", "abbr": "CZ", "bg": "#dc2626", "fg": "#ffffff"},
    "betrivers": {"domain": "betrivers.com", "abbr": "BR", "bg": "#0ea5e9", "fg": "#ffffff"},
    "thescore": {"domain": "thescore.com", "abbr": "TS", "bg": "#0066cc", "fg": "#ffffff"},
    "fanatics": {"domain": "fanatics.com", "abbr": "FN", "bg": "#1d4ed8", "fg": "#ffffff"},
    "hardrock": {"domain": "hardrock.bet", "abbr": "HR", "bg": "#111827", "fg": "#fbbf24"},
    "williamhill_us": {"domain": "williamhill.com", "abbr": "WH", "bg": "#0f766e", "fg": "#ffffff"},
    "superbook": {"domain": "superbook.com", "abbr": "SB", "bg": "#7c3aed", "fg": "#ffffff"},
    "circa": {"domain": "circasports.com", "abbr": "CIR", "bg": "#111827", "fg": "#fbbf24"},
    "bet365": {"domain": "bet365.com", "abbr": "365", "bg": "#16a34a", "fg": "#ffffff"},
    "bovada": {"domain": "bovada.lv", "abbr": "BV", "bg": "#dc2626", "fg": "#ffffff"},
    "betonline": {"domain": "betonline.ag", "abbr": "BO", "bg": "#b45309", "fg": "#ffffff"},
    # Sharp / exchanges
    "pinnacle": {"domain": "pinnacle.com", "abbr": "PIN", "bg": "#f97316", "fg": "#ffffff"},
    "betfair": {"domain": "betfair.com", "abbr": "BF", "bg": "#fbbf24", "fg": "#111827"},
    "matchbook": {"domain": "matchbook.com", "abbr": "MB", "bg": "#ef4444", "fg": "#ffffff"},
    "lowvig": {"domain": "lowvig.ag", "abbr": "LV", "bg": "#64748b", "fg": "#ffffff"},
    "3et": {"domain": "3et.com", "abbr": "3ET", "bg": "#0f172a", "fg": "#38bdf8"},
    "vertex": {"domain": "vertexbets.com", "abbr": "VX", "bg": "#6366f1", "fg": "#ffffff"},
    "apex": {"domain": "apex.exchange", "abbr": "APX", "bg": "#14b8a6", "fg": "#ffffff"},
    "amapola": {"domain": "amapola.co", "abbr": "AM", "bg": "#e11d48", "fg": "#ffffff"},
    # Prediction markets
    "novig": {"domain": "novig.us", "abbr": "NV", "bg": "#8b5cf6", "fg": "#ffffff"},
    "kalshi": {"domain": "kalshi.com", "abbr": "KL", "bg": "#22c55e", "fg": "#ffffff"},
    "polymarket": {"domain": "polymarket.com", "abbr": "PM", "bg": "#2563eb", "fg": "#ffffff"},
    "polymarketus": {"domain": "polymarket.com", "abbr": "PM", "bg": "#2563eb", "fg": "#ffffff"},
    "predictfun": {"domain": "predict.fun", "abbr": "PF", "bg": "#a855f7", "fg": "#ffffff"},
    "prophetx": {"domain": "prophetx.co", "abbr": "PX", "bg": "#0891b2", "fg": "#ffffff"},
    "4c": {"domain": "4codds.com", "abbr": "4C", "bg": "#2563eb", "fg": "#ffffff"},
    "4codds": {"domain": "4codds.com", "abbr": "4C", "bg": "#2563eb", "fg": "#ffffff"},
    # Fantasy / alt
    "playersfantasy": {"domain": "underdogfantasy.com", "abbr": "PF", "bg": "#f59e0b", "fg": "#111827"},
    "underdog": {"domain": "underdogfantasy.com", "abbr": "UD", "bg": "#eab308", "fg": "#111827"},
    "underdogfantasy": {"domain": "underdogfantasy.com", "abbr": "UD", "bg": "#eab308", "fg": "#111827"},
    "prizepicks": {"domain": "prizepicks.com", "abbr": "PP", "bg": "#7c3aed", "fg": "#ffffff"},
    "fliff": {"domain": "getfliff.com", "abbr": "FL", "bg": "#14b8a6", "fg": "#ffffff"},
    "sleeper": {"domain": "sleeper.com", "abbr": "SL", "bg": "#1e293b", "fg": "#ffffff"},
    "kambi": {"domain": "kambi.com", "abbr": "KB", "bg": "#0284c7", "fg": "#ffffff"},
}

# Legacy alias for any external imports
_BOOK_DOMAINS: dict[str, str] = {k: v["domain"] for k, v in _BOOK_REGISTRY.items()}
_BOOK_SVG_FALLBACK: dict[str, tuple[str, str, str]] = {
    k: (v["abbr"], v["bg"], v["fg"]) for k, v in _BOOK_REGISTRY.items()
}


def _favicon_url(domain: str, *, size: int = 128) -> str:
    site = quote(f"https://www.{domain}", safe="")
    return (
        "https://t1.gstatic.com/faviconV2"
        f"?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url={site}&size={size}"
    )


def _svg_data_url(label: str, bg: str, fg: str, *, size: int = 22) -> str:
    fs = 7 if len(label) > 3 else 9
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<rect width="{size}" height="{size}" rx="4" fill="{bg}"/>'
        f'<text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" '
        f'fill="{fg}" font-family="Arial,sans-serif" font-size="{fs}" font-weight="700">{label}</text></svg>'
    )
    return f"data:image/svg+xml,{quote(svg)}"


def _registry_entry(book_id: str) -> dict[str, str] | None:
    bid = normalize_book_id(book_id)
    return _BOOK_REGISTRY.get(bid)


def book_logo_url(book_id: str) -> str:
    bid = normalize_book_id(book_id)
    entry = _registry_entry(bid)
    if entry and entry.get("domain"):
        return _favicon_url(entry["domain"])
    if bid in _BOOK_SVG_FALLBACK:
        label, bg, fg = _BOOK_SVG_FALLBACK[bid]
        return _svg_data_url(label, bg, fg)
    short = (book_label(bid) or bid or "?")[:3].upper()
    return _svg_data_url(short, "#475569", "#ffffff")


def book_logo_fallback_url(book_id: str, *, size: int = 22) -> str:
    bid = normalize_book_id(book_id)
    entry = _registry_entry(bid)
    if entry:
        return _svg_data_url(entry["abbr"], entry["bg"], entry["fg"], size=size)
    if bid in _BOOK_SVG_FALLBACK:
        label, bg, fg = _BOOK_SVG_FALLBACK[bid]
        return _svg_data_url(label, bg, fg, size=size)
    short = (book_label(bid) or bid or "?")[:3].upper()
    return _svg_data_url(short, "#475569", "#ffffff", size=size)


def book_logo_img(book_id: str, *, size: int = 20, cls: str = "bo-sl-book-logo") -> str:
    bid = normalize_book_id(book_id)
    url = book_logo_url(bid)
    fallback = book_logo_fallback_url(bid, size=size)
    title = html.escape(book_label(bid))
    return (
        f'<img class="{cls}" src="{url}" alt="{title}" title="{title}" '
        f'width="{size}" height="{size}" loading="lazy" '
        f'onerror="this.onerror=null;this.src=\'{fallback}\'"/>'
    )


def book_header_html(book_id: str, label: str | None = None) -> str:
    bid = normalize_book_id(book_id)
    lbl = html.escape(label or book_label(bid))
    return f'<div class="bo-sl-book-hdr">{book_logo_img(bid)}{lbl}</div>'
