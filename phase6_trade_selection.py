"""Selection and confirmed soft-archive UI for manual research trades."""

import sqlite3
import streamlit as st

from phase6_trade_archive import archive_manual_trades


@st.dialog("Confirm Delete Selected", width="large")
def confirm_trade_archive(selected_ids, connection_factory):
    """Require an explicit second action before archiving trades."""
    selected_ids = tuple(sorted(set(selected_ids)))

    st.warning(
        f"You selected {len(selected_ids)} manual research trade(s). "
        "They will be hidden from the active journal, not permanently deleted."
    )

    st.caption(
        "Original trade records, outcomes and audit history remain in SQLite. "
        "Historical strategy decisions and modeled research setups are unaffected."
    )

    st.write("**Selected trade IDs:**", ", ".join(map(str, selected_ids)))

    left, right = st.columns(2)

    with left:
        if st.button(
            "Cancel",
            key="phase6_archive_cancel",
            use_container_width=True,
        ):
            st.rerun()

    with right:
        if st.button(
            "Confirm Delete Selected",
            key="phase6_archive_confirm",
            type="primary",
            use_container_width=True,
        ):
            try:
                conn = connection_factory()
                try:
                    result = archive_manual_trades(
                        conn,
                        list(selected_ids),
                        confirmation=True,
                    )
                finally:
                    conn.close()
            except (ValueError, RuntimeError, sqlite3.Error) as exc:
                st.error(f"Archive failed: {exc}")
                return

            for trade_id in selected_ids:
                st.session_state.pop(
                    f"phase6_select_trade_{trade_id}", None
                )

            st.session_state["phase6_select_all_trades"] = False
            st.session_state["phase6_archive_feedback"] = (
                f"Archived {result['archived_count']} research trade(s). "
                "Original records and audit history preserved."
            )
            st.rerun()


def render_trade_selection(active_trades, connection_factory):
    """Render selection controls for active manual research trades."""
    feedback = st.session_state.pop("phase6_archive_feedback", None)
    if feedback:
        st.success(feedback)

    if not active_trades:
        return

    st.markdown("#### Manage Manual Research Trades")
    st.caption(
        "Select trades to hide from the active journal. "
        "Delete Selected performs a recoverable, audited soft archive."
    )

    trade_ids = [int(trade["id"]) for trade in active_trades]

    def sync_select_all():
        checked = bool(
            st.session_state.get("phase6_select_all_trades", False)
        )
        for trade_id in trade_ids:
            st.session_state[f"phase6_select_trade_{trade_id}"] = checked

    select_all = st.checkbox(
        "Select All",
        key="phase6_select_all_trades",
        on_change=sync_select_all,
    )

    selected_ids = []

    for trade in active_trades:
        trade_id = int(trade["id"])
        key = f"phase6_select_trade_{trade_id}"

        checked = st.checkbox(
            f"Trade #{trade_id} | "
            f"{trade.get('direction', 'UNKNOWN')} | "
            f"{trade.get('status', 'UNKNOWN')}",
            key=key,
        )

        if checked:
            selected_ids.append(trade_id)

    st.caption(f"Selected: {len(selected_ids)} / {len(trade_ids)}")

    if st.button(
        "Delete Selected",
        key="phase6_archive_selected",
        disabled=not selected_ids,
        use_container_width=True,
    ):
        confirm_trade_archive(selected_ids, connection_factory)
