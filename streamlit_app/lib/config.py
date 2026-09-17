"""App configuration — US-legal sportsbooks + Pinnacle only."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
PUBLIC_DIR = ROOT / "public"
SNAPSHOT_DIR = DATA_DIR / "streamlit_odds_snapshots"
EXCEL_EXPORT_DIR = DATA_DIR / "streamlit_exports"
CSV_LOG_DIR = DATA_DIR / "streamlit_csv_log"

# US-regulated retail books + Pinnacle (sharp reference)
US_LEGAL_BOOKS: frozenset[str] = frozenset(
    {
        "draftkings",
        "fanduel",
        "betmgm",
        "caesars",
        "betrivers",
        "thescore",
        "fanatics",
        "pinnacle",
        "williamhill_us",
        "superbook",
    }
)

BOOK_LABELS = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "caesars": "Caesars",
    "betrivers": "BetRivers",
    "thescore": "theScore",
    "fanatics": "Fanatics",
    "pinnacle": "Pinnacle",
    "hardrock": "Hard Rock",
    "bovada": "Bovada",
    "betonline": "BetOnline",
    "lowvig": "LowVig",
    "novig": "Novig",
    "kalshi": "Kalshi",
    "polymarket": "Polymarket",
    "polymarketus": "Polymarket US",
    "predictfun": "PredictFun",
    "prophetx": "ProphetX",
    "4c": "4C Odds",
    "4codds": "4C Odds",
    "betfair": "Betfair",
    "matchbook": "Matchbook",
    "3et": "3ET",
    "vertex": "Vertex",
    "apex": "Apex",
    "amapola": "Amapola",
    "playersfantasy": "Players Fantasy",
    "circa": "Circa",
    "bet365": "bet365",
    "williamhill_us": "William Hill",
    "superbook": "SuperBook",
    "kambi": "Kambi",
    "fliff": "Fliff",
    "prizepicks": "PrizePicks",
    "underdog": "Underdog",
    "underdogfantasy": "Underdog",
    "sleeper": "Sleeper",
}

ONYX_API = "https://api.onyxodds.com/api/public/odds"
ODDS_API_V4 = "https://api.the-odds-api.com/v4"
SPORT_KEY = "americanfootball_ncaaf"
NODE_API = os.getenv("FOOTBALL_LABS_API", "http://localhost:3000")

DEFAULT_YEAR = int(os.getenv("CFB_SEASON", "2026"))
DEFAULT_WEEK = int(os.getenv("CFB_WEEK", "1"))
MIN_WEEK = 0
MAX_WEEK = 16
WEEK_OPTIONS = list(range(MIN_WEEK, MAX_WEEK + 1))


def load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def odds_api_key() -> str | None:
    load_env()
    return (
        os.getenv("THE_ODDS_API_KEY")
        or os.getenv("ODDS_API_KEY")
        or os.getenv("THE_ODDS_API_KEY_V4")
    )


def otter_odds_api_key() -> str | None:
    """Bearer token for api.otterodds.com — API key from dashboard or session JWT while logged in."""
    load_env()
    for name in (
        "OTTER_ODDS_API_KEY",
        "OTTER_API_KEY",
        "OTTER_ODDS_SESSION_TOKEN",
        "OTTER_SESSION_TOKEN",
    ):
        val = (os.getenv(name) or "").strip()
        if val:
            return val
    return None


def audit_export_enabled() -> bool:
    """Excel pull logging — off by default (very slow on every page load)."""
    load_env()
    return os.getenv("STREAMLIT_AUDIT_EXPORT", "0").strip().lower() in ("1", "true", "yes")


def csv_log_enabled() -> bool:
    """Append-only CSV feed for odds + projections — on by default."""
    load_env()
    return os.getenv("STREAMLIT_CSV_LOG", "1").strip().lower() in ("1", "true", "yes")


def _book_aliases() -> dict[str, str]:
    return {
        "espn": "thescore",
        "espnbet": "thescore",
        "espnbets": "thescore",
        "thescorebet": "thescore",
    }


def normalize_book_id(raw: str) -> str:
    key = (
        str(raw or "")
        .lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )
    return _book_aliases().get(key, key)


def book_allowed(raw: str) -> bool:
    return normalize_book_id(raw) in US_LEGAL_BOOKS


def book_label(raw: str) -> str:
    key = normalize_book_id(raw)
    return BOOK_LABELS.get(key, str(raw or "Book").title())
