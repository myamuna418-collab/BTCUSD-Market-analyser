"""Responsive read-only details for manual research journal entries."""
import streamlit as st
from phase6_terminal_view import render_terminal_css, _cards, _row, _price, _text


def _r(value):
    try:
        return f"{float(value):+.2f}R" if value is not None else "NOT AVAILABLE"
    except (TypeError, ValueError):
        return "NOT AVAILABLE"


@st.dialog("Manual Research Trade Details", width="large")
def show_trade_details(trade, audit_rows, display_mode="Auto"):
    render_terminal_css(display_mode)
    trade_id = trade.get("id")
    st.caption(f"MANUAL RESEARCH TRADE #{_text(trade_id)} · NO VERIFIED EXCHANGE FILL")
    _cards([
        ("DIRECTION", _text(trade.get("direction"))),
        ("STATUS", _text(trade.get("status"))),
        ("ENTRY", _price(trade.get("entry"))),
        ("STOP-LOSS", _price(trade.get("stop"))),
        ("TARGET", _price(trade.get("target"))),
        ("REPORTED OUTCOME (R)", _r(trade.get("realized_r"))),
    ])
    for label, key in [
        ("Created UTC", "created_at"),
        ("Symbol", "symbol"),
        ("Outcome source", "outcome_source"),
        ("Notes", "notes"),
        ("Archived UTC", "archived_at"),
    ]:
        _row(label, _text(trade.get(key)))

    st.markdown("#### AUDIT HISTORY")
    matching = [
        row for row in audit_rows
        if str(row.get("trade_id")) == str(trade_id)
    ]
    if not matching:
        st.info("No audit records available.")
    for idx, row in enumerate(matching):
        with st.expander(
            f"{_text(row.get('changed_at'))} · {_text(row.get('new_status'))}",
            expanded=(idx == 0),
        ):
            for label, key in [
                ("Previous status", "previous_status"),
                ("New status", "new_status"),
                ("Evidence", "evidence_source"),
                ("Notes", "notes"),
            ]:
                _row(label, _text(row.get(key)))

    st.caption(
        "Research only. R-multiples are not currency P&L. "
        "No exchange order or fill has been independently verified."
    )
