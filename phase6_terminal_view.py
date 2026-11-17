"""Responsive, read-only historical decision terminal. No execution features."""
from html import escape
from math import isfinite
import streamlit as st

STRATEGIES = ("ORIGINAL_WICK", "CONTINUATION", "PULLBACK", "REVERSAL", "IMPULSE_PULLBACK")


def _text(value, missing="NOT AVAILABLE"):
    if value is None or value == "":
        return missing
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".") if isfinite(value) else missing
    return str(value)


def _price(value):
    try:
        n = float(value)
        return f"{n:,.2f}" if isfinite(n) else "NOT AVAILABLE"
    except (TypeError, ValueError):
        return "NOT AVAILABLE"


def _safe(value):
    return escape(str(value), quote=True)


def render_terminal_css(display_mode="Auto"):
    mobile = display_mode == "Mobile"
    max_width = "560px" if mobile else "1480px"
    grid = "1fr" if mobile else "repeat(auto-fit, minmax(min(100%, 230px), 1fr))"
    st.markdown("""
    <style>
    .stApp {background:#080f1c;color:#edf3ff}
    .block-container {max-width:MAXWIDTH;padding-top:1.2rem;padding-bottom:3rem}
    .phase6-grid {display:grid;grid-template-columns:GRID;gap:12px;margin:12px 0 22px}
    .phase6-card {min-width:0;padding:16px 18px;border:1px solid #2c4565;
        border-radius:14px;background:linear-gradient(140deg,#152841,#0e1b30)}
    .phase6-label {font-size:.78rem;letter-spacing:.06em;font-weight:700;
        color:#b4c8e4;margin-bottom:10px;overflow-wrap:anywhere}
    .phase6-value {font-size:clamp(1.05rem,2.2vw,1.6rem);line-height:1.35;
        font-weight:750;color:#fff;overflow-wrap:anywhere;word-break:normal;
        white-space:normal;user-select:text}
    .phase6-meta {font-size:.88rem;color:#b9cce7;overflow-wrap:anywhere}
    .phase6-row {padding:11px 0;border-bottom:1px solid #233954;
        overflow-wrap:anywhere;color:#edf3ff}
    .phase6-row strong {color:#b9d6ff}
    [data-testid="stMain"] [data-testid="stExpander"] summary,
    [data-testid="stMain"] [data-testid="stExpander"] summary * {
        color:#fff!important;opacity:1!important}
    [data-testid="stMain"] [data-testid="stCaptionContainer"] *,
    [data-testid="stMain"] [data-testid="stCaptionContainer"] {color:#b5c9e5!important;opacity:1!important}
    @media(max-width:768px) {
        .phase6-grid {grid-template-columns:1fr}
        .phase6-card {padding:14px}
        .phase6-value {font-size:1.2rem}
        .block-container {padding-left:.8rem;padding-right:.8rem}
    }
    </style>
    """.replace("MAXWIDTH", max_width).replace("GRID", grid), unsafe_allow_html=True)


def _cards(items):
    parts = ['<div class="phase6-grid">']
    for label, value in items:
        parts.append(
            '<div class="phase6-card"><div class="phase6-label">'
            + _safe(label) + '</div><div class="phase6-value">'
            + _safe(value) + '</div></div>'
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def _row(label, value):
    st.markdown(
        '<div class="phase6-row"><strong>' + _safe(label) +
        ':</strong> ' + _safe(value) + '</div>',
        unsafe_allow_html=True,
    )


def render_decision_body(snapshot):
    """Shared mobile-friendly view for terminal and journal detail dialogs."""
    signal = snapshot.get("signal") or "NOT AVAILABLE"
    strategy = snapshot.get("strategy") or (
        "NO ELIGIBLE STRATEGY" if signal == "NO TRADE" else "NOT AVAILABLE"
    )
    has_setup = signal in ("BUY", "SELL")
    _cards([
        ("DATA READINESS", "SAVED HISTORICAL RECORD"),
        ("SELECTED STRATEGY", strategy),
        ("RESEARCH SIGNAL", signal),
    ])

    st.markdown("#### SIGNAL DETAILS")
    def level(key, kind="price"):
        if not has_setup:
            return "NOT APPLICABLE"
        value = snapshot.get(key)
        return _price(value) if kind == "price" else _text(value)
    _cards([
        ("ENTRY", level("entry")),
        ("STOP-LOSS", level("stop")),
        ("TARGET", level("target")),
        ("REWARD / RISK", level("rr", "text")),
        ("EXPIRY UTC", level("expiry_utc", "text")),
    ])
    if signal == "NO TRADE":
        st.info("No eligible setup. Entry, stop, target, RR and expiry do not apply.")
    else:
        st.caption("Indicative research levels, not observed orders or exchange fills.")

    st.markdown("#### ESSENTIAL MARKET ANALYSIS")
    for label, key in [
        ("ATR (14), 1H", "atr_1h_14"),
        ("Momentum, 3H / ATR", "momentum_3h_atr"),
        ("Relative volatility", "relative_volatility"),
        ("+DI, 4H", "plus_di_4h"),
        ("-DI, 4H", "minus_di_4h"),
    ]:
        _row(label, _text(snapshot.get(key)))
    st.caption("No live trend or candle status is inferred from historical indicators.")

    st.markdown("#### FIVE-STRATEGY EVALUATION")
    eligible = {
        item.get("strategy"): item.get("direction")
        for item in (snapshot.get("eligible_candidates") or [])
        if isinstance(item, dict) and item.get("strategy") in STRATEGIES
    }
    for name in STRATEGIES:
        status = ("ELIGIBLE / " + str(eligible[name])) if name in eligible else "NOT ELIGIBLE"
        _row(name, status)

    st.markdown("#### RECORD PROVENANCE")
    for label, key in [
        ("Decision UTC", "decision_time_utc"),
        ("Signal candle OPEN UTC", "signal_candle_open_utc"),
        ("Strategy version", "strategy_version"),
        ("Data source", "data_source"),
        ("Engine explanation", "reason"),
        ("Decision key", "decision_key"),
    ]:
        _row(label, _text(snapshot.get(key)))
    st.caption("Historical decision only · Research mode · No live orders or verified fills.")


def render_phase6_terminal(snapshot, display_mode="Auto"):
    render_terminal_css(display_mode)
    st.title("BTCUSD SIGNAL ANALYSER")
    st.caption("HISTORICAL RESEARCH TERMINAL · Saved decision · No live orders")
    if st.button("← Back to Analysis Sheet", key="phase6_back_to_analysis", use_container_width=True):
        st.session_state["phase6_screen"] = "analysis"
        st.rerun()
    render_decision_body(snapshot)
