"""Read-only decision details and audited, recoverable historical-decision archive.

Never removes or rewrites frozen decision payloads or modeled setups.
"""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

import streamlit as st
from phase6_terminal_view import render_decision_body, render_terminal_css


def ensure_decision_archive_schema(conn):
    """Idempotent additive migration; call only after setup_database(conn)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(strategy_decisions)")}
    if not columns:
        raise RuntimeError("Historical decision table is missing.")
    if "archived_at" not in columns:
        conn.execute("ALTER TABLE strategy_decisions ADD COLUMN archived_at TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_decision_archive_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_key TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            action TEXT NOT NULL,
            evidence_source TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT ''
        )
    """)


def archive_decisions(conn, keys, confirmed=False):
    """Archive decisions atomically without deleting or changing decision payloads."""
    if confirmed is not True:
        raise ValueError("Explicit confirmation required.")
    if not isinstance(keys, (list, tuple, set)) or not keys:
        raise ValueError("No historical decisions selected.")
    if any(not isinstance(k, str) or not k.strip() for k in keys):
        raise ValueError("Invalid decision key.")
    unique = sorted(set(keys))
    timestamp = datetime.now(timezone.utc).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        placeholders = ",".join("?" for _ in unique)
        rows = conn.execute(
            f"SELECT decision_key, archived_at FROM strategy_decisions "
            f"WHERE decision_key IN ({placeholders})", unique
        ).fetchall()
        if len(rows) != len(unique) or any(row[1] is not None for row in rows):
            raise ValueError("Selected decision missing or already archived.")
        for key in unique:
            cursor = conn.execute(
                "UPDATE strategy_decisions SET archived_at=? "
                "WHERE decision_key=? AND archived_at IS NULL",
                (timestamp, key),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Archive update failed.")
            conn.execute("""
                INSERT INTO historical_decision_archive_audit
                    (decision_key, changed_at, action, evidence_source, notes)
                VALUES (?, ?, 'ARCHIVED', 'USER_CONFIRMED_SOFT_ARCHIVE',
                    'Hidden from active journal; original payload retained.')
            """, (key, timestamp))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(unique)


def _parse_payload(record):
    try:
        payload = json.loads(record.get("payload_json") or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, TypeError):
        payload = {}
    return payload


@st.dialog("Historical Decision Details", width="large")
def show_decision_details(record, display_mode="Auto"):
    render_terminal_css(display_mode)
    st.caption("Saved historical decision · Read-only · No exchange execution")
    render_decision_body(_parse_payload(record))


@st.dialog("Confirm Archive Historical Decisions", width="large")
def confirm_decision_archive(keys, connection_factory):
    st.warning(
        f"Archive {len(keys)} historical decision(s)? "
        "They will be hidden from the active journal, NOT permanently deleted."
    )
    st.caption(
        "Original decision payloads and strategy provenance remain in SQLite. "
        "A separate audit entry will be written for each archived decision. "
        "Modeled research setups are not modified."
    )
    st.write("**Selected keys:**")
    for key in keys:
        st.code(key[:16] + "...", language=None)
    if st.button("Cancel", key="phase6_decision_archive_cancel", use_container_width=True):
        st.rerun()
    if st.button(
        "Confirm Archive Selected", key="phase6_decision_archive_confirm",
        type="primary", use_container_width=True,
    ):
        conn = connection_factory()
        try:
            count = archive_decisions(conn, keys, confirmed=True)
        except (sqlite3.Error, ValueError, RuntimeError) as exc:
            st.error(f"Archive failed: {exc}")
            return
        finally:
            conn.close()
        for key in keys:
            st.session_state.pop("phase6_decision_selected_" + key, None)
        st.session_state["phase6_decision_select_all"] = False
        st.session_state["phase6_decision_archive_feedback"] = (
            f"Archived {count} historical decision(s). Original data and audit retained."
        )
        st.rerun()


def render_historical_decisions(records, connection_factory, display_mode="Auto"):
    """Active decisions only. Record type remains distinct from manual trades."""
    st.markdown("#### Historical Research Decisions")
    st.caption(
        "These are saved research decisions, not executed trades. "
        "Choose View for full details; Archive Selected hides records without deleting them."
    )
    feedback = st.session_state.pop("phase6_decision_archive_feedback", None)
    if feedback:
        st.success(feedback)
    if not records:
        st.info("No active historical decisions to display.")
        return

    all_keys = [str(row["decision_key"]) for row in records]
    def sync_all():
        selected = bool(st.session_state.get("phase6_decision_select_all", False))
        for key in all_keys:
            st.session_state["phase6_decision_selected_" + key] = selected

    st.checkbox(
        "Select All Historical Decisions",
        key="phase6_decision_select_all",
        on_change=sync_all,
    )
    grouped = defaultdict(list)
    for row in records:
        timestamp = row.get("decision_time_utc") or row.get("created_at")
        try:
            date = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            month = date.strftime("%Y-%m")
            label = date.strftime("%B %Y")
        except (TypeError, ValueError):
            month, label = "0000-00", "Unknown date"
        grouped[(month, label)].append(row)

    selected_keys = []
    for idx, (month, label) in enumerate(sorted(grouped, reverse=True)):
        items = grouped[(month, label)]
        with st.expander(f"{label} · {len(items)} decision(s)", expanded=(idx == 0)):
            for row in items:
                key = str(row["decision_key"])
                signal = row.get("signal") or "NOT AVAILABLE"
                strategy = row.get("strategy") or "NO ELIGIBLE STRATEGY"
                st.markdown(
                    f"**{signal} · {strategy}**  \n"
                    f"Decision UTC: `{row.get('decision_time_utc') or 'NOT AVAILABLE'}`"
                )
                st.checkbox(
                    "Select for archive", key="phase6_decision_selected_" + key,
                )
                if st.session_state.get("phase6_decision_selected_" + key):
                    selected_keys.append(key)
                if st.button(
                    "View Details", key="phase6_view_decision_" + key,
                    use_container_width=True,
                ):
                    show_decision_details(row, display_mode)
                st.divider()

    st.caption(f"Selected historical decisions: {len(selected_keys)} / {len(records)}")
    if st.button(
        "Delete Selected (Soft Archive)",
        key="phase6_archive_decisions",
        disabled=not selected_keys,
        use_container_width=True,
    ):
        confirm_decision_archive(tuple(selected_keys), connection_factory)
    st.caption(
        "Archive is reversible at the database level; this release does not include "
        "a restore button. Existing archived records are retained."
    )
