"""Auto-refresh Bill Connelly SP+ ratings for live projections."""

from __future__ import annotations



import json

import os

import subprocess

import sys

from datetime import datetime, timezone

from pathlib import Path

from typing import Any



from .config import DATA_DIR, DEFAULT_WEEK, DEFAULT_YEAR, ROOT

# Bill Connelly 2026 SP+ — primary source for all CFB modeling / projections.
SP_2026_SHEET_ID = "1vwoVl-Dxy0es87Z9I1RTvFzr72Lb1fAkREfbLxbK-eg"
SP_2026_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SP_2026_SHEET_ID}/htmlview"

SP_INDEX_PATH = DATA_DIR / "puntandrally" / "sp_2026_index.json"
LIVE_SHEET_STATE_PATH = DATA_DIR / "puntandrally" / "sp_live_sheet_state.json"
CHECK_SHEET_SCRIPT = ROOT / "scripts" / "check_sp_sheet_state.js"

REFRESH_SCRIPT = ROOT / "scripts" / "refresh_sp_latest.js"

REFRESH_STATE_PATH = DATA_DIR / "puntandrally" / "sp_refresh_state.json"



# Re-pull from Bill Connelly's sheet often during the season (default 15 min).

SP_REFRESH_TTL_SEC = int(os.getenv("SP_REFRESH_TTL_SEC", "900"))





def sp_index_path(season: int, display_week: int) -> Path:

    """Per-display-week SP+ cache — display week W uses sheet tab FBS Week (W-1)."""

    return DATA_DIR / "puntandrally" / f"sp_{int(season)}_w{int(display_week)}.json"





def _utc_now() -> datetime:

    return datetime.now(timezone.utc)





def _parse_ts(raw: Any) -> datetime | None:

    if not raw:

        return None

    try:

        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))

    except (TypeError, ValueError):

        return None





def _read_json_meta(path: Path) -> dict[str, Any]:

    if not path.exists():

        return {}

    try:

        return json.loads(path.read_text(encoding="utf-8"))

    except (json.JSONDecodeError, OSError):

        return {}





def _read_index_meta(*, season: int, display_week: int) -> dict[str, Any]:

    """Load SP+ metadata for a specific display week."""

    path = sp_index_path(season, display_week)

    meta = _read_json_meta(path)

    if meta.get("teams"):

        return meta



    legacy = _read_json_meta(SP_INDEX_PATH)

    if legacy.get("teams"):

        if int(legacy.get("season") or 0) == int(season) and int(legacy.get("display_week") or -1) == int(

            display_week

        ):

            return legacy

    return {}





def _target_source_week(display_week: int) -> int:

    return max(0, int(display_week) - 1)


def _fbs_tab_name_for_display_week(display_week: int) -> str:
    return f"FBS Week {_target_source_week(display_week)}"


def _poll_live_sheet_state(*, max_age_sec: int = 300) -> dict[str, Any]:
    """Refresh live tab/content hashes from the Google Sheet (cached briefly)."""
    cached = _read_json_meta(LIVE_SHEET_STATE_PATH)
    if cached.get("content_by_tab"):
        checked = _parse_ts(cached.get("checked_at"))
        if checked and (_utc_now() - checked).total_seconds() < max_age_sec:
            return cached

    if not CHECK_SHEET_SCRIPT.exists():
        return cached

    try:
        proc = subprocess.run(
            ["node", str(CHECK_SHEET_SCRIPT)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if proc.returncode == 0 and LIVE_SHEET_STATE_PATH.exists():
            return _read_json_meta(LIVE_SHEET_STATE_PATH)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return cached


def _live_content_signature(display_week: int) -> str | None:
    live = _poll_live_sheet_state()
    tab = _fbs_tab_name_for_display_week(display_week)
    sig = (live.get("content_by_tab") or {}).get(tab)
    return str(sig) if sig else None





def sp_index_stale(*, season: int, display_week: int) -> bool:

    """True when cached SP+ is missing, old, or for the wrong week."""

    meta = _read_index_meta(season=season, display_week=display_week)

    if not meta.get("teams"):

        return True



    if int(meta.get("season") or 0) != int(season):

        return True

    if int(season) == 2026:
        sid = str(meta.get("spreadsheet_id") or "")
        if sid and sid != SP_2026_SHEET_ID:
            return True

        live = _poll_live_sheet_state()
        live_sig = str(live.get("sheet_signature") or "")
        cached_sig = str(meta.get("sheet_signature") or "")
        if live_sig and cached_sig and live_sig != cached_sig:
            return True

        live_content = _live_content_signature(display_week)
        cached_content = str(meta.get("content_signature") or "")
        if live_content and cached_content and live_content != cached_content:
            return True

    target_week = _target_source_week(display_week)

    cached_week = meta.get("sourceWeek")

    if cached_week is not None and int(cached_week) < target_week:

        return True



    if meta.get("display_week") is not None and int(meta.get("display_week")) != int(display_week):

        return True



    generated = _parse_ts(meta.get("generated_at"))

    if generated is None:

        return True



    age_sec = (_utc_now() - generated).total_seconds()

    return age_sec >= SP_REFRESH_TTL_SEC





def _write_refresh_state(payload: dict[str, Any]) -> None:

    REFRESH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    REFRESH_STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")





def refresh_sp_index(*, season: int, display_week: int, force: bool = False) -> bool:

    """Run Node refresh script. Returns True when a new index was written."""

    if not force and not sp_index_stale(season=season, display_week=display_week):

        return False



    if not REFRESH_SCRIPT.exists():

        return False



    cmd = ["node", str(REFRESH_SCRIPT), "--season", str(season), "--week", str(display_week)]

    try:

        proc = subprocess.run(

            cmd,

            cwd=str(ROOT),

            capture_output=True,

            text=True,

            timeout=120,

            check=False,

        )

    except (OSError, subprocess.TimeoutExpired) as err:

        _write_refresh_state(

            {

                "ok": False,

                "error": str(err),

                "at": _utc_now().isoformat(),

                "season": season,

                "display_week": display_week,

            }

        )

        return False



    week_path = sp_index_path(season, display_week)

    ok = proc.returncode == 0 and week_path.exists()

    _write_refresh_state(

        {

            "ok": ok,

            "returncode": proc.returncode,

            "stdout": (proc.stdout or "").strip()[-500:],

            "stderr": (proc.stderr or "").strip()[-500:],

            "at": _utc_now().isoformat(),

            "season": season,

            "display_week": display_week,

        }

    )

    return ok





def ensure_fresh_sp_ratings(

    season: int | None = None,

    display_week: int | None = None,

    *,

    force: bool = False,

) -> None:

    """Refresh SP+ index when stale, then bust Python caches."""

    yr = int(season if season is not None else DEFAULT_YEAR)

    wk = int(display_week if display_week is not None else DEFAULT_WEEK)



    if refresh_sp_index(season=yr, display_week=wk, force=force):

        from . import sp_projections

        from .sp_prop_matchup import clear_sp_matchup_cache



        sp_projections.clear_sp_cache()

        clear_sp_matchup_cache()





def sp_index_summary(*, season: int | None = None, display_week: int | None = None) -> dict[str, Any]:

    yr = int(season if season is not None else DEFAULT_YEAR)

    wk = int(display_week if display_week is not None else DEFAULT_WEEK)

    meta = _read_index_meta(season=yr, display_week=wk)

    generated = _parse_ts(meta.get("generated_at"))

    age_min = None

    if generated:

        age_min = round((_utc_now() - generated).total_seconds() / 60, 1)

    return {

        "generated_at": meta.get("generated_at"),

        "age_minutes": age_min,

        "season": meta.get("season"),

        "display_week": meta.get("display_week"),

        "sourceWeek": meta.get("sourceWeek"),

        "source": meta.get("source"),

        "spreadsheet_id": meta.get("spreadsheet_id") or SP_2026_SHEET_ID,

        "spreadsheet_url": SP_2026_SHEET_URL,

        "source_tab": meta.get("source_tab"),

        "sheet_signature": meta.get("sheet_signature"),

        "team_count": meta.get("team_count") or len(meta.get("teams") or {}),

    }


