"""Calibrated constants — NFL methodology + CFB defaults."""
from __future__ import annotations

# NFL (nflverse 2015–2025, excl 2016/2020)
NFL_LEAGUE_AVG = 22.47
NFL_R_DISPERSION = 7.83
NFL_HFA_BASE = 1.706
NFL_HFA_DIVISIONAL = -0.790
NFL_HFA_SURFACE = 0.986
NFL_HFA_HOME_BYE = -0.467
NFL_HFA_AWAY_BYE = -1.232
NFL_ELO_BASE = 1500.0
NFL_ELO_SCALE = 25.0
NFL_TD_SPLIT = (0.58, 0.37, 0.05)
NFL_SACKS_PER_GAME = 2.39
NFL_INTS_PER_GAME = 0.70

# CFB (Bill Connelly SP+ calibration)
CFB_LEAGUE_AVG = 28.5
CFB_HFA = 2.81
CFB_R_DISPERSION = 10.5

# Simulation — 3k vectorized sims ≈ methodology 10k stability for UI latency
DEFAULT_SIMS = 3000
PERIOD_Q1 = 0.235
PERIOD_H1 = 0.545
YARD_CV = 0.28
TD_CV = 0.22

# Live — odds poll matches background 4C mirror (max 5s); projections are cached per game
LIVE_POLL_SEC = 8
ODDS_POLL_SEC = 5
FOURC_POLL_SEC = 5
PROJECTION_CACHE_TTL = 3600
