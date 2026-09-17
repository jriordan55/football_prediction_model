import logging
logging.disable(logging.CRITICAL)
import pandas as pd
from lib.sport_context import SPORT_CFB
from views.player_projections import _finalize_prop_board, _live_onyx_props_for_week

out = _finalize_prop_board(_live_onyx_props_for_week(2026, 2), sport=SPORT_CFB, year=2026, week=2)
out["line_f"] = pd.to_numeric(out["line"], errors="coerce")
out["mine_f"] = pd.to_numeric(out["modelProj"], errors="coerce")
out["vs_line"] = out["mine_f"] - out["line_f"]
print("ratio", (out["mine_f"] / out["line_f"]).mean())
print("median vs_line", out["vs_line"].median())
print("pct above line", (out["mine_f"] > out["line_f"]).mean())
print(out.sort_values("vs_line", ascending=False).head(5)[["player", "prop_label", "line_f", "mine_f", "vs_line"]].to_string())
