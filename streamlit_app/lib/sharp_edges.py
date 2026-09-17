"""Pinnacle vs soft-book spread/total edges (mirrors puntandrally/pinnacle_edges.js)."""

from __future__ import annotations



import math

import re

from typing import Any



import pandas as pd



from .odds_math import american_to_implied, devig_two_way

from .odds_client import market_label

from .team_registry import match_selection_to_side, resolve_canonical, teams_match, teams_match_strict



PINNACLE = "pinnacle"

SOFT_BOOKS = [

    "draftkings",

    "fanduel",

    "betmgm",

    "caesars",

    "betrivers",

    "thescore",

    "fanatics",

    "bovada",

]

SPREAD_KEYS = {"spreads", "spread", "point_spread"}

TOTAL_KEYS = {"totals", "total", "total_points"}





def _american_num(price: Any) -> float | None:

    if price is None:

        return None

    s = str(price).replace("−", "-").replace("+", "").strip()

    try:

        return float(s)

    except (TypeError, ValueError):

        return None





def _fmt_spread(line: float | None) -> str:

    if line is None or not math.isfinite(line):

        return "—"

    if line > 0:

        return f"+{line:g}"

    return f"{line:g}"





def _better_american(a: Any, b: Any) -> Any:

    an, bn = _american_num(a), _american_num(b)

    if an is None:

        return b

    if bn is None:

        return a

    if an >= 0 and bn >= 0:

        return a if an > bn else b

    if an < 0 and bn < 0:

        return a if an > bn else b

    return a if an > bn else b





def _is_h2h_key(mk: str) -> bool:

    mk = str(mk or "").lower()

    return mk == "h2h" or mk.startswith("h2h_") or mk in {"moneyline", "h2h_lay"}





def _is_spread_key(mk: str) -> bool:

    mk = str(mk or "").lower()

    if _is_h2h_key(mk) or "team" in mk or "alternate" in mk:

        return False

    return mk in SPREAD_KEYS or bool(re.match(r"^spreads(_|$)", mk))





def _is_total_key(mk: str) -> bool:

    mk = str(mk or "").lower()

    if _is_h2h_key(mk) or "team" in mk or "alternate" in mk:

        return False

    return mk in TOTAL_KEYS or bool(re.match(r"^totals(_|$)", mk))





def _is_team_total_key(mk: str) -> bool:

    mk = str(mk or "").lower()

    return "team_total" in mk and "alternate" not in mk





def _book_side_rows(book_df: pd.DataFrame, home: str, away: str, market_key: str) -> dict[str, dict[str, Any]]:

    mk = str(market_key or "").lower()

    out: dict[str, dict[str, Any]] = {}

    if _is_spread_key(mk):
        side_map: dict[str, dict[str, Any]] = {}
        for _, row in book_df.iterrows():
            sel = str(row.get("selection") or "")
            slot = match_selection_to_side(sel, home, away)
            if not slot or slot in side_map:
                continue
            side_map[slot] = {
                "line": row.get("line"),
                "price": row.get("price"),
                "selection": row.get("selection"),
            }
        out.update(side_map)

    elif _is_total_key(mk):

        over = book_df[book_df["selection"].astype(str).str.lower().str.contains("over", na=False)]

        under = book_df[book_df["selection"].astype(str).str.lower().str.contains("under", na=False)]

        if not over.empty:

            row = over.iloc[0]

            out["over"] = {"line": row.get("line"), "price": row.get("price"), "selection": row.get("selection")}

        if not under.empty:

            row = under.iloc[0]

            out["under"] = {"line": row.get("line"), "price": row.get("price"), "selection": row.get("selection")}

    return out





def _book_team_total_rows(book_df: pd.DataFrame, home: str, away: str) -> dict[str, dict[str, dict[str, Any]]]:

    """Team totals: Over/Under keyed by team (description field)."""

    out: dict[str, dict[str, dict[str, Any]]] = {}

    if book_df.empty:

        return out



    desc_col = book_df["description"] if "description" in book_df.columns else pd.Series([""] * len(book_df))

    for team in (home, away):

        team_mask = desc_col.astype(str).apply(lambda s: teams_match_strict(s, team)) | book_df[
            "selection"
        ].astype(str).apply(lambda s: teams_match_strict(s, team))

        team_df = book_df.loc[team_mask]

        if team_df.empty:

            continue

        over = team_df[team_df["selection"].astype(str).str.lower().str.contains("over", na=False)]

        under = team_df[team_df["selection"].astype(str).str.lower().str.contains("under", na=False)]

        sides: dict[str, dict[str, Any]] = {}

        if not over.empty:

            row = over.iloc[0]

            sides["over"] = {"line": row.get("line"), "price": row.get("price"), "selection": row.get("selection")}

        if not under.empty:

            row = under.iloc[0]

            sides["under"] = {"line": row.get("line"), "price": row.get("price"), "selection": row.get("selection")}

        if sides:

            out[team] = sides

    return out





def _append_spread_edges(

    rows: list[dict[str, Any]],

    *,

    event_id: Any,

    event: str,

    home: str,

    away: str,

    market_key: str,

    pin: dict[str, dict[str, Any]],

    soft_df: pd.DataFrame,

    market_name: str,

) -> None:

    if not pin.get("home") or not pin.get("away"):

        return



    pin_home_fair = devig_two_way(pin["home"]["price"], pin["away"]["price"])

    sides = [

        {

            "side": "home",

            "team": home,

            "label": f"{home} {_fmt_spread(float(pin['home']['line'])) if pin['home']['line'] is not None else ''}".strip(),

            "pin_line": pin["home"]["line"],

            "pin_price": pin["home"]["price"],

            "pin_fair": pin_home_fair,

        },

        {

            "side": "away",

            "team": away,

            "label": f"{away} {_fmt_spread(float(pin['away']['line'])) if pin['away']['line'] is not None else ''}".strip(),

            "pin_line": pin["away"]["line"],

            "pin_price": pin["away"]["price"],

            "pin_fair": 1 - pin_home_fair if pin_home_fair is not None else None,

        },

    ]

    for side in sides:

        best_soft = None

        best_book = None

        best_line = None

        for book_id, bdf in soft_df.groupby("book_id"):

            soft = _book_side_rows(bdf, str(home), str(away), market_key)

            sp = soft.get(side["side"])

            if not sp or sp.get("price") is None:

                continue

            try:

                pin_line = float(side["pin_line"])

                soft_line = float(sp["line"])

            except (TypeError, ValueError):

                continue

            line_diff = pin_line - soft_line if side["side"] == "home" else soft_line - pin_line

            price_better = _better_american(sp["price"], best_soft or sp["price"]) == sp["price"]

            if best_soft is None or line_diff > 0 or (abs(line_diff) < 1e-6 and price_better):

                best_soft = sp["price"]

                best_line = soft_line

                best_book = str(bdf["book"].iloc[0] if "book" in bdf.columns else book_id)



        if best_soft is None or best_line is None:

            continue

        try:

            edge_pts = round(abs(float(best_line) - float(side["pin_line"])) * 10) / 10

        except (TypeError, ValueError):

            edge_pts = None

        soft_imp = american_to_implied(best_soft)

        pin_imp = side["pin_fair"] if side["pin_fair"] is not None else american_to_implied(side["pin_price"])

        edge_pct = None

        if pin_imp is not None and soft_imp is not None:

            edge_pct = round((pin_imp - soft_imp) * 1000) / 10



        rows.append(

            {

                "event_id": event_id,

                "event": event,

                "home": home,

                "away": away,

                "market_key": market_key,

                "market": market_name,

                "side": side["label"],

                "pick": side["team"],

                "pin_line": side["pin_line"],

                "pin_price": side["pin_price"],

                "book_line": best_line,

                "book_price": best_soft,

                "book": best_book,

                "edge_points": edge_pts,

                "edge_pct": edge_pct,

            }

        )





def _append_total_edges(

    rows: list[dict[str, Any]],

    *,

    event_id: Any,

    event: str,

    home: str,

    away: str,

    market_key: str,

    pin: dict[str, dict[str, Any]],

    soft_df: pd.DataFrame,

    market_name: str,

    side_prefix: str = "",

) -> None:

    if not pin.get("over") or not pin.get("under"):

        return



    pin_over_fair = devig_two_way(pin["over"]["price"], pin["under"]["price"])

    for side_key, fair in (("over", pin_over_fair), ("under", 1 - pin_over_fair if pin_over_fair else None)):

        pin_row = pin[side_key]

        best_soft = None

        best_book = None

        best_line = None

        for book_id, bdf in soft_df.groupby("book_id"):

            soft = _book_side_rows(bdf, str(home), str(away), market_key)

            sp = soft.get(side_key)

            if not sp or sp.get("price") is None:

                continue

            try:

                pin_line = float(pin_row["line"])

                soft_line = float(sp["line"])

            except (TypeError, ValueError):

                continue

            line_diff = soft_line - pin_line if side_key == "over" else pin_line - soft_line

            price_better = _better_american(sp["price"], best_soft or sp["price"]) == sp["price"]

            if best_soft is None or line_diff > 0 or (abs(line_diff) < 1e-6 and price_better):

                best_soft = sp["price"]

                best_line = soft_line

                best_book = str(bdf["book"].iloc[0] if "book" in bdf.columns else book_id)



        if best_soft is None or best_line is None:

            continue

        try:

            edge_pts = round(abs(float(best_line) - float(pin_row["line"])) * 10) / 10

        except (TypeError, ValueError):

            edge_pts = None

        soft_imp = american_to_implied(best_soft)

        pin_imp = fair if fair is not None else american_to_implied(pin_row["price"])

        edge_pct = None

        if pin_imp is not None and soft_imp is not None:

            edge_pct = round((pin_imp - soft_imp) * 1000) / 10



        side_label = "Over" if side_key == "over" else "Under"

        label = f"{side_prefix}{side_label} {pin_row['line']}".strip()

        rows.append(

            {

                "event_id": event_id,

                "event": event,

                "home": home,

                "away": away,

                "market_key": market_key,

                "market": market_name,

                "side": label,

                "pick": side_label.upper(),

                "pin_line": pin_row["line"],

                "pin_price": pin_row["price"],

                "book_line": best_line,

                "book_price": best_soft,

                "book": best_book,

                "edge_points": edge_pts,

                "edge_pct": edge_pct,

            }

        )





def _append_team_total_edges(

    rows: list[dict[str, Any]],

    *,

    event_id: Any,

    event: str,

    home: str,

    away: str,

    market_key: str,

    pin_teams: dict[str, dict[str, dict[str, Any]]],

    soft_df: pd.DataFrame,

    market_name: str,

) -> None:

    for team, pin in pin_teams.items():

        if not pin.get("over") or not pin.get("under"):

            continue

        pin_over_fair = devig_two_way(pin["over"]["price"], pin["under"]["price"])

        for side_key, fair in (("over", pin_over_fair), ("under", 1 - pin_over_fair if pin_over_fair else None)):

            pin_row = pin[side_key]

            best_soft = None

            best_book = None

            best_line = None

            for book_id, bdf in soft_df.groupby("book_id"):

                soft_teams = _book_team_total_rows(bdf, str(home), str(away))

                sp = soft_teams.get(team, {}).get(side_key)

                if not sp or sp.get("price") is None:

                    continue

                try:

                    pin_line = float(pin_row["line"])

                    soft_line = float(sp["line"])

                except (TypeError, ValueError):

                    continue

                line_diff = soft_line - pin_line if side_key == "over" else pin_line - soft_line

                price_better = _better_american(sp["price"], best_soft or sp["price"]) == sp["price"]

                if best_soft is None or line_diff > 0 or (abs(line_diff) < 1e-6 and price_better):

                    best_soft = sp["price"]

                    best_line = soft_line

                    best_book = str(bdf["book"].iloc[0] if "book" in bdf.columns else book_id)



            if best_soft is None or best_line is None:

                continue

            try:

                edge_pts = round(abs(float(best_line) - float(pin_row["line"])) * 10) / 10

            except (TypeError, ValueError):

                edge_pts = None

            soft_imp = american_to_implied(best_soft)

            pin_imp = fair if fair is not None else american_to_implied(pin_row["price"])

            edge_pct = None

            if pin_imp is not None and soft_imp is not None:

                edge_pct = round((pin_imp - soft_imp) * 1000) / 10



            side_label = "Over" if side_key == "over" else "Under"

            rows.append(

                {

                    "event_id": event_id,

                    "event": event,

                    "home": home,

                    "away": away,

                    "market_key": market_key,

                    "market": market_name,

                    "side": f"{team} {side_label} {pin_row['line']}",

                    "pick": side_label.upper(),

                    "pin_line": pin_row["line"],

                    "pin_price": pin_row["price"],

                    "book_line": best_line,

                    "book_price": best_soft,

                    "book": best_book,

                    "edge_points": edge_pts,

                    "edge_pct": edge_pct,

                }

            )





def compute_sharp_edges(df: pd.DataFrame) -> pd.DataFrame:

    """Spread, total, team total, and period-market edges — excludes moneyline."""

    if df.empty:

        return pd.DataFrame()



    rows: list[dict[str, Any]] = []

    grouped = df.groupby(["event_id", "event", "home", "away", "market_key"], dropna=False)



    for (event_id, event, home, away, market_key), evdf in grouped:

        mk = str(market_key or "").lower()

        if _is_h2h_key(mk):

            continue

        if not (_is_spread_key(mk) or _is_total_key(mk) or _is_team_total_key(mk)):

            continue



        pin_df = evdf[evdf["book_id"].astype(str).str.lower() == PINNACLE]

        if pin_df.empty:

            continue



        soft_df = evdf[evdf["book_id"].astype(str).str.lower().isin(SOFT_BOOKS)]

        if soft_df.empty:

            continue



        market_name = market_label(mk)



        if _is_spread_key(mk):

            pin = _book_side_rows(pin_df, str(home), str(away), mk)

            _append_spread_edges(

                rows,

                event_id=event_id,

                event=str(event),

                home=str(home),

                away=str(away),

                market_key=mk,

                pin=pin,

                soft_df=soft_df,

                market_name=market_name,

            )

        elif _is_total_key(mk):

            pin = _book_side_rows(pin_df, str(home), str(away), mk)

            _append_total_edges(

                rows,

                event_id=event_id,

                event=str(event),

                home=str(home),

                away=str(away),

                market_key=mk,

                pin=pin,

                soft_df=soft_df,

                market_name=market_name,

            )

        elif _is_team_total_key(mk):

            pin_teams = _book_team_total_rows(pin_df, str(home), str(away))

            _append_team_total_edges(

                rows,

                event_id=event_id,

                event=str(event),

                home=str(home),

                away=str(away),

                market_key=mk,

                pin_teams=pin_teams,

                soft_df=soft_df,

                market_name=market_name,

            )



    out = pd.DataFrame(rows)

    if out.empty:

        return out

    out["edge_sort"] = out["edge_points"].fillna(0)

    return out.sort_values("edge_sort", ascending=False).drop(columns=["edge_sort"])


