"""TV network logos for game cards — ESPN CDN + brand favicon fallbacks."""
from __future__ import annotations

import html
import re
from urllib.parse import quote

# ESPN CDN network logos (from geoBroadcasts when present).
_ESPN_NETWORK_LOGOS: dict[str, str] = {
    "espn": "https://a.espncdn.com/guid/335fd2d2-97b9-336b-81ee-573eb6bdcffc/logos/default.png",
    "espn2": "https://a.espncdn.com/guid/335fd2d2-97b9-336b-81ee-573eb6bdcffc/logos/default.png",
    "espnu": "https://a.espncdn.com/guid/335fd2d2-97b9-336b-81ee-573eb6bdcffc/logos/default.png",
    "espnews": "https://a.espncdn.com/guid/335fd2d2-97b9-336b-81ee-573eb6bdcffc/logos/default.png",
    "acc network": "https://a.espncdn.com/guid/8936b3f6-e2cb-3924-807b-00f236018174/logos/default.png",
    "accn": "https://a.espncdn.com/guid/8936b3f6-e2cb-3924-807b-00f236018174/logos/default.png",
    "sec network": "https://a.espncdn.com/guid/8936b3f6-e2cb-3924-807b-00f236018174/logos/default.png",
    "secn": "https://a.espncdn.com/guid/8936b3f6-e2cb-3924-807b-00f236018174/logos/default.png",
}

# Brand favicons for networks ESPN often omits logos for.
_NETWORK_DOMAINS: dict[str, str] = {
    "fox": "fox.com",
    "fs1": "foxsports.com",
    "fs2": "foxsports.com",
    "btn": "btn.com",
    "big ten network": "btn.com",
    "cbs": "cbs.com",
    "cbssn": "cbssports.com",
    "cbs sports network": "cbssports.com",
    "nbc": "nbc.com",
    "abc": "abc.com",
    "tnt": "tntdrama.com",
    "tbs": "tbs.com",
    "peacock": "peacocktv.com",
    "prime video": "amazon.com",
    "amazon": "amazon.com",
    "nfl network": "nfl.com",
    "pac-12 network": "pac-12.com",
    "the cw": "cwtv.com",
}

_NETWORK_COLORS: dict[str, tuple[str, str, str]] = {
    "fox": ("FOX", "#003366", "#fff"),
    "fs1": ("FS1", "#00539B", "#fff"),
    "fs2": ("FS2", "#00539B", "#fff"),
    "btn": ("BTN", "#003366", "#fff"),
    "cbs": ("CBS", "#005DAA", "#fff"),
    "cbssn": ("CBSSN", "#005DAA", "#fff"),
    "nbc": ("NBC", "#111", "#fff"),
    "abc": ("ABC", "#111", "#fff"),
    "espn": ("ESPN", "#c41230", "#fff"),
    "espn2": ("ESPN2", "#c41230", "#fff"),
    "espnu": ("ESPNU", "#c41230", "#fff"),
    "acc network": ("ACC", "#013ca6", "#fff"),
    "accn": ("ACC", "#013ca6", "#fff"),
    "sec network": ("SEC", "#003087", "#fff"),
    "secn": ("SEC", "#003087", "#fff"),
}


def _norm_network(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _favicon_url(domain: str, *, size: int = 64) -> str:
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


def broadcast_logo_url(network: str, espn_logo: str = "") -> str:
    """Resolve a display logo for a TV network name."""
    if espn_logo and str(espn_logo).startswith("http"):
        return str(espn_logo)

    key = _norm_network(network)
    if not key:
        return ""

    if key in _ESPN_NETWORK_LOGOS:
        return _ESPN_NETWORK_LOGOS[key]

    domain = _NETWORK_DOMAINS.get(key)
    if domain:
        return _favicon_url(domain)

    if key in _NETWORK_COLORS:
        label, bg, fg = _NETWORK_COLORS[key]
        return _svg_data_url(label, bg, fg)

    short = network.strip()[:4].upper() if network else "TV"
    return _svg_data_url(short, "#334155", "#fff")


def broadcast_logo_img(network: str, espn_logo: str = "", *, size: int = 18, cls: str = "bo-mg-tv-logo") -> str:
    url = broadcast_logo_url(network, espn_logo)
    name = html.escape(str(network or "TV"))
    if not url:
        return f'<span class="bo-mg-tv-text">{name}</span>'
    return (
        f'<img class="{cls}" src="{html.escape(url)}" alt="{name}" '
        f'width="{size}" height="{size}" loading="lazy" />'
    )
