# CFB Betting Labs — Streamlit

Production college football betting dashboard (Bettor Odds–style UI).

## Run

```bash
pip install -r streamlit_app/requirements.txt
npm run streamlit
```

Open **http://localhost:8501**

Optional: `npm start` on port 3000 unlocks full matchup SP+, graded results, and pinnacle edges via Node API.

## Tabs

| Tab | Data sources |
|-----|----------------|
| Home | Overview |
| Injury Room | ESPN (`site.web.api.espn.com`) |
| Projections | Onyx slate / SP+ |
| Market Moves | The Odds API snapshots |
| Closing Accuracy | Labs results + Odds API |
| +EV Player Props | Onyx props |
| Matchup Detail | Node `/api/labs/matchup` |
| Weather Report | ESPN venues + Open-Meteo |
| CLV Leaderboard | CFBD finals + snapshots |
| Biggest Moves | Local snapshot archive |
| Pinnacle Sharp | The Odds API / Node edges |

## Books

US-legal retail + Pinnacle only: DraftKings, FanDuel, BetMGM, Caesars, BetRivers, ESPN BET, Fanatics, Pinnacle.

## Env

Uses root `.env`:

- `THE_ODDS_API_KEY` — live lines, edges, snapshots
- `FOOTBALL_LABS_API` — optional Node base URL (default `http://localhost:3000`)

## Excel audit trail

Every data pull is saved automatically to `data/streamlit_exports/`:

| File | Contents |
|------|----------|
| `{source}_{YYYYMMDD_HHMMSS}.xlsx` | Individual timestamped pull |
| `cfb_master_{YYYY-MM-DD}.xlsx` | Daily rollup — one sheet per source + `pull_log` |

Each row includes `pull_timestamp_utc`, `pull_source`, `pull_tab`, and `pull_origin` (`live`, `cache`, `node_api`, or `computed`).
