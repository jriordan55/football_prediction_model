"""Pull DraftKings CFB team-yard odds by loading the sportsbook site in a real browser."""
from __future__ import annotations

import re
import subprocess
import sys
from typing import Any

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_LEAGUE_BASE = "https://sportsbook.draftkings.com/leagues/football/ncaaf"
_TEAM_YARDS_HUB = f"{_LEAGUE_BASE}?category=games&subcategory=team-yards"

# Every tab on the DK team-yards page → internal market key
_NAV_TABS: tuple[tuple[str, str], ...] = (
    ("team-total-yards", "team_total_yds"),
    ("team-total-yards---1h", "team_total_yds_h1"),
    ("team-total-yards---q1", "team_total_yds_q1"),
    ("team-total-rushing-yards", "team_total_rush_yds"),
    ("team-total-rushing-yards---1h", "team_total_rush_yds_h1"),
    ("team-total-rushing-yards---q1", "team_total_rush_yds_q1"),
    ("team-total-passing-yards", "team_total_pass_yds"),
    ("team-total-passing-yards---1h", "team_total_pass_yds_h1"),
    ("team-total-passing-yards---q1", "team_total_pass_yds_q1"),
    ("team-total-receiving-yards", "team_total_rec_yds"),
    ("team-total-receiving-yards---1h", "team_total_rec_yds_h1"),
    ("team-total-receiving-yards---q1", "team_total_rec_yds_q1"),
)


def ensure_playwright() -> None:
    try:
        import playwright  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright", "-q"])


def _tab_url(nav_slug: str) -> str:
    return f"{_TEAM_YARDS_HUB}&nav_1={nav_slug}"


def _interesting_dk_url(url: str) -> bool:
    u = url.lower()
    if "draftkings.com" not in u:
        return False
    return any(
        token in u
        for token in (
            "eventgroups",
            "subcategories",
            "categories",
            "/offers/",
            "/markets/",
            "leaguesubcategory",
            "sportscontent",
            "offerCategory",
        )
    )


def _click_tab_if_present(page: Any, slug: str) -> None:
    """Click a team-yards sub-tab by slug or visible label."""
    slug_text = slug.replace("-", " ").replace("---", " ").replace("  ", " ")
    patterns = (
        slug.replace("-", " "),
        slug_text,
        slug.split("---")[-1].replace("-", " ") if "---" in slug else slug.replace("-", " "),
    )
    for pat in patterns:
        if not pat.strip():
            continue
        try:
            link = page.locator(f'a[href*="nav_1={slug}"]').first
            if link.count() > 0:
                link.click(timeout=4000)
                page.wait_for_timeout(900)
                return
        except Exception:
            pass
        try:
            tab = page.get_by_role("tab", name=re.compile(re.escape(pat), re.I))
            if tab.count() > 0:
                tab.first.click(timeout=4000)
                page.wait_for_timeout(900)
                return
        except Exception:
            pass


def _goto_dk(page: Any, url: str, *, timeout_ms: int, wait_ms: int = 2500) -> None:
    """DK SPAs never reach networkidle — load DOM then pause for XHR."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    page.wait_for_timeout(wait_ms)


def _run_scrape(*, headless: bool, timeout_ms: int) -> list[dict[str, Any]]:
    ensure_playwright()
    from playwright.sync_api import sync_playwright

    captured: list[dict[str, Any]] = []
    current_market_key: str | None = None

    def _on_response(response: Any) -> None:
        try:
            url = response.url or ""
            if response.status != 200 or not _interesting_dk_url(url):
                return
            ctype = (response.headers.get("content-type") or "").lower()
            if "json" not in ctype and "javascript" not in ctype and "text" not in ctype:
                return
            body = response.json()
            sub_id = None
            m = re.search(r"templateVars=87637(?:%2C|,)(\d+)", url)
            if m:
                sub_id = m.group(1)
            captured.append(
                {
                    "url": url,
                    "market_key": current_market_key,
                    "subcategory_id": sub_id,
                    "body": body,
                }
            )
        except Exception:
            return

    with sync_playwright() as pw:
        browser = None
        errors: list[str] = []
        for channel in (["msedge", "chrome", None] if sys.platform == "win32" else ["chrome", None]):
            try:
                if channel:
                    browser = pw.chromium.launch(channel=channel, headless=headless)
                else:
                    browser = pw.chromium.launch(headless=headless)
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{channel or 'chromium'}: {exc}")
        if browser is None:
            raise RuntimeError(
                "Could not launch Edge/Chrome for DraftKings. "
                f"Install Microsoft Edge or run: python -m playwright install chromium. "
                f"({' | '.join(errors)})"
            )
        try:
            context = browser.new_context(
                user_agent=_USER_AGENT,
                locale="en-US",
                viewport={"width": 1440, "height": 900},
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            page = context.new_page()
            page.on("response", _on_response)

            current_market_key = None
            try:
                with page.expect_response(
                    lambda r: r.status == 200 and "eventgroups/87637" in (r.url or ""),
                    timeout=min(timeout_ms, 45_000),
                ):
                    _goto_dk(page, _TEAM_YARDS_HUB, timeout_ms=timeout_ms, wait_ms=500)
            except Exception:
                _goto_dk(page, _TEAM_YARDS_HUB, timeout_ms=timeout_ms, wait_ms=4000)

            for slug, market_key in _NAV_TABS:
                current_market_key = market_key
                tab_url = _tab_url(slug)
                try:
                    with page.expect_response(
                        lambda r: r.status == 200
                        and "leaguesubcategory" in (r.url or "").lower()
                        and "templateVars=87637" in (r.url or ""),
                        timeout=25_000,
                    ):
                        _goto_dk(page, tab_url, timeout_ms=timeout_ms, wait_ms=500)
                except Exception:
                    _goto_dk(page, tab_url, timeout_ms=timeout_ms, wait_ms=2500)
                    _click_tab_if_present(page, slug)
                    page.wait_for_timeout(1500)
        finally:
            browser.close()
    return captured


def browser_scrape_team_yards(*, timeout_ms: int = 120_000) -> dict[str, Any]:
    """
    Open DraftKings team-yards pages in Edge/Chrome and capture the JSON the site loads.
    Returns {eventgroup, payloads: [{url, market_key, body}], captured_count}.
    """
    captured = _run_scrape(headless=True, timeout_ms=timeout_ms)
    if not captured:
        captured = _run_scrape(headless=False, timeout_ms=timeout_ms)

    eventgroup: dict[str, Any] | None = None
    payloads: list[dict[str, Any]] = []
    for item in captured:
        url = str(item.get("url") or "")
        body = item.get("body")
        if not isinstance(body, dict):
            continue
        if re.search(r"/eventgroups/87637(?:\?|$|/compact)", url) and "subcategories" not in url:
            eventgroup = body
            continue
        if any(
            token in url
            for token in ("subcategories", "categories", "/offers/", "/markets/", "leaguesubcategory")
        ):
            payloads.append(item)
        elif eventgroup is None and "eventGroup" in body:
            eventgroup = body
        elif "markets" in body and "selections" in body:
            payloads.append(item)

    if not eventgroup and not payloads:
        raise RuntimeError(
            f"DraftKings page loaded but captured 0 odds JSON ({len(captured)} responses). "
            "Geo/login block, bot detection, or page layout change."
        )

    return {
        "eventgroup": eventgroup,
        "payloads": payloads,
        "captured_count": len(captured),
    }