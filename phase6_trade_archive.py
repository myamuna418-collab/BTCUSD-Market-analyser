"""Audit-preserving soft archive for manually recorded research trades."""

from datetime import datetime, timezone


def archive_manual_trades(conn, trade_ids, confirmation=False):
    """Archive selected manual trades atomically without deleting records."""
    if confirmation is not True:
        raise ValueError("Explicit archive confirmation is required.")

    if not isinstance(trade_ids, (list, tuple, set)):
        raise ValueError("Trade IDs must be a collection.")

    if not trade_ids:
        raise ValueError("No trades selected.")

    if any(type(trade_id) is not int or trade_id <= 0 for trade_id in trade_ids):
        raise ValueError("Trade IDs must be positive integers.")

    selected = sorted(set(trade_ids))

    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(trades)")
    }
    if "archived_at" not in columns:
        raise RuntimeError("Archive migration is missing.")

    archived_at = datetime.now(timezone.utc).isoformat()

    with conn:
        placeholders = ",".join("?" for _ in selected)

        rows = conn.execute(
            f"SELECT id, status, archived_at FROM trades "
            f"WHERE id IN ({placeholders})",
            selected,
        ).fetchall()

        if len(rows) != len(selected):
            raise ValueError("One or more selected trades do not exist.")

        if any(row[2] is not None for row in rows):
            raise ValueError("One or more selected trades are already archived.")

        for trade_id, status, previous_archive in rows:
            cursor = conn.execute(
                "UPDATE trades SET archived_at = ? "
                "WHERE id = ? AND archived_at IS NULL",
                (archived_at, trade_id),
            )

            if cursor.rowcount != 1:
                raise RuntimeError("Trade archive update failed.")

            conn.execute(
                "INSERT INTO journal_audit "
                "(trade_id, changed_at, previous_status, new_status, "
                "evidence_source, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    trade_id,
                    archived_at,
                    status,
                    "ARCHIVED",
                    "USER_CONFIRMED_SOFT_ARCHIVE",
                    "Hidden from active research journal; "
                    "original trade and outcome retained.",
                ),
            )

    return {
        "archived_count": len(selected),
        "trade_ids": selected,
        "archived_at": archived_at,
    }
