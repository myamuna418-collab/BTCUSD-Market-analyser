"""
Phase 5.5 ? Read-only BTCUSD historical research analytics.

Uses saved modeled outcomes only.
No exchange orders, SQLite writes, or strategy evaluations.
"""

import json
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st


ARTIFACT_NAME = "phase5_modeled_outcomes_v2.json"

STRATEGIES = [
    "ORIGINAL_WICK",
    "CONTINUATION",
    "PULLBACK",
    "REVERSAL",
    "IMPULSE_PULLBACK",
]

STATUSES = ["TARGET", "STOP", "UNFILLED", "TIMEOUT"]


def load_phase5_artifact(project_dir):
    path = Path(project_dir) / ARTIFACT_NAME

    if not path.is_file():
        raise FileNotFoundError(
            f"Research artifact not found: {path.name}"
        )

    with path.open("r", encoding="utf-8") as f:
        artifact = json.load(f)

    records = artifact["outcomes"]

    expected_counts = {
        "TARGET": 1781,
        "STOP": 839,
        "UNFILLED": 696,
        "TIMEOUT": 55,
    }

    if len(records) != 3371:
        raise ValueError("Unexpected modeled signal count")

    counts = Counter(r["modeled_status"] for r in records)

    if dict(counts) != expected_counts:
        raise ValueError("Modeled outcome count mismatch")

    if artifact["status_counts"] != expected_counts:
        raise ValueError("Artifact metadata mismatch")

    if artifact["independent_delta_api_verified"] is not False:
        raise ValueError("Unexpected source verification metadata")

    if artifact["timeout_realized_r_policy"] != "NULL_NOT_ASSUMED_ZERO":
        raise ValueError("Unexpected TIMEOUT R policy")

    df = pd.DataFrame(records)

    required = {
        "decision_key",
        "decision_time_utc",
        "strategy",
        "direction",
        "modeled_status",
        "modeled_realized_r",
    }

    if not required.issubset(df.columns):
        raise ValueError("Missing required research fields")

    if df["decision_key"].duplicated().any():
        raise ValueError("Duplicate decision keys")

    df["decision_time_utc"] = pd.to_datetime(
        df["decision_time_utc"],
        utc=True,
        errors="raise",
    )

    if df["decision_time_utc"].duplicated().any():
        raise ValueError("Duplicate decision timestamps")

    if not df["strategy"].isin(STRATEGIES).all():
        raise ValueError("Unexpected strategy")

    if not df["direction"].isin(["BUY", "SELL"]).all():
        raise ValueError("Unexpected direction")

    expected_r = df["modeled_status"].map({
        "TARGET": 0.5,
        "STOP": -1.0,
        "UNFILLED": float("nan"),
        "TIMEOUT": float("nan"),
    })

    actual_r = pd.to_numeric(
        df["modeled_realized_r"],
        errors="coerce",
    )

    if not (
        (actual_r == expected_r)
        | (actual_r.isna() & expected_r.isna())
    ).all():
        raise ValueError("Modeled R-value mismatch")

    df["modeled_realized_r"] = actual_r

    return df, artifact


def aggregate_outcomes(df, group_field):
    rows = []

    for name, group in df.groupby(group_field, sort=True):
        counts = group["modeled_status"].value_counts()

        target = int(counts.get("TARGET", 0))
        stop = int(counts.get("STOP", 0))
        unfilled = int(counts.get("UNFILLED", 0))
        timeout = int(counts.get("TIMEOUT", 0))

        fills = target + stop + timeout

        rows.append({
            group_field: name,
            "Signals": len(group),
            "Modeled fills": fills,
            "TARGET": target,
            "STOP": stop,
            "UNFILLED": unfilled,
            "TIMEOUT": timeout,
            "TARGET rate (%)": (
                round(100 * target / fills, 2)
                if fills else 0.0
            ),
            "Known R subtotal": round(
                group["modeled_realized_r"].sum(),
                2,
            ),
        })

    return pd.DataFrame(rows)


def calculate_known_r_sequence(df):
    known = (
        df.dropna(subset=["modeled_realized_r"])
        .sort_values(["decision_time_utc", "decision_key"])
        .copy()
    )

    known["cumulative_known_r"] = (
        known["modeled_realized_r"].cumsum()
    )

    peaks = known["cumulative_known_r"].cummax().clip(lower=0)

    known["sequence_drawdown_r"] = (
        peaks - known["cumulative_known_r"]
    )

    return known


def render_phase5_analytics_dashboard(project_dir):
    st.divider()

    with st.expander(
        "Historical Research Analytics ? Phase 5",
        expanded=False,
    ):
        st.warning(
            "RESEARCH ONLY ? modeled OHLC outcomes, not verified "
            "exchange fills or live trading performance."
        )

        try:
            df, artifact = load_phase5_artifact(project_dir)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            st.error(f"Historical analytics unavailable: {exc}")
            return

        st.caption(
            "Source: phase5_modeled_outcomes_v2.json | "
            "Historical dataset independently exchange-verified: NO | "
            "Fees/slippage: NOT INCLUDED | "
            "TIMEOUT realized R: UNDEFINED."
        )

        counts = df["modeled_status"].value_counts()

        fills = (
            int(counts.get("TARGET", 0))
            + int(counts.get("STOP", 0))
            + int(counts.get("TIMEOUT", 0))
        )

        known_r = float(df["modeled_realized_r"].sum())

        metrics = st.columns(4)

        metrics[0].metric("Research signals", f"{len(df):,}")
        metrics[1].metric("Modeled fills", f"{fills:,}")
        metrics[2].metric("Known modeled R", f"{known_r:+.1f}R")
        metrics[3].metric(
            "Undefined TIMEOUT R",
            str(int(counts.get("TIMEOUT", 0))),
        )

        st.caption(
            "Known R is a partial subtotal, NOT net profit "
            "or executable portfolio return."
        )

        tab_strategy, tab_direction, tab_month, tab_risk = st.tabs([
            "Strategies",
            "BUY vs SELL",
            "Monthly",
            "Known-R sequence",
        ])

        with tab_strategy:
            table = aggregate_outcomes(df, "strategy")
            table["strategy"] = pd.Categorical(
                table["strategy"],
                categories=STRATEGIES,
                ordered=True,
            )
            table = table.sort_values("strategy")
            st.dataframe(
                table,
                hide_index=True,
                use_container_width=True,
            )

        with tab_direction:
            table = aggregate_outcomes(df, "direction")
            st.dataframe(
                table,
                hide_index=True,
                use_container_width=True,
            )

        with tab_month:
            monthly = df.copy()
            monthly["month"] = (
                monthly["decision_time_utc"]
                .dt.strftime("%Y-%m")
            )

            table = aggregate_outcomes(monthly, "month")
            st.dataframe(
                table,
                hide_index=True,
                use_container_width=True,
            )

            if not table.empty:
                st.bar_chart(
                    table.set_index("month")["Known R subtotal"]
                )

            st.caption(
                "January and November are partial months "
                "in this research replay."
            )

        with tab_risk:
            known = calculate_known_r_sequence(df)

            max_dd = (
                float(known["sequence_drawdown_r"].max())
                if not known.empty else 0.0
            )

            st.metric(
                "Maximum known-R sequence drawdown",
                f"{max_dd:.1f}R",
            )

            st.caption(
                "Decision-time ordered known-R sequence only. "
                "NOT an executable portfolio equity curve. "
                "Overlapping trades and actual exit chronology "
                "are not represented."
            )

            if not known.empty:
                chart_data = known.set_index(
                    "decision_time_utc"
                )["cumulative_known_r"]

                st.line_chart(chart_data)

        st.info(
            "Frozen strategy rules unchanged. "
            "No exchange orders or SQLite writes."
        )
