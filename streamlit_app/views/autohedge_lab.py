"""Autohedge & TKO staking lab — Kelly, arb, and expected-growth tools."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib.autohedge_math import (
    BOOK_BASKETBALL_PRESET,
    RUfus_DEMON_PRESET,
    american_to_net_odds,
    autohedge_fraction,
    balanced_hedge_fraction,
    compare_strategies,
    eg_hedge_curve,
    expected_log_growth,
    guaranteed_arb_profit,
    kelly_fraction,
    optimal_hedge_fraction,
    tko_expected_log_growth,
    tko_plus_ev_fraction,
    vig_free_prob,
)
from lib.odds_math import implied_to_american


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _pct(x: float, digits: int = 2) -> str:
    return f"{100 * x:.{digits}f}%"


def _bps(x: float) -> str:
    return f"{x * 10000:.1f} bps"


def _eg_chart(
    f_value: float,
    p_value: float,
    odds_value: float,
    odds_hedge: float,
    *,
    h_auto: float,
    h_bal: float,
    half_kelly_f: float | None = None,
) -> go.Figure:
    hs, egs = eg_hedge_curve(
        f_value, p_value_win=p_value, odds_value=odds_value, odds_hedge=odds_hedge
    )
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=hs * 100,
            y=egs * 10000,
            mode="lines",
            name="Full Kelly on value side",
            line=dict(color="#60a5fa", width=2.5),
        )
    )
    if half_kelly_f is not None and half_kelly_f > 0:
        hs2, egs2 = eg_hedge_curve(
            half_kelly_f,
            p_value_win=p_value,
            odds_value=odds_value,
            odds_hedge=odds_hedge,
        )
        fig.add_trace(
            go.Scatter(
                x=hs2 * 100,
                y=egs2 * 10000,
                mode="lines+markers",
                name="Half Kelly on value side",
                line=dict(color="#a78bfa", width=2, dash="dot"),
                marker=dict(size=4, symbol="diamond"),
            )
        )
    eg_auto = expected_log_growth(
        f_value,
        h_auto,
        p_value_win=p_value,
        b_value=american_to_net_odds(odds_value),
        b_hedge=american_to_net_odds(odds_hedge),
    )
    eg_bal = expected_log_growth(
        f_value,
        h_bal,
        p_value_win=p_value,
        b_value=american_to_net_odds(odds_value),
        b_hedge=american_to_net_odds(odds_hedge),
    )
    fig.add_trace(
        go.Scatter(
            x=[h_auto * 100],
            y=[eg_auto * 10000],
            mode="markers",
            name="Autohedge",
            marker=dict(size=14, color="#4ade80", symbol="circle", line=dict(width=2, color="#fff")),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[h_bal * 100],
            y=[eg_bal * 10000],
            mode="markers",
            name="Balanced arb",
            marker=dict(size=14, color="#fafafa", symbol="circle", line=dict(width=2, color="#71717a")),
        )
    )
    fig.update_layout(
        template="plotly_dark",
        height=420,
        margin=dict(l=40, r=20, t=40, b=40),
        xaxis_title="Hedge stake (% of bankroll) on opposite side",
        yaxis_title="Expected log growth (basis points)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        hovermode="x unified",
    )
    return fig


def _tko_chart(p: float, b_plus: float, b_minus: float) -> go.Figure:
    fs = [i / 200 for i in range(1, 200)]
    egs = [tko_expected_log_growth(f, p=p, b_plus=b_plus, b_minus=b_minus) for f in fs]
    f_star = tko_plus_ev_fraction(p)
    eg_star = tko_expected_log_growth(f_star, p=p, b_plus=b_plus, b_minus=b_minus)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[f * 100 for f in fs],
            y=[e * 10000 for e in egs],
            mode="lines",
            name="EG vs +EV allocation",
            line=dict(color="#60a5fa", width=2.5),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[f_star * 100],
            y=[eg_star * 10000],
            mode="markers",
            name=f"TKO optimum ({p:.1%} on +EV)",
            marker=dict(size=14, color="#4ade80", symbol="circle", line=dict(width=2, color="#fff")),
        )
    )
    fig.update_layout(
        template="plotly_dark",
        height=380,
        margin=dict(l=40, r=20, t=40, b=40),
        xaxis_title="Fraction of bankroll on +EV side (%)",
        yaxis_title="Expected log growth (bps)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def render() -> None:
    st.title("Autohedge & TKO Staking Lab")
    st.caption(
        "Kelly criterion staking with strategic hedges — value bets, balanced arbs, and autohedges. "
        "Based on *But How Much Did You Lose?* (Dan Abrams)."
    )

    tab_calc, tab_chart, tab_tko, tab_compare = st.tabs(
        ["Autohedge calculator", "EG vs hedge", "TKO / Rufus' demon", "Strategy compare"]
    )

    with tab_calc:
        st.subheader("Two-book spot: value side + hedge")
        preset = st.toggle("Load book example (1H basketball ML)", value=True)
        p = BOOK_BASKETBALL_PRESET if preset else {}
        c1, c2, c3 = st.columns(3)
        with c1:
            bankroll = st.number_input(
                "Bankroll ($)", min_value=100.0, value=float(p.get("bankroll", 2500.0)), step=100.0
            )
            odds_value = st.number_input(
                "Value odds (soft book, American)", value=int(p.get("odds_value", 400)), step=5
            )
        with c2:
            odds_hedge = st.number_input(
                "Hedge odds (sharp book, American)", value=int(p.get("odds_hedge", -350)), step=5
            )
            value_stake = st.number_input(
                "Stake on value side ($)", min_value=1.0, value=float(p.get("value_stake", 100.0)), step=10.0
            )
        with c3:
            sharp_a = st.number_input(
                "Sharp book Team A (for true prob)", value=int(p.get("sharp_a", 315)), step=5
            )
            sharp_b = st.number_input(
                "Sharp book Team B", value=int(p.get("sharp_b", -350)), step=5
            )

        p_devig, p_b_devig = vig_free_prob(float(sharp_a), float(sharp_b))
        use_manual_p = st.checkbox("Override true P(value side)", value=False)
        if use_manual_p:
            p_a = st.slider("True P(value side wins)", 0.01, 0.99, 0.232, 0.001)
        else:
            p_a = p_devig
        p_b = 1.0 - p_a
        st.caption(f"Devigged sharp line → P(A) = {_pct(p_devig)} ({implied_to_american(p_devig)})")
        f_v = value_stake / bankroll
        k_a = kelly_fraction(p_a, odds_value)
        k_b = kelly_fraction(p_b, odds_hedge)
        h_bal = balanced_hedge_fraction(f_v, odds_value, odds_hedge)
        h_auto = autohedge_fraction(f_v, p_value_win=p_a, odds_value=odds_value, odds_hedge=odds_hedge)
        h_opt = optimal_hedge_fraction(
            f_v, p_value_win=p_a, odds_value=odds_value, odds_hedge=odds_hedge
        )
        _, arb_profit = guaranteed_arb_profit(
            bankroll, value_stake, odds_value=odds_value, odds_hedge=odds_hedge
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("True P(value side)", _pct(p_a))
        m2.metric("Kelly (value side)", _pct(k_a))
        m3.metric("Kelly (hedge side)", _pct(k_b))
        m4.metric("Vig-free fair line", implied_to_american(p_a) or "—")

        st.markdown("#### Recommended stakes")
        rec = pd.DataFrame(
            [
                {
                    "Strategy": "Value only (no hedge)",
                    "Value side": _money(value_stake),
                    "Hedge side": _money(0),
                    "Guaranteed profit": "—",
                },
                {
                    "Strategy": "Balanced arb",
                    "Value side": _money(value_stake),
                    "Hedge side": _money(h_bal * bankroll),
                    "Guaranteed profit": _money(arb_profit),
                },
                {
                    "Strategy": "Autohedge (why not both?)",
                    "Value side": _money(value_stake),
                    "Hedge side": _money(h_auto * bankroll),
                    "Guaranteed profit": "—",
                },
            ]
        )
        st.dataframe(rec, use_container_width=True, hide_index=True)

        st.info(
            f"Balanced arb hedge = **{_pct(h_bal)}** of bankroll. "
            f"Hedge-side Kelly = **{_pct(k_b)}** (often negative when value side is maxed). "
            f"Autohedge hedge ≈ balanced − |Kelly| = **{_pct(h_auto)}** "
            f"({_money(h_auto * bankroll)} on the opposite side)."
        )

        with st.expander("Formula reference"):
            st.markdown(
                """
                - **Kelly:** `f* = (b·p − q) / b` where `b` = net fractional odds
                - **Balanced arb hedge:** `h = f·(1+b_value) / (1+b_hedge)`
                - **Autohedge:** `h_auto = h_balanced + Kelly_hedge` (Kelly on hedge is often negative)
                - **Expected growth:** `EG = p·ln(G_win) + (1−p)·ln(G_lose)` on bankroll multipliers
                """
            )

    with tab_chart:
        st.subheader("Expected growth vs hedge size")
        g1, g2, g3 = st.columns(3)
        with g1:
            chart_bankroll = st.number_input("Bankroll ($)", min_value=100.0, value=2500.0, step=100.0, key="chart_br")
            chart_odds_v = st.number_input("Value odds", value=400, step=5, key="chart_ov")
        with g2:
            chart_odds_h = st.number_input("Hedge odds", value=-350, step=5, key="chart_oh")
            chart_stake = st.number_input("Value stake ($)", min_value=1.0, value=100.0, step=10.0, key="chart_st")
        with g3:
            chart_p = st.slider("P(value side wins)", 0.05, 0.95, 0.232, 0.001, key="chart_p")
            show_half = st.checkbox("Overlay half-Kelly value curve", value=True)

        f_chart = chart_stake / chart_bankroll
        h_a = autohedge_fraction(
            f_chart, p_value_win=chart_p, odds_value=chart_odds_v, odds_hedge=chart_odds_h
        )
        h_b = balanced_hedge_fraction(f_chart, chart_odds_v, chart_odds_h)
        k_full = max(0.0, kelly_fraction(chart_p, chart_odds_v))
        half_f = k_full * 0.5 if show_half else None

        st.plotly_chart(
            _eg_chart(
                f_chart,
                chart_p,
                chart_odds_v,
                chart_odds_h,
                h_auto=h_a,
                h_bal=h_b,
                half_kelly_f=half_f,
            ),
            use_container_width=True,
        )
        st.caption(
            "Full Kelly with no hedge is at the left edge (0% hedge). "
            "Autohedge sits between naked Kelly and balanced arb — higher EG than either alone."
        )

    with tab_tko:
        st.subheader("TKO portfolio allocation (Rufus' demon)")
        st.markdown(
            "When you can bet both sides and deploy the whole bankroll, "
            "the optimal fraction on the +EV side equals its **true win probability** — "
            "regardless of the posted odds."
        )
        t1, t2 = st.columns(2)
        with t1:
            load_demon = st.button("Load Elihu Feustel coin example (+300 / −105)")
            tko_p = st.slider("P(+EV side wins)", 0.05, 0.95, 0.5, 0.01, key="tko_p")
            tko_plus = st.number_input("+EV American odds", value=300, step=5, key="tko_plus")
        with t2:
            tko_minus = st.number_input("Opposite American odds", value=-105, step=5, key="tko_minus")
            if load_demon:
                st.session_state["tko_p"] = RUfus_DEMON_PRESET["p_plus"]
                st.session_state["tko_plus"] = RUfus_DEMON_PRESET["odds_plus"]
                st.session_state["tko_minus"] = RUfus_DEMON_PRESET["odds_minus"]
                st.rerun()

        b_plus = american_to_net_odds(float(tko_plus))
        b_minus = american_to_net_odds(float(tko_minus))
        f_tko = tko_plus_ev_fraction(tko_p)
        eg_tko = tko_expected_log_growth(f_tko, p=tko_p, b_plus=b_plus, b_minus=b_minus)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("TKO: stake +EV side", _pct(f_tko))
        c2.metric("TKO: stake opposite", _pct(1 - f_tko))
        c3.metric("Expected log growth", _bps(eg_tko))
        ev_plus = tko_p * b_plus - (1 - tko_p)
        c4.metric("+EV side edge", _pct(ev_plus))

        st.plotly_chart(_tko_chart(tko_p, b_plus, b_minus), use_container_width=True)

        st.markdown(
            f"At **{tko_plus:+d}** / **{tko_minus:+d}** with fair coin (`p = {tko_p:.0%}`), "
            f"put **{_pct(f_tko)}** on the +EV side and **{_pct(1 - f_tko)}** on the opposite. "
            "If sportsbook limits cap the +EV side, bet the limit and autohedge the remainder."
        )

    with tab_compare:
        st.subheader("Strategy comparison")
        s1, s2, s3 = st.columns(3)
        with s1:
            cmp_bankroll = st.number_input("Bankroll ($)", value=2500.0, step=100.0, key="cmp_br")
            cmp_odds_v = st.number_input("Value odds", value=400, key="cmp_ov")
        with s2:
            cmp_odds_h = st.number_input("Hedge odds", value=-350, key="cmp_oh")
            cmp_cap = st.number_input("Value stake cap ($)", value=100.0, key="cmp_cap")
        with s3:
            cmp_sharp_a = st.number_input("Sharp A (devig)", value=315, key="cmp_sa")
            cmp_sharp_b = st.number_input("Sharp B", value=-350, key="cmp_sb")

        p_cmp, _ = vig_free_prob(float(cmp_sharp_a), float(cmp_sharp_b))
        plans = compare_strategies(
            cmp_bankroll,
            p_value_win=p_cmp,
            odds_value=cmp_odds_v,
            odds_hedge=cmp_odds_h,
            value_stake_cap=cmp_cap,
        )
        rows = []
        for pl in plans:
            rows.append(
                {
                    "Strategy": pl.label,
                    "Value $": round(pl.stake_value, 2),
                    "Hedge $": round(pl.stake_hedge, 2),
                    "EG (bps)": round(pl.expected_log_growth * 10000, 1),
                    "Max loss $": round(pl.amount_at_risk, 2),
                    "Locked profit $": round(pl.guaranteed_profit, 2)
                    if pl.guaranteed_profit is not None
                    else None,
                }
            )
        df = pd.DataFrame(rows).sort_values("EG (bps)", ascending=False)
        st.dataframe(df, use_container_width=True, hide_index=True)
        best = df.iloc[0]
        st.success(
            f"Highest expected log growth: **{best['Strategy']}** at **{best['EG (bps)']:.1f} bps** "
            f"(vs balanced arb at {df[df['Strategy']=='Balanced arb']['EG (bps)'].iloc[0]:.1f} bps)."
        )
