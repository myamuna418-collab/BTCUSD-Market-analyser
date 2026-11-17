
# ============================================================
# BTCUSD RESEARCH — DELTA EXCHANGE
# SECTION 1 + 2: DASHBOARD, DISPLAY MODE, TRADE JOURNAL
#
# Research only. No live exchange orders.
# OCR and frozen strategy integration will be added later.
# ============================================================

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import sqlite3
import io
import re
import json
import math
import os
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from datetime import timedelta

from hybrid_ocr import extract_fields as hybrid_extract_fields
from btcusd_theme import apply_theme as apply_approved_theme

from PIL import Image, ImageEnhance, ImageOps
import pytesseract

import pandas as pd
from btcusd_data_validator import validate_historical_rows, aggregate_history
from btcusd_strategy_engine import evaluate, save_decision, setup_database, advance_from_history
import streamlit as st
from phase5_research_dashboard import render_phase5_analytics_dashboard


# ============================================================
# 1. APPLICATION CONFIGURATION
# ============================================================

APP_NAME = "BTCUSD Research"
APP_VERSION = "2.0 — Integrated Research"

PROJECT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = PROJECT_DIR / "btcusd_research_journal.sqlite3"

MAX_SCREENSHOTS = 4
LIVE_TRADING_ENABLED = False

STATUSES = [
    "PENDING",
    "TARGET",
    "STOP",
    "TIMEOUT",
    "UNFILLED",
]

st.set_page_config(
    page_title="BTCUSD Research | Delta Exchange",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# 2. DATABASE CONNECTION AND SETUP
# ============================================================

def get_connection():
    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=15,
    )
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():

    with get_connection() as connection:

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry REAL NOT NULL,
                stop REAL NOT NULL,
                target REAL NOT NULL,
                status TEXT NOT NULL,
                outcome_source TEXT NOT NULL,
                realized_r REAL,
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                changed_at TEXT NOT NULL,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                evidence_source TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )


    # Additive migrations only; never replace or drop existing journal tables.
    with get_connection() as connection:
        setup_database(connection)
        from phase6_decision_journal import ensure_decision_archive_schema

        trade_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(trades)")
        }
        if "archived_at" not in trade_columns:
            connection.execute("ALTER TABLE trades ADD COLUMN archived_at TEXT")
        ensure_decision_archive_schema(connection)


def utc_now():
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )


# ============================================================
# 3. TRADE JOURNAL DATABASE OPERATIONS
# ============================================================

def load_trades():

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM trades
            ORDER BY id DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def load_audit():

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM journal_audit
            ORDER BY id DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def create_manual_trade(
    direction,
    entry,
    stop,
    target,
    notes,
):

    if direction == "BUY":
        valid = stop < entry < target
    else:
        valid = target < entry < stop

    if not valid:
        raise ValueError(
            "Invalid price ordering for the trade direction."
        )

    with get_connection() as connection:

        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute(
            """
            SELECT id
            FROM trades
            WHERE status = 'PENDING'
              AND archived_at IS NULL
            LIMIT 1
            """
        ).fetchone()

        if existing:
            raise ValueError(
                "A pending trade already exists. "
                "Resolve it before adding another."
            )

        cursor = connection.execute(
            """
            INSERT INTO trades (
                created_at,
                symbol,
                direction,
                entry,
                stop,
                target,
                status,
                outcome_source,
                realized_r,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                "BTCUSD",
                direction,
                entry,
                stop,
                target,
                "PENDING",
                "MANUAL_RESEARCH_ENTRY",
                None,
                notes,
            ),
        )

        trade_id = cursor.lastrowid

        connection.execute(
            """
            INSERT INTO journal_audit (
                trade_id,
                changed_at,
                previous_status,
                new_status,
                evidence_source,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                utc_now(),
                None,
                "PENDING",
                "MANUAL_RESEARCH_ENTRY",
                notes,
            ),
        )

    return trade_id


def update_trade_outcome(
    trade_id,
    new_status,
    notes,
):

    if new_status not in [
        "TARGET",
        "STOP",
        "TIMEOUT",
        "UNFILLED",
    ]:
        raise ValueError("Invalid outcome status.")

    if not notes.strip():
        raise ValueError(
            "Please describe the evidence for this outcome."
        )

    with get_connection() as connection:

        connection.execute("BEGIN IMMEDIATE")

        trade = connection.execute(
            """
            SELECT *
            FROM trades
            WHERE id = ?
            """,
            (trade_id,),
        ).fetchone()

        if trade is None:
            raise ValueError("Trade not found.")

        if trade["archived_at"] is not None:
            raise ValueError(
                "Archived research trades cannot have their outcomes changed."
            )

        if trade["status"] != "PENDING":
            raise ValueError(
                "Only pending trades can be resolved "
                "using this form."
            )

        if new_status == "TARGET":
            realized_r = (
                abs(trade["target"] - trade["entry"])
                / abs(trade["entry"] - trade["stop"])
            )

        elif new_status == "STOP":
            realized_r = -1.0

        else:
            # TIMEOUT needs an exit price to calculate R.
            # UNFILLED has no realized trade return.
            realized_r = None

        connection.execute(
            """
            UPDATE trades
            SET status = ?,
                outcome_source = ?,
                realized_r = ?
            WHERE id = ?
            """,
            (
                new_status,
                "USER_REPORTED",
                realized_r,
                trade_id,
            ),
        )

        connection.execute(
            """
            INSERT INTO journal_audit (
                trade_id,
                changed_at,
                previous_status,
                new_status,
                evidence_source,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                utc_now(),
                "PENDING",
                new_status,
                "USER_REPORTED",
                notes,
            ),
        )


# ============================================================
# 4. THEME AND BACKGROUND
# ============================================================

def apply_theme(display_mode):

    if display_mode == "Mobile":
        max_width = "520px"
    elif display_mode == "Desktop":
        max_width = "1450px"
    else:
        max_width = "1450px"

    # The approved light palette is applied centrally by btcusd_theme.py.
    # Do not inject the old dark-mode CSS: it overrides text and card contrast.
    st.markdown(
        f"<style>.block-container {{max-width:{max_width};"
        "padding-top:1.5rem;padding-bottom:3rem;}}</style>",
        unsafe_allow_html=True,
    )


# ============================================================
# 5. DISPLAY MODE
# ============================================================

def render_display_mode():

    if "display_mode" not in st.session_state:
        st.session_state.display_mode = "Desktop"

    return st.radio(
        "Display mode",
        options=["Desktop", "Mobile"],
        horizontal=True,
        key="display_mode",
        help=(
            "Auto follows responsive browser layout. "
            "Mobile uses a narrow single-column dashboard."
        ),
    )


def make_columns(count, display_mode):

    if display_mode == "Mobile":
        return None

    return st.columns(count)


# ============================================================
# 6. HEADER
# ============================================================

def render_header():

    st.markdown(
        """
        <div class="research-banner">
            <h1>BTCUSD Research</h1>
            <div class="research-subtitle">
                Delta Exchange · Multi-timeframe research
                · Research only · No live orders
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# 7. SCREENSHOT WORKSPACE
# ============================================================

def screenshot_workspace():

    

    st.caption("Select four BTCUSD charts together (15m, 1H, 3H, 4H).")

    uploaded_files = st.file_uploader(
        "Select BTCUSD screenshots",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="btcusd_screenshot_upload",
    )

    if not uploaded_files:
        restored = st.session_state.pop("phase6_restore_screenshots", None)
        if isinstance(restored, list) and restored:
            st.session_state["phase6_screenshot_cache"] = [
                dict(shot) for shot in restored
            ]
            st.session_state["phase6_use_cached_screenshots"] = True

        if st.session_state.get("phase6_use_cached_screenshots"):
            cached = st.session_state.get("phase6_screenshot_cache")
            if isinstance(cached, list) and cached:
                st.info("Previous screenshots preserved in this session.")
                return [dict(shot) for shot in cached]

        st.session_state.pop("phase6_screenshot_cache", None)
        st.session_state.pop("phase6_use_cached_screenshots", None)
        st.info("Upload screenshots to begin.")
        return []

    if len(uploaded_files) > MAX_SCREENSHOTS:
        st.error(
            f"Maximum {MAX_SCREENSHOTS} screenshots allowed."
        )
        return []

    screenshots = []

    for uploaded_file in uploaded_files:

        image_bytes = uploaded_file.getvalue()

        if not image_bytes:
            continue

        screenshots.append(
            {
                "name": uploaded_file.name,
                "bytes": image_bytes,
                "sha256": hashlib.sha256(
                    image_bytes
                ).hexdigest(),
            }
        )

    st.session_state["phase6_use_cached_screenshots"] = False
    st.session_state["phase6_screenshot_cache"] = [
        dict(shot) for shot in screenshots
    ]
    st.success(
        f"{len(screenshots)} screenshot(s) uploaded."
    )

    with st.expander(
        "Preview screenshots",
        expanded=False,
    ):
        for screenshot in screenshots:
            st.image(
                screenshot["bytes"],
                caption=screenshot["name"],
                use_container_width=True,
            )

    return screenshots


# ============================================================
# 8. SYSTEM STATUS
# ============================================================

def render_system_status(screenshots, display_mode):

    st.subheader("System Status")

    values = [
        ("Screenshots", str(len(screenshots))),
        ("OCR", "Ready to extract" if screenshots else "Awaiting images"),
        ("Strategy", "Source required"),
        ("Live orders", "Disabled"),
    ]

    columns = make_columns(4, display_mode)

    if columns is None:
        for label, value in values:
            st.metric(label, value)
    else:
        for column, (label, value) in zip(
            columns, values
        ):
            with column:
                st.metric(label, value)


# ============================================================
# 9. FOUR RESEARCH OUTPUTS
# ============================================================

def render_research_outputs():
    st.subheader("Research Outputs")
    tabs = st.tabs(["Signal", "Explanation", "Guidance", "Pending Trade Tracker"])
    result = st.session_state.get("last_strategy_decision")
    with tabs[0]:
        if result:
            st.success(f"{result['signal']} | {result['strategy'] or 'No eligible setup'}")
            st.dataframe(pd.DataFrame([result]), hide_index=True, use_container_width=True)
        else:
            st.warning("NOT EVALUATED — see Table 4 for missing inputs.")
    with tabs[1]:
        st.write(result["reason"] if result else "No valid evaluation yet.")
    with tabs[2]:
        st.info("Research only. A historical candle match is not exchange verification. "
                "Limit fills and exits are modeled from OHLC, not actual exchange executions.")
    with tabs[3]:
        with get_connection() as conn:
            setup_database(conn)
            pending = pd.read_sql_query(
                "SELECT direction,strategy,entry,stop,target,status,updated_at FROM research_setups WHERE status IN ('PENDING','FILLED') ORDER BY created_at DESC",
                conn)
        if pending.empty:
            st.info("No active modeled research setups.")
        else:
            st.dataframe(pending,hide_index=True,use_container_width=True)


# ============================================================
# 10. TRADE JOURNAL STATISTICS
# ============================================================

def calculate_journal_stats(trades):

    total = len(trades)

    wins = sum(
        trade["status"] == "TARGET"
        for trade in trades
    )

    losses = sum(
        trade["status"] == "STOP"
        for trade in trades
    )

    pending = sum(
        trade["status"] == "PENDING"
        for trade in trades
    )

    resolved = wins + losses

    win_rate = (
        100 * wins / resolved
        if resolved
        else None
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": win_rate,
    }


# ============================================================
# 11. TRADE JOURNAL DISPLAY
# ============================================================

def render_trade_journal(display_mode):

    st.subheader("Trade Journal")

    st.caption(
        "Persistent research journal. "
        "Manual entries are not strategy-generated signals."
    )

    trades = [t for t in load_trades() if t.get("archived_at") is None]
    stats = calculate_journal_stats(trades)

    win_rate_text = (
        f"{stats['win_rate']:.1f}%"
        if stats["win_rate"] is not None
        else "—"
    )

    metrics = [
        ("Total trades", stats["total"]),
        ("Wins", stats["wins"]),
        ("Losses", stats["losses"]),
        ("Win rate", win_rate_text),
    ]

    columns = make_columns(4, display_mode)

    if columns is None:
        for label, value in metrics:
            st.metric(label, value)
    else:
        for column, (label, value) in zip(
            columns, metrics
        ):
            with column:
                st.metric(label, value)

    st.metric(
        "Pending trades",
        stats["pending"],
    )

    st.markdown("#### Trade History")

    if trades:

        display_rows = []

        for trade in trades:

            display_rows.append(
                {
                    "ID": trade["id"],
                    "Created UTC": trade["created_at"],
                    "Direction": trade["direction"],
                    "Entry": trade["entry"],
                    "Stop": trade["stop"],
                    "Target": trade["target"],
                    "Status": trade["status"],
                    "Evidence": trade["outcome_source"],
                    "R": trade["realized_r"],
                    "Notes": trade["notes"],
                }
            )

        st.dataframe(
            pd.DataFrame(display_rows),
            use_container_width=True,
            hide_index=True,
        )

    else:
        st.info(
            "No journal records yet. "
            "The journal will remain empty until "
            "a trade is explicitly recorded."
        )

    st.markdown("#### Add Research Trade Manually")

    st.caption(
        "Optional manual journal entry. "
        "This does not execute or validate a strategy."
    )

    with st.expander(
        "Create manual research entry",
        expanded=False,
    ):

        with st.form("create_manual_trade_form"):

            direction = st.selectbox(
                "Direction",
                ["BUY", "SELL"],
            )

            entry = st.number_input(
                "Entry price",
                min_value=0.01,
                value=75000.0,
                step=1.0,
            )

            stop = st.number_input(
                "Stop price",
                min_value=0.01,
                value=74500.0,
                step=1.0,
            )

            target = st.number_input(
                "Target price",
                min_value=0.01,
                value=75250.0,
                step=1.0,
            )

            notes = st.text_area(
                "Research notes",
                placeholder=(
                    "Why is this trade being recorded?"
                ),
            )

            submitted = st.form_submit_button(
                "Save manual research trade"
            )

        if submitted:

            try:
                trade_id = create_manual_trade(
                    direction=direction,
                    entry=entry,
                    stop=stop,
                    target=target,
                    notes=notes,
                )

                st.success(
                    f"Research trade #{trade_id} saved."
                )

                st.rerun()

            except ValueError as error:
                st.error(str(error))

    st.markdown("#### Update Pending Trade")

    pending_trades = [
        trade for trade in trades
        if trade["status"] == "PENDING"
    ]

    if pending_trades:

        pending_trade = pending_trades[0]

        st.write(
            f"Pending trade #{pending_trade['id']}: "
            f"{pending_trade['direction']}"
        )

        with st.form("resolve_pending_trade_form"):

            outcome = st.selectbox(
                "Reported outcome",
                [
                    "TARGET",
                    "STOP",
                    "TIMEOUT",
                    "UNFILLED",
                ],
            )

            evidence_notes = st.text_area(
                "Evidence / explanation",
                placeholder=(
                    "Describe what you observed. "
                    "This will be marked USER_REPORTED."
                ),
            )

            confirmed = st.form_submit_button(
                "Save reported outcome"
            )

        if confirmed:

            try:
                update_trade_outcome(
                    trade_id=pending_trade["id"],
                    new_status=outcome,
                    notes=evidence_notes,
                )

                st.success(
                    "Outcome recorded as USER_REPORTED."
                )

                st.rerun()

            except ValueError as error:
                st.error(str(error))

    else:
        st.caption(
            "No pending trade to update."
        )

    with st.expander(
        "Journal audit history",
        expanded=False,
    ):

        audit_rows = load_audit()

        if audit_rows:
            st.dataframe(
                pd.DataFrame(audit_rows),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.write("No journal changes recorded.")


# ============================================================
# 12. RESTRICTED OCR — NO FULL-CHART PRICE SCRAPING
# ============================================================

TIMEFRAMES = ("15m", "1h", "3h", "4h")
TF_MINUTES = {"15m": 15, "1h": 60, "3h": 180, "4h": 240}
TESSERACT_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if TESSERACT_PATH.is_file():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)

# OHLC must occur in O/H/L/C order in the chart header.
PRICE = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,7}(?:\.\d+)?)"
OHLC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])O\s*[:=]?\s*" + PRICE
    + r"\s*H\s*[:=]?\s*" + PRICE
    + r"\s*L\s*[:=]?\s*" + PRICE
    + r"\s*C\s*[:=]?\s*" + PRICE,
    re.IGNORECASE,
)
TITLE_TF = re.compile(
    r"\bBTC\s*[/_-]?\s*USD\b[^\n]{0,50}?\b(15\s*m(?:in)?|1\s*h|3\s*h|4\s*h|15|60|180|240)\b",
    re.IGNORECASE,
)
TF_ALIASES = {"15m": "15m", "15min": "15m", "15": "15m",
              "1h": "1h", "60": "1h", "3h": "3h", "180": "3h",
              "4h": "4h", "240": "4h"}


def parse_ohlc(text):
    found = set()
    for match in OHLC_PATTERN.finditer(text):
        try:
            values = tuple(float(v.replace(",", "")) for v in match.groups())
        except ValueError:
            continue
        o, h, l, c = values
        if all(math.isfinite(x) and x > 0 for x in values) and l <= min(o, c) <= max(o, c) <= h:
            found.add(values)
    if len(found) == 1:
        return dict(zip(("open", "high", "low", "close"), next(iter(found))))
    return None


def parse_chart_timeframe(text):
    """Read the timeframe next to BTCUSD/Delta, not toolbar intervals."""
    normalized = re.sub(r"[|•·]", " ", str(text))
    # Tolerate BTCYSD/BTCVSD, OCR punctuation, and "15+ Delta".
    patterns = (
        r"BTC\s*[/_-]?\s*[UYV]?SD",
        r"BTCUSD",
    )
    symbol = r"(?:BTC\s*[/_-]?\s*[UYV]?SD)"
    token = r"(15\s*(?:m(?:in)?)?|1\s*h|3\s*h|4\s*h|60|180|240)"
    for pattern in (
        symbol + r"[^\n]{0,36}?" + token + r"\s*[^A-Za-z0-9\n]{0,8}\s*Delta\b",
        symbol + r"[^\n]{0,22}?" + token + r"\s*[^A-Za-z0-9\n]{0,8}\s*Delta\b",
    ):
        match = re.search(pattern, normalized, re.I)
        if match:
            value = re.sub(r"\s+", "", match.group(1)).lower()
            return TF_ALIASES.get(value)
    return None



def parse_title_interval_without_delta(text):
    """Recover only an interval immediately adjacent to BTCUSD in title OCR.

    This deliberately does not read the toolbar and never trusts an upload
    slot. A missing or ambiguous symbol/interval remains unconfirmed.
    """
    normalized = re.sub(r"[|•·]", " ", str(text))
    symbol = r"BTC\s*[/_-]?\s*[UYV]?SD"
    token = r"(15\s*m(?:in)?|1\s*[hH]|3\s*[hH]|4\s*[hH])"
    matches = re.findall(
        symbol + r"\s*[^A-Za-z0-9\n]{0,5}\s*" + token
        + r"(?=[^A-Za-z0-9]|$)", normalized, re.I)
    timeframes = {TF_ALIASES.get(re.sub(r"\s+", "", item).lower())
                  for item in matches}
    return next(iter(timeframes)) if len(timeframes) == 1 else None

def ocr_variants(crop):
    rgb = crop.convert("RGB")
    for scale in (2, 3):
        enlarged = rgb.resize((rgb.width * scale, rgb.height * scale))
        yield ImageEnhance.Sharpness(ImageEnhance.Contrast(enlarged).enhance(2)).enhance(1.5)


# Delta Exchange screenshot layout: title and OHLC on the same header line.
# Keep timestamps separate: the bottom crosshair label does NOT prove which
# candle the top-left OHLC belongs to.
HEADER_PRICES = re.compile(
    r"[O0]\s*([\d,]+(?:\.\d+)?)\s*H\s*([\d,]+(?:\.\d+)?)"
    r"\s*L\s*([\d,]+(?:\.\d+)?)\s*C\s*([\d,]+(?:\.\d+)?)", re.I
)
TIMEFRAME_HEADER = re.compile(r"BTC\s*USD\s*[·•.\-:]?\s*(15|1h|3h|4h)\s*[·•.\-:]?\s*Delta", re.I)


def read_ohlc_header(text):
    # OCR frequently confuses O with 0/©, L with 1, C with €.
    # Require the chart's Delta label plus H and four ordered prices.
    pattern = re.compile(
        r"Delta\s*[O0©]?\s*(\d{2,3},\d{3}\.\d+)\s*"
        r"H\s*(\d{2,3},\d{3}\.\d+)\s*"
        r"[L1]\s*(\d{2,3},\d{3}\.\d+)\s*"
        r"[C€]?\s*(\d{2,3},\d{3}\.\d+)", re.I)
    for m in pattern.finditer(text):
        vals = [float(v.replace(",", "")) for v in m.groups()]
        o, h, l, c = vals
        if l <= min(o, c) <= max(o, c) <= h:
            return dict(zip(("open", "high", "low", "close"), vals))
    return None


def parse_ohlc_fields(text):
    """Conservative fallback for OCR-damaged header values.

    Never substitute a guessed digit. A corrupted or ambiguous field stays missing.
    """
    text = re.sub(r"\s+", " ", text)
    fields = {}
    for key, pattern in {
        "open": r"(?:Delta\s*)?[O0©]\s*[:=]?\s*",
        "high": r"\bH\s*[:=]?\s*",
        "low": r"\b[L1]\s*[:=]?\s*",
        "close": r"\b[C€]\s*[:=]?\s*",
    }.items():
        marker = re.search(pattern + r"(\d{2,3},\d{3}\.\d+)", text, re.I)
        if marker:
            fields[key] = float(marker.group(1).replace(",", ""))
    if len(fields) == 4:
        o, h, l, c = (fields[k] for k in ("open", "high", "low", "close"))
        if all(math.isfinite(x) and x > 0 for x in (o, h, l, c)) and l <= min(o, c) <= max(o, c) <= h:
            return fields
    return None


def recover_header_high(image, known):
    """Targeted OCR retry for a corrupted H price; never infer a digit from context.

    This fallback applies only to the known Delta 1532x862-style header layout.
    A recovered value remains OCR_UNVERIFIED until independently confirmed.
    """
    w, h = image.size
    # The H price is around x=277..350, y=54..73 in the supplied chart.
    region = image.crop((int(w * .181), int(h * .0626),
                         int(w * .229), int(h * .0847))).convert("RGB")
    scale = 3
    enlarged = region.resize((region.width * scale, region.height * scale))
    gray = ImageOps.grayscale(enlarged)
    binary = gray.point(lambda v: 255 if v > 130 else 0)
    readings = [
        pytesseract.image_to_string(binary, config="--psm 7 -c tessedit_char_whitelist=H0123456789,."),
    ]
    candidates = set()
    for reading in readings:
        raw = re.sub(r"[^0-9.]", "", reading)
        # OCR sometimes changes the thousands comma to a period.
        if raw.count(".") == 2 and len(raw.split(".")[0]) == 2:
            raw = raw.replace(".", "", 1)
        try:
            value = float(raw)
        except ValueError:
            continue
        if (value >= max(known["open"], known["close"], known["low"])
                and value < max(known["open"], known["close"]) * 1.1):
            candidates.add(value)
    return next(iter(candidates)) if len(candidates) == 1 else None



def recover_15m_header_with_consensus(image):
    """Narrow retry for damaged 15m H glyph. Never infer digits from OHLC constraints."""
    w, h = image.size
    # Relative coordinates calibrated on the supplied Delta chart layout.
    header = image.crop((int(w*.137), int(h*.058), int(w*.376), int(h*.090)))
    enlarged = header.resize((header.width*4, header.height*4))
    raw = pytesseract.image_to_string(enlarged, config="--psm 7")
    m = re.search(
        r"^[O0]\s*(\d{2,3},\d{3}\.\d+)\s+H[^\s]*\s+"
        r"L\s*(\d{2,3},\d{3}\.\d+)\s+"
        r"C\s*(\d{2,3},\d{3}\.\d+)", raw.strip(), re.I)
    if not m:
        return None
    o, l, c = (float(v.replace(",", "")) for v in m.groups())
    # Read ONLY the damaged H label using two independently resized
    # thresholded views; require identical exact numeric text.
    high_crop = image.crop((int(w*.1828), int(h*.0626),
                            int(w*.2246), int(h*.0836)))
    votes = []
    for scale in (3, 5):
        binary = ImageOps.grayscale(high_crop.resize((high_crop.width*scale, high_crop.height*scale), Image.Resampling.BICUBIC)).point(lambda p: 255 if p > 90 else 0)
        reading = pytesseract.image_to_string(
            binary, config="--psm 7 -c tessedit_char_whitelist=H0123456789,.")
        match = re.fullmatch(r"\s*H\s*(\d{2,3},\d{3}\.\d+)\s*", reading)
        if match:
            votes.append(float(match.group(1).replace(",", "")))
    if len(votes) != 2 or votes[0] != votes[1]:
        return None
    high = votes[0]
    if not (0 < l <= min(o, c) <= max(o, c) <= high):
        return None
    return {"open": o, "high": high, "low": l, "close": c}



def focused_header_ocr(image):
    """Conservative retry on the single OHLC strip, with two-scale agreement.

    This recovers OCR that merges the chart title into the header.
    It never invents digits or promotes a forming candle to completed.
    """
    w, h = image.size
    crop = image.crop((int(w * .14), int(h * .056),
                       int(w * .42), int(h * .088)))
    votes = []
    for scale in (3, 4):
        enlarged = crop.resize((crop.width * scale, crop.height * scale),
                              Image.Resampling.BICUBIC)
        raw = pytesseract.image_to_string(enlarged, config="--psm 7").strip()
        # The leading 'O' is sometimes read as 0 or 9; it is a field
        # marker only when followed by a complete BTCUSD-style price.
        m = re.search(
            r"(?<![A-Za-z0-9])[O09]\s*(\d{2,3},\d{3}\.\d+)\s*"
            r"H\s*(\d{2,3},\d{3}\.\d+)\s*"
            r"[L1]\s*(\d{2,3},\d{3}\.\d+)\s*"
            r"[C€]\s*(\d{2,3},\d{3}\.\d+)", raw, re.I)
        if not m:
            continue
        values = tuple(float(v.replace(",", "")) for v in m.groups())
        o, high, low, close = values
        if 0 < low <= min(o, close) <= max(o, close) <= high:
            votes.append(values)
    if len(votes) != 2 or votes[0] != votes[1]:
        return None
    return dict(zip(("open", "high", "low", "close"), votes[0]))



def toolbar_header_field_consensus(image):
    """Targeted Delta OHLC header recovery.

    Uses complete OHLC sequences instead of overlapping field boxes.
    Never fills missing digits using historical data.
    """
    w, h = image.size

    if w < 1000 or h < 500:
        return None

    # Delta screenshot header, scaled from 1535x861 layout.
    crop = image.crop((
        round(195 * w / 1535),
        round(47 * h / 861),
        round(535 * w / 1535),
        round(83 * h / 861),
    )).convert("RGB")

    pattern = re.compile(
        r"[O0©]\s*[:=]?\s*(\d{2,3}[,.]\d{3}\.\d+)"
        r"\s*H\s*[:=]?\s*(\d{2,3}[,.]\d{3}\.\d+)"
        r"\s*[L1]\s*[:=]?\s*(\d{2,3}[,.]\d{3}\.\d+)"
        r"\s*[C€]\s*[:=]?\s*(\d{2,3}[,.]\d{3}\.\d+)",
        re.IGNORECASE,
    )

    readings = []

    for scale in (3, 4, 5):
        enlarged = crop.resize(
            (crop.width * scale, crop.height * scale),
            Image.Resampling.BICUBIC,
        )

        variants = (
            ImageEnhance.Contrast(enlarged).enhance(1.6),
            ImageEnhance.Sharpness(enlarged).enhance(1.8),
        )

        for variant in variants:
            raw = pytesseract.image_to_string(
                variant,
                config="--psm 7",
            )

            normalized = re.sub(r"\s+", " ", raw)
            match = pattern.search(normalized)

            if match is None:
                continue

            try:
                values = tuple(
                    float(value.replace(",", ""))
                    for value in match.groups()
                )
            except ValueError:
                continue

            o, high, low, close = values

            if not (
                all(math.isfinite(v) and v > 0 for v in values)
                and low <= min(o, close)
                and max(o, close) <= high
            ):
                continue

            readings.append(values)

    unique = set(readings)

    # Require at least two matching OCR passes.
    # Reject conflicting complete readings.
    if len(unique) != 1 or len(readings) < 2:
        return None

    return dict(zip(
        ("open", "high", "low", "close"),
        next(iter(unique)),
    ))



def recover_3h_header_evidence(image):
    """3h-only retry: separately read H, because the chart crosshair obscures its label.

    Do not use historical candles, other screenshot values, or inferred digits.
    Two independently scaled complete readings must agree.
    """
    w, h = image.size
    header = image.crop((int(w * .135), int(h * .055),
                         int(w * .41), int(h * .092)))
    high_crop = image.crop((
        round(280 * w / 1535), round(54 * h / 862),
        round(354 * w / 1535), round(74 * h / 862),
    ))
    high_zoom = high_crop.resize((high_crop.width * 4, high_crop.height * 4))
    high_text = re.sub(r"\s+", "", pytesseract.image_to_string(
        high_zoom, config="--psm 7"))
    high = re.search(r"H(\d{2}),(\d{3})\.(\d+)", high_text, re.I)
    if not high:
        return None

    votes = []
    for scale in (3, 5):
        zoom = header.resize((header.width * scale, header.height * scale))
        raw = pytesseract.image_to_string(zoom, config="--psm 7")
        clean = re.sub(r"\s+", "", raw)
        open_match = re.search(r"[O0](\d{2}),(\d{3})\.(\d+)", clean, re.I)
        low_match = re.search(r"L(\d{2}),(\d{3})\.(\d+)", clean, re.I)
        close_match = re.search(r"C(\d{2}),(\d{3})\.(\d+)", clean, re.I)
        if not all((open_match, low_match, close_match)):
            continue
        groups = (open_match.groups(), high.groups(),
                  low_match.groups(), close_match.groups())
        values = tuple(float(a + b + "." + c) for a, b, c in groups)
        o, hi, lo, cl = values
        if all(math.isfinite(v) and v > 0 for v in values) and lo <= min(o, cl) <= max(o, cl) <= hi:
            votes.append(values)
    if len(votes) != 2 or votes[0] != votes[1]:
        return None
    return dict(zip(("open", "high", "low", "close"), votes[0]))



def recover_damaged_3h_4h_header(image, timeframe):
    """Layout-specific OCR retries; never repair a missing price digit by inference."""
    if timeframe not in ("3h", "4h"):
        return None
    w, h = image.size
    if not (1450 <= w <= 1600 and 800 <= h <= 920):
        return None

    def scaled_box(coords):
        x1, y1, x2, y2 = coords
        return (round(x1*w/1535), round(y1*h/862),
                round(x2*w/1535), round(y2*h/862))

    header = image.crop(scaled_box((205, 50, 545, 80) if timeframe == "3h" else (205, 48, 545, 82)))
    header_texts = []
    for scale in ((3, 4) if timeframe == "3h" else (4, 5)):
        z = header.resize((header.width*scale, header.height*scale),
                          Image.Resampling.BICUBIC)
        header_texts.append(pytesseract.image_to_string(z, config="--psm 7"))

    number = r"(\d{2})\s*[, ]\s*(\d{3})\s*\.\s*(\d+)"
    def field(text, label):
        m = re.search(label + r"\s*" + number, text, re.I)
        return float("".join(m.groups()[:2]) + "." + m.group(3)) if m else None

    def single(values):
        unique = {v for v in values if v is not None}
        return next(iter(unique)) if len(unique) == 1 else None

    opens = [field(t, r"[O0]") for t in header_texts]
    closes = [field(t, r"C") for t in header_texts]
    if timeframe == "3h":
        lows = [field(t, r"L") for t in header_texts]
        # H is obscured by a crosshair; isolate its numeric region.
        box = scaled_box((296, 55, 358, 76))
        missing = "high"
    else:
        highs = [field(t, r"H") for t in header_texts]
        # L is partly obscured; a tighter crop can read the complete number.
        box = scaled_box((355, 55, 435, 75))
        missing = "low"

    crop = image.crop(box)
    recovered_votes = []
    configs = ("--psm 6", "--psm 7") if timeframe == "4h" else ("--psm 7",)
    scales = (5,) if timeframe == "4h" else (4, 5)
    for scale in scales:
        z = crop.resize((crop.width*scale, crop.height*scale),
                        Image.Resampling.BICUBIC)
        for config in configs:
            raw = pytesseract.image_to_string(z, config=config)
            m = re.search(number, raw)
            if m:
                recovered_votes.append(float("".join(m.groups()[:2]) + "." + m.group(3)))
            elif timeframe == "4h":
                # On this Delta layout, the 4h L64,621.5 glyphs overlap:
                # OCR can split the *visible* 64 and 621.5 as "64 621.5".
                # Require both full numeric groups in the same LOW-only crop.
                # Never infer the missing digits from other OHLC prices.
                split_low = re.search(
                    r"(?<!\d)(\d{2})\s+(\d{3})\s*\.\s*(\d+)(?!\d)", raw
                )
                if split_low:
                    recovered_votes.append(float("".join(split_low.groups()[:2]) + "." + split_low.group(3)))

    if len(recovered_votes) < 2 or len(set(recovered_votes)) != 1:
        return None
    o, c = single(opens), single(closes)
    if timeframe == "3h":
        hi, lo = recovered_votes[0], single(lows)
    else:
        hi, lo = single(highs), recovered_votes[0]
    if any(v is None or not math.isfinite(v) or v <= 0 for v in (o, hi, lo, c)):
        return None
    if not (lo <= min(o, c) <= max(o, c) <= hi):
        return None
    return {"open": o, "high": hi, "low": lo, "close": c}




def recover_4h_low_with_consensus(image):
    """Read the 4h LOW directly from its chart-header pixels.

    Require two agreeing complete price readings from independent crop/scale
    variants. Never derive LOW from other prices or historical candles.
    """
    w, h = image.size
    if not (1450 <= w <= 1600 and 800 <= h <= 920):
        return None
    def box(coords):
        x1, y1, x2, y2 = coords
        return (round(x1*w/1535), round(y1*h/862),
                round(x2*w/1535), round(y2*h/862))
    header = image.crop(box((205, 48, 545, 82)))
    header_votes = []
    number = r"(\d{2})\s*[, ]\s*(\d{3})\s*\.\s*(\d{1,2})"
    for scale in (3, 4, 5):
        zoom = header.resize((header.width*scale, header.height*scale),
                             Image.Resampling.BICUBIC)
        raw = pytesseract.image_to_string(zoom, config="--psm 7")
        def read(label):
            match = re.search(label + r"\s*" + number, raw, re.I)
            if not match:
                return None
            a, b, d = match.groups()
            return float(a + b + "." + d)
        o, hi, cl = read(r"[O0]"), read(r"H"), read(r"C")
        if all(v is not None for v in (o, hi, cl)):
            header_votes.append((o, hi, cl))
    if len(header_votes) < 2 or len(set(header_votes)) != 1:
        return None
    o, hi, cl = header_votes[0]

    low_votes = []
    for coords in ((355, 55, 435, 75), (353, 54, 436, 77)):
        crop = image.crop(box(coords)).convert("RGB")
        for scale in (4, 5, 6):
            zoom = crop.resize((crop.width*scale, crop.height*scale),
                               Image.Resampling.BICUBIC)
            for variant in (zoom, ImageEnhance.Contrast(zoom).enhance(1.5)):
                for psm in (6, 7):
                    raw = pytesseract.image_to_string(variant, config=f"--psm {psm}")
                    # The label L64,621.5 may be rendered as '64 621.5'.
                    # Both groups must be visible in the LOW-only crop.
                    match = re.search(r"(?<!\d)(\d{2})\s*[, ]\s*(\d{3})\s*\.\s*(\d{1,2})(?!\d)", raw)
                    if match:
                        a, b, d = match.groups()
                        low_votes.append(float(a + b + "." + d))
    if len(low_votes) < 2 or len(set(low_votes)) != 1:
        return None
    lo = low_votes[0]
    if not (all(math.isfinite(v) and v > 0 for v in (o, hi, lo, cl))
            and lo <= min(o, cl) <= max(o, cl) <= hi):
        return None
    return {"open": o, "high": hi, "low": lo, "close": cl}


def detect_active_toolbar_timeframe(image):
    """Read the selected orange timeframe from the fixed Delta chart toolbar.

    Only returns a timeframe when exactly one interval has a strong orange
    highlight. Does not infer OHLC, timestamps, or candle completion.
    """
    from PIL import ImageStat
    w, h = image.size
    if w < 1000 or h < 500:
        return None
    # Coordinates are relative to the supplied 1535x861 Delta layout.
    # Restrict to the interval labels; do not inspect orange chart candles.
    centers = {"15m": 710, "1h": 738, "3h": 761, "4h": 785}
    counts = {}
    for tf, cx in centers.items():
        box = image.crop((
            round((cx - 8) * w / 1535), round(13 * h / 861),
            round((cx + 10) * w / 1535), round(34 * h / 861),
        )).convert("RGB")
        orange = 0
        for r, g, b in box.getdata():
            if r > 150 and g > 50 and r > 1.35 * g and b < .9 * g:
                orange += 1
        counts[tf] = orange
    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    if ordered[0][1] >= 18 and ordered[1][1] <= 5:
        return ordered[0][0]
    return None


@st.cache_data(show_spinner=False)
def restricted_ocr(image_bytes, algorithm_version="phase3_3h_4h_header_recovery_v7"):
    """Extract chart-header evidence; never infer a completed/current candle."""
    with Image.open(io.BytesIO(image_bytes)) as loaded:
        image = ImageOps.exif_transpose(loaded).convert("RGB")
    w, h = image.size
    if w < 500 or h < 250:
        return {"symbol": None, "timeframe": None, "ohlc": {},
                "issues": ["Screenshot resolution too low for reliable chart-header OCR"],
                "raw_header": "", "timestamp_utc": None, "candle_closed": None,
                "crosshair_status": "UNKNOWN"}

    crops = (
        image.crop((int(w*.02), int(h*.025), int(w*.72), int(h*.13))),
        image.crop((int(w*.02), int(h*.045), int(w*.56), int(h*.105))),
        image.crop((int(w*.02), int(h*.04), int(w*.23), int(h*.11))),
    )
    readings = []
    for crop in crops:
        for variant in ocr_variants(crop):
            for psm in (6, 7):
                readings.append(pytesseract.image_to_string(variant, config=f"--psm {psm}"))

    # Independent, narrow chart-title pass. Full-width crops can blend toolbar
    # intervals and volume labels into the header and corrupt otherwise clear text.
    title_crop = image.crop((int(w*.038), int(h*.055),
                             int(w*.405), int(h*.091)))
    for scale in (3, 4):
        zoom = title_crop.resize((title_crop.width*scale, title_crop.height*scale))
        readings.append(pytesseract.image_to_string(zoom, config="--psm 7"))
    # Toolbar active interval is a corroborating hint only, not a substitute
    # for the chart header or an OCR-unreadable title.
    toolbar = image.crop((int(w*.415), 0, int(w*.56), int(h*.05)))
    toolbar_text = pytesseract.image_to_string(
        toolbar.resize((toolbar.width*3, toolbar.height*3)), config="--psm 7")
    timeframe_votes = {tf for text in readings if (tf := parse_chart_timeframe(text))}
    # Targeted title-only recovery: OCR sometimes loses the trailing "Delta"
    # even when BTCUSD and its adjacent interval are legible. Require the
    # same title interval in BOTH separately resized narrow title crops.
    # Never use the toolbar alone or the user's slot assignment as proof.
    title_recovery_votes = []
    for scale in (3, 4):
        narrow_title = image.crop((int(w*.038), int(h*.055),
                                   int(w*.245), int(h*.091)))
        zoom = ImageEnhance.Contrast(narrow_title.resize(
            (narrow_title.width*scale, narrow_title.height*scale),
            Image.Resampling.BICUBIC)).enhance(1.6)
        title_reading = pytesseract.image_to_string(zoom, config="--psm 7")
        title_recovery_votes.append(parse_title_interval_without_delta(title_reading))
    if (title_recovery_votes[0] is not None
            and title_recovery_votes[0] == title_recovery_votes[1]):
        recovered_tf = title_recovery_votes[0]
        if not timeframe_votes or timeframe_votes == {recovered_tf}:
            timeframe_votes.add(recovered_tf)
        else:
            timeframe_votes.add(recovered_tf)  # conflict remains rejected
    # Independent selected-toolbar color fallback for OCR-corrupted titles.
    # Require a unique orange interval and reject disagreement with any
    # successfully parsed chart-title timeframe.
    toolbar_active = detect_active_toolbar_timeframe(image)
    if toolbar_active is not None:
        if not timeframe_votes or timeframe_votes == {toolbar_active}:
            timeframe_votes.add(toolbar_active)
        else:
            timeframe_votes.add(toolbar_active)  # conflict => ambiguous
    # OCR can read 3h as "3p"; accept that *only* if the orange
    # toolbar interval also identifies 3h. Do not infer from upload order.
    if not timeframe_votes and re.search(r"BTC\s*USD[^\n]{0,24}3p\s*[-·.]?\s*Delta",
                                          "\n".join(readings), re.I):
        if re.search(r"\b3h\b", toolbar_text, re.I):
            timeframe_votes.add("3h")
    candidates = []
    for text in readings:
        candidate = read_ohlc_header(text) or parse_ohlc(text) or parse_ohlc_fields(text)
        if candidate:
            candidates.append(tuple(candidate[k] for k in ("open", "high", "low", "close")))
    distinct = set(candidates)
    # Independent focused retry: only accept identical full OHLC across
    # two scales; reject conflicts with the existing parser.
    focused = focused_header_ocr(image)
    if focused is not None:
        focused_tuple = tuple(focused[k] for k in ("open", "high", "low", "close"))
        if not distinct or distinct == {focused_tuple}:
            distinct.add(focused_tuple)
        else:
            distinct.add(focused_tuple)  # conflicting evidence => rejected below
    # Narrow four-field retry for the Delta toolbar layout. Preserve conflicts.
    if not distinct and len(timeframe_votes) == 1:
        recovered = toolbar_header_field_consensus(image)
        if recovered is not None:
            distinct.add(tuple(recovered[k] for k in ("open", "high", "low", "close")))
    # Tested on the supplied 2026-11-16 3h screenshot.
    # Only use this targeted recovery for independently confirmed 3h charts.
    if not distinct and timeframe_votes == {"3h"}:
        recovered_3h = recover_3h_header_evidence(image)
        if recovered_3h is not None:
            distinct.add(tuple(recovered_3h[k] for k in
                               ("open", "high", "low", "close")))
    # Targeted retries for OCR-corrupted 3h H and 4h L labels.
    # Require complete numeric readings from the screenshot; never infer
    # a missing digit from historical data or candle constraints.
    if not distinct and len(timeframe_votes) == 1:
        only_tf = next(iter(timeframe_votes))
        if only_tf in ("3h", "4h"):
            recovered = recover_damaged_3h_4h_header(image, only_tf)
            if recovered is not None:
                distinct.add(tuple(recovered[k] for k in
                                   ("open", "high", "low", "close")))
    # Additional 4h-only LOW crop consensus when the original recovery fails.
    if not distinct and timeframe_votes == {"4h"}:
        recovered_4h = recover_4h_low_with_consensus(image)
        if recovered_4h is not None:
            distinct.add(tuple(recovered_4h[k] for k in
                               ("open", "high", "low", "close")))
    # 15m H often appears as 73,59Z0: retry the isolated glyph instead
    # of guessing the corrupted character or borrowing a historical price.
    if not distinct and timeframe_votes == {"15m"}:
        recovered = recover_15m_header_with_consensus(image)
        if recovered is not None:
            distinct.add(tuple(recovered[k] for k in ("open", "high", "low", "close")))
            
    # Diagnostic evidence only — does not change accepted OHLC.
    ohlc_debug = {
        "title_timeframe_votes": sorted(timeframe_votes),
        "toolbar_active": toolbar_active,
        "complete_ohlc_candidates": [
            list(candidate) for candidate in sorted(distinct)
        ],
        "focused_header_result": focused,
        "field_consensus_attempted": (
            len(distinct) == 0 and len(timeframe_votes) == 1
        ),
    }

    issues = []
    if len(timeframe_votes) != 1:
        issues.append("Chart timeframe ambiguous or unreadable")
    if len(distinct) > 1:
        issues.append("Conflicting OHLC readings; no values accepted")
    elif not distinct:
        issues.append("OHLC header unreadable; no values accepted")
    values = dict(zip(("open", "high", "low", "close"), next(iter(distinct)))) if len(distinct) == 1 else {}
    # Chart-local display clock (24-hour HH:MM:SS UTC±H), NOT candle time.
    # Dedicated lower-right crop; require agreement between two independent scales.
    clock_crop = image.crop((int(w*.83), int(h*.965), int(w*.945), h))
    clock_readings = []
    clock_votes = []
    for scale in (3, 4):
        enlarged = clock_crop.resize((clock_crop.width*scale, clock_crop.height*scale))
        reading = pytesseract.image_to_string(enlarged, config="--psm 7").strip()
        clock_readings.append(reading)
        cleaned = reading.replace("−", "-").replace("–", "-").replace("—", "-")
        match = re.search(
            r"(?<!\d)(\d{1,2}):(\d{2}):(\d{2})\s*UTC\s*([+-])\s*(\d{1,2})(?!\d)",
            cleaned, re.I)
        if match:
            hour, minute, second, sign, offset = match.groups()
            if (int(hour) < 24 and int(minute) < 60 and int(second) < 60
                    and int(offset) <= 14):
                clock_votes.append(
                    (f"{int(hour):02d}:{minute}:{second}",
                     (1 if sign == "+" else -1)*int(offset)))
    chart_clock = clock_votes[0] if len(clock_votes) == 2 and clock_votes[0] == clock_votes[1] else None
    # Crosshair date is evidence of a selected chart position, NOT proof that
    # top-left OHLC belongs to that candle.
    bottom = image.crop((int(w*.39), int(h*.946), int(w*.59), int(h*.988)))
    bottom_text = pytesseract.image_to_string(
        bottom.resize((bottom.width*3, bottom.height*3)), config="--psm 7")
    historical_label = bool(re.search(
        r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"['’]?\s*\d{2,4}\s+\d{1,2}:\d{2}",
        bottom_text, re.I))
    # A countdown at the right-hand price label means the displayed live
    # candle is forming; it does not prove the header belongs to that candle.
    # Price-label countdown can appear at different vertical positions.
    # Search the full rightmost price scale in overlapping strips rather than
    # assuming it always lies between 60% and 71% of image height.
    edge_readings = []
    for top_fraction, bottom_fraction in ((.08, .42), (.35, .68), (.61, .93)):
        crop = image.crop((int(w * .927), int(h * top_fraction),
                           int(w * .997), int(h * bottom_fraction)))
        if crop.width < 1 or crop.height < 1:
            continue
        enlarged = crop.resize((crop.width * 3, crop.height * 3))
        for psm in (6, 11):
            edge_readings.append(pytesseract.image_to_string(
                enlarged, config=f"--psm {psm}"))
    # Pure-Pillow connected-component detection, with no cv2 dependency.
    # The earlier OpenCV-only branch was silently skipped on Windows installs
    # without cv2, and row-band cropping included unrelated chart labels.
    red_label_readings = []
    red_label_debug = []
    try:
        from collections import deque
        x_start = int(w * .84)
        band = image.crop((x_start, 0, w, h)).convert("RGB")
        bw, bh = band.size
        pixels = band.load()
        mask = bytearray(bw * bh)
        for yy in range(bh):
            for xx in range(bw):
                r, g, b = pixels[xx, yy]
                if r > 175 and r > g * 1.35 and r > b * 1.20:
                    mask[yy * bw + xx] = 1
        components = []
        for pos in range(len(mask)):
            if not mask[pos]:
                continue
            mask[pos] = 0
            queue = deque([pos])
            area = 0
            min_x, max_x, min_y, max_y = bw, 0, bh, 0
            while queue:
                pixel = queue.popleft()
                yy, xx = divmod(pixel, bw)
                area += 1
                min_x, max_x = min(min_x, xx), max(max_x, xx)
                min_y, max_y = min(min_y, yy), max(max_y, yy)
                for neighbor in (pixel - 1, pixel + 1, pixel - bw, pixel + bw):
                    if neighbor < 0 or neighbor >= len(mask):
                        continue
                    ny, nx = divmod(neighbor, bw)
                    if abs(nx - xx) + abs(ny - yy) != 1 or not mask[neighbor]:
                        continue
                    mask[neighbor] = 0
                    queue.append(neighbor)
            if area >= 180 and max_x - min_x >= 35 and max_y - min_y >= 18:
                components.append((area, x_start + min_x, min_y,
                                   x_start + max_x, max_y))
        components.sort(reverse=True)
        for area, x0, y0, x1, y1 in components[:6]:
            crop = image.crop((max(0, x0 - 6), max(0, y0 - 5),
                               min(w, x1 + 8), min(h, y1 + 8)))
            crop = crop.resize((crop.width * 4, crop.height * 4))
            for psm in (6, 11):
                red_label_readings.append(pytesseract.image_to_string(
                    crop, config=f"--psm {psm}"))
            red_label_debug.append(
                f"red label x={x0}:{x1}, y={y0}:{y1}, area={area}")
        if not components:
            red_label_debug.append("No suitable connected red label found")
    except (OSError, ValueError, pytesseract.TesseractError) as exc:
        red_label_debug.append(
            f"Red-label OCR error: {type(exc).__name__}: {exc}")
    edge_readings.extend(red_label_readings)
    edge_text = " | ".join(edge_readings)
    countdown_candidates = parse_countdown_evidence(edge_text)
    # Require a countdown that is temporally consistent with this timeframe.
    # A random price-axis label must never be treated as a countdown.
    tf_for_countdown = next(iter(timeframe_votes)) if len(timeframe_votes) == 1 else None
    countdown_seconds = next((
        seconds for seconds in countdown_candidates
        if countdown_consistent_with_interval(
            seconds, chart_clock[0] if chart_clock else None,
            chart_clock[1] if chart_clock else None, tf_for_countdown)
    ), None)
    countdown_visible = countdown_seconds is not None
    crosshair_status = "HISTORICAL_CROSSHAIR_VISIBLE" if historical_label else "NOT_CONFIRMED"
    if historical_label:
        issues.append("Historical crosshair date visible; header OHLC association unproven")
    if countdown_visible:
        issues.append("Live candle countdown visible; current candle is forming")
    # Crosshair date labels and chart clock alone do not establish the header
    # candle's open timestamp or whether that candle is complete.
    issues.append("Header candle timestamp/completion cannot be verified from screenshot alone")
    # Hybrid OCR: preserve individually supported prices even if a sibling
    # field is unreadable. Never fill from history or OHLC arithmetic.
    hybrid = hybrid_extract_fields(image_bytes)
    if hybrid["ohlc"]:
        values = dict(hybrid["ohlc"])
        issues = [msg for msg in issues if not msg.startswith(
            ("OHLC header unreadable", "Conflicting OHLC readings"))]
    if hybrid["uncertain"]:
        issues.append("OCR uncertain fields: " + ", ".join(hybrid["uncertain"]))
    ohlc_debug["hybrid_field_votes"] = hybrid["votes"]
    return {
        "symbol": "BTCUSD" if len(timeframe_votes) == 1 else None,
        "timeframe": next(iter(timeframe_votes)) if len(timeframe_votes) == 1 else None,
        "ohlc": values,
        "issues": issues,
        "raw_header": "\\n--- OCR PASS ---\\n".join(readings),
        "timestamp_utc": None,
        "candle_closed": None,
        "crosshair_status": crosshair_status,
        "crosshair_label_ocr": bottom_text.strip(),
        "live_countdown_ocr": edge_text.strip(),
        "red_label_debug": "; ".join(red_label_debug),
        "red_label_ocr": " | ".join(red_label_readings),
        "ocr_pass_count": len(readings),
        "chart_clock_ocr": " | ".join(clock_readings),
        "countdown_visible": countdown_visible,
        "ohlc_debug": ohlc_debug,
        "countdown_seconds": countdown_seconds,
        "countdown_source": "OCR" if countdown_visible else "NONE",
        "chart_clock_local": chart_clock[0] if chart_clock else None,
        "chart_utc_offset_hours": chart_clock[1] if chart_clock else None,
    }


def crosshair_label_consistency(label, utc_offset_hours, candidate_utc, tf):
    """Check whether a chart-local crosshair label falls in a matched UTC bar.

    The label may denote a position inside the candle rather than its open.
    This is supporting evidence only, not proof of header OHLC association.
    """
    if not label or utc_offset_hours is None or not candidate_utc or tf not in TF_MINUTES:
        return "NOT CHECKED"
    match = re.search(
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"['’]?\s*(\d{2,4})\s+(\d{1,2}):(\d{2})",
        label, re.I)
    if not match:
        return "LABEL UNREADABLE"
    day, month, year, hour, minute = match.groups()
    year = int(year)
    if year < 100:
        year += 2000
    try:
        local = pd.Timestamp(
            year=year, month=list(("jan","feb","mar","apr","may","jun",
                                   "jul","aug","sep","oct","nov","dec")).index(month.lower())+1,
            day=int(day), hour=int(hour), minute=int(minute), tz="UTC")
        position_utc = local - pd.Timedelta(hours=int(utc_offset_hours))
        start = pd.Timestamp(candidate_utc)
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        end = start + pd.Timedelta(minutes=TF_MINUTES[tf])
        return ("LABEL WITHIN MATCHED CANDLE (SUPPORTING EVIDENCE)"
                if start <= position_utc < end
                else "LABEL CONFLICTS WITH MATCHED CANDLE")
    except (ValueError, TypeError, OverflowError):
        return "LABEL UNREADABLE"


def parse_countdown_evidence(ocr_text):
    """Extract plausible live-candle countdown durations, never a chart clock."""
    text = (ocr_text or "").replace("：", ":").replace(";", ":")
    candidates = []
    for match in re.finditer(r"(?<!\d)(\d{1,2}):(\d{2}):(\d{2})(?!\d)", text):
        h, m, sec = map(int, match.groups())
        if h < 24 and m < 60 and sec < 60:
            candidates.append(h * 3600 + m * 60 + sec)
    for match in re.finditer(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])", text):
        m, sec = map(int, match.groups())
        if m < 60 and sec < 60:
            candidates.append(m * 60 + sec)
    return sorted(set(candidates))


def countdown_consistent_with_interval(seconds, chart_clock, offset_hours, tf, tolerance=150):
    """Cross-check countdown with chart clock and UTC-epoch interval boundary."""
    if not chart_clock or offset_hours is None or tf not in TF_MINUTES:
        return False
    try:
        hour, minute, second = map(int, chart_clock.split(":"))
        utc_second_of_day = ((hour * 3600 + minute * 60 + second)
                             - offset_hours * 3600) % 86400
        interval = TF_MINUTES[tf] * 60
        remaining = interval - (utc_second_of_day % interval)
        return abs(remaining - seconds) <= tolerance
    except (TypeError, ValueError):
        return False


def parse_confirmed_countdown(raw, tf, clock, offset):
    """Manual screenshot transcription is evidence, never an exchange OHLC match.

    Return (seconds, reason). Reject invalid format or a countdown that conflicts
    with the chart clock. This is an explicit fallback when OCR cannot read it.
    """
    raw = str(raw or "").strip()
    if not raw:
        return None, "No manual countdown entered"
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", raw)
    if not match:
        return None, "Use MM:SS or HH:MM:SS"
    a, b, c = match.groups()
    if c is None:
        if int(b) >= 60:
            return None, "Seconds must be below 60"
        seconds = int(a) * 60 + int(b)
    else:
        if int(b) >= 60 or int(c) >= 60:
            return None, "Minutes and seconds must be below 60"
        seconds = int(a) * 3600 + int(b) * 60 + int(c)
    if not countdown_consistent_with_interval(seconds, clock, offset, tf, tolerance=20):
        return None, "Countdown conflicts with chart clock/timeframe (20s tolerance)"
    return seconds, "USER-CONFIRMED SCREENSHOT COUNTDOWN (NOT API VERIFIED)"


def blank_row(tf):
    return {"timeframe": tf, "symbol": "BTCUSD", "open": None,
            "high": None, "low": None, "close": None,
            "candle_open_utc": "", "candle_closed": False,
            "source": "MISSING", "ocr_notes": ""}


def assemble_rows(screenshots, assignments, precomputed=None):
    """Reject duplicate slots and OCR/title mismatches instead of overwriting."""
    rows = {tf: blank_row(tf) for tf in TIMEFRAMES}
    evidence = []
    precomputed = precomputed or {}
    counts = {tf: list(assignments.values()).count(tf) for tf in TIMEFRAMES}
    for file in screenshots:
        tf = assignments.get(file["sha256"])
        result = dict(precomputed.get(file["sha256"]) or restricted_ocr(file["bytes"]))
        result["issues"] = list(result.get("issues", []))
        if tf not in TIMEFRAMES:
            evidence.append({"filename": file["name"], "sha256": file["sha256"],
                             "assigned_timeframe": None, **result})
            continue
        if counts[tf] != 1:
            result["issues"].append(f"Duplicate assignment for {tf}; rejected")
        if result.get("timeframe") and result["timeframe"] != tf:
            result["issues"].append(
                f"Assigned {tf} but OCR title identifies {result['timeframe']}; rejected")
        if result.get("timeframe") is None:
            result["issues"].append("Timeframe not independently confirmed by OCR")
        trusted_slot = (counts[tf] == 1 and result.get("timeframe") == tf)
        if trusted_slot:
            row = rows[tf]
            row.update(result.get("ohlc", {}))
            row["symbol"] = result.get("symbol") or ""
            row["source"] = "OCR_UNVERIFIED" if result.get("ohlc") else "MISSING"
            row["ocr_notes"] = "; ".join(result["issues"])
        evidence.append({"filename": file["name"], "sha256": file["sha256"],
                         "assigned_timeframe": tf, **result})
    return list(rows.values()), evidence


def parse_utc_time(value):
    if not str(value).strip():
        return None
    try:
        value = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
            return None
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def validate_row(row):
    problems = []
    tf = row.get("timeframe")
    if tf not in TIMEFRAMES:
        problems.append("Unknown timeframe")
    if str(row.get("symbol", "")).strip().upper() != "BTCUSD":
        problems.append("Symbol not confirmed as BTCUSD")
    try:
        o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
        if not (all(math.isfinite(x) and x > 0 for x in (o, h, l, c))
                and l <= min(o, c) <= max(o, c) <= h):
            problems.append("Invalid OHLC ordering or nonpositive price")
    except (ValueError, TypeError, KeyError):
        problems.append("Missing or nonnumeric OHLC")
        o = h = l = c = None
    dt = parse_utc_time(row.get("candle_open_utc"))
    if not dt:
        problems.append("Candle OPEN time in UTC not confirmed")
    elif tf in TF_MINUTES:
        minutes = TF_MINUTES[tf]
        # UTC epoch alignment; exchange-specific session anchoring still needs confirmation.
        if int(dt.timestamp()) % (minutes * 60):
            problems.append("UTC timestamp not aligned to nominal timeframe grid")
        if dt > datetime.now(timezone.utc):
            problems.append("Candle timestamp is in the future")
        if bool(row.get("candle_closed")) and dt + timedelta(minutes=minutes) > datetime.now(timezone.utc):
            problems.append("Candle cannot yet be completed")
    if not bool(row.get("candle_closed")):
        problems.append("Completed candle not confirmed")
    if row.get("ocr_notes") and "Conflicting" in str(row["ocr_notes"]):
        problems.append("Unresolved OCR conflict")
    structural = not any("OHLC" in x or "nonnumeric" in x or "Symbol" in x or "timeframe" == x for x in problems)
    return {"timeframe": tf, "ohlc_structural": structural,
            "time_confirmed": dt is not None,
            "ready_for_history_match": not problems,
            "market_verified": False, "problems": problems}


def candle_features(row):
    try:
        o, h, l, c = [float(row[k]) for k in ("open", "high", "low", "close")]
        if not (l <= min(o, c) <= max(o, c) <= h and l > 0):
            return None
        rng = h - l
        body = abs(c - o)
        return {"Timeframe": row["timeframe"],
                "Direction": "Bullish" if c > o else "Bearish" if c < o else "Doji",
                "Body": round(body, 4), "Range": round(rng, 4),
                "Body / range": round(body / rng, 4) if rng else None,
                "Upper wick": round(h - max(o, c), 4),
                "Lower wick": round(min(o, c) - l, 4)}
    except (ValueError, TypeError, KeyError):
        return None


# ============================================================
# 13. OPTIONAL HISTORICAL CANDLES — READ ONLY
# ============================================================

def load_historical_candles(path):
    """Support common exported JSON/CSV OHLC schemas; never fabricate missing candles."""
    if not path.exists():
        return None, f"Historical file not found: {path.name}"
    try:
        if path.suffix.lower() == ".csv":
            frame = pd.read_csv(path)
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                records = payload
            elif isinstance(payload, dict):
                records = next((payload[k] for k in ("candles", "bars", "data", "rows")
                                if isinstance(payload.get(k), list)), None)
            else:
                records = None
            if records is None:
                return None, "Unsupported JSON schema; expected a list of candle records"
            frame = pd.DataFrame(records)
        columns = {str(c).lower().strip(): c for c in frame.columns}
        time_col = next((columns[k] for k in ("timestamp", "time", "datetime", "open_time", "ts", "date")
                         if k in columns), None)
        if time_col is None:
            return None, "Historical file has no recognized timestamp column"
        for name in ("open", "high", "low", "close"):
            if name not in columns:
                return None, f"Historical file missing {name}"
        ts = frame[time_col]
        if pd.api.types.is_numeric_dtype(ts):
            unit = "ms" if pd.to_numeric(ts, errors="coerce").abs().median() > 1e11 else "s"
            times = pd.to_datetime(ts, unit=unit, utc=True, errors="coerce")
        else:
            times = pd.to_datetime(ts, utc=True, errors="coerce")
        result = pd.DataFrame({"time": times})
        for k in ("open", "high", "low", "close"):
            result[k] = pd.to_numeric(frame[columns[k]], errors="coerce")
        result = result.dropna().sort_values("time")
        if result.empty or result["time"].duplicated().any():
            return None, "Historical timestamps are empty or duplicated"
        if not ((result["low"] <= result[["open", "close"]].min(axis=1))
                & (result["high"] >= result[["open", "close"]].max(axis=1))
                & (result["low"] > 0)).all():
            return None, "Historical OHLC values failed consistency checks"
        if (result["time"].diff().dropna() != pd.Timedelta(minutes=15)).any():
            return None, "Historical candles are not a contiguous 15-minute series"
        return result.reset_index(drop=True), None
    except Exception as exc:
        return None, f"Historical file error: {exc}"


def compare_history(rows, history):
    if history is None:
        return [], []
    lookup = history.set_index("time")
    comparisons, problems = [], []
    for row in rows:
        tf = row["timeframe"]
        dt = parse_utc_time(row.get("candle_open_utc"))
        if dt is None or not bool(row.get("candle_closed")):
            continue
        start = pd.Timestamp(dt)
        end = start + pd.Timedelta(minutes=TF_MINUTES[tf])
        subset = lookup.loc[(lookup.index >= start) & (lookup.index < end)]
        if len(subset) != TF_MINUTES[tf] // 15:
            problems.append(f"{tf}: historical candle interval missing or incomplete")
            continue
        observed = {"open": float(subset.iloc[0]["open"]),
                    "high": float(subset["high"].max()),
                    "low": float(subset["low"].min()),
                    "close": float(subset.iloc[-1]["close"])}
        try:
            given = {k: float(row[k]) for k in observed}
            matches = all(abs(given[k] - observed[k]) <= 0.011 for k in observed)
        except (TypeError, ValueError):
            matches = False
        comparisons.append({"Timeframe": tf, "Candle UTC": dt.isoformat(),
                            "Historical OHLC match": "MATCH" if matches else "MISMATCH",
                            "Source": "LOCAL_HISTORICAL_FILE",
                            **{f"History {k}": v for k, v in observed.items()}})
        if not matches:
            problems.append(f"{tf}: screenshot OHLC differs from historical candles")
    return comparisons, problems


# ============================================================
# 13B. DELTA INDIA PUBLIC HISTORICAL DATA (COMPLETED 15M ONLY)
# ============================================================

DELTA_HISTORY_URL = "https://api.india.delta.exchange/v2/history/candles"
SYNC_CACHE_PATH = PROJECT_DIR / "btcusd_delta_15m_cache.json"
SYNC_MIN_BARS = 3200  # Covers 720 hourly ATR observations and 4H warmup.
SYNC_MAX_REQUEST_BARS = 1800  # Delta limits responses to 2000.
SYNC_REFRESH_SECONDS = 60


def _validate_delta_frame(records, end_utc):
    """Validate API response independently of any local historical export."""
    if not isinstance(records, list) or not records:
        raise ValueError("Delta returned no candle records")
    frame = pd.DataFrame(records)
    required = {"time", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("Delta response is missing required candle fields")
    numeric = pd.to_numeric(frame["time"], errors="coerce")
    if numeric.isna().any():
        raise ValueError("Delta response contains invalid timestamps")
    frame["time"] = pd.to_datetime(numeric, unit="s", utc=True, errors="coerce")
    if frame["time"].isna().any() or frame["time"].duplicated().any():
        raise ValueError("Delta response has invalid or duplicated timestamps")
    if (frame["time"].dt.minute % 15 != 0).any() or (frame["time"].dt.second != 0).any():
        raise ValueError("Delta candle timestamps are not 15-minute aligned")
    for field in ("open", "high", "low", "close"):
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    values = frame[["open", "high", "low", "close"]]
    if (not values.notna().all().all()
            or not (values > 0).all().all()
            or not (frame["low"] <= values[["open", "close"]].min(axis=1)).all()
            or not (frame["high"] >= values[["open", "close"]].max(axis=1)).all()):
        raise ValueError("Delta candle prices failed OHLC checks")
    frame = frame[["time", "open", "high", "low", "close"]].sort_values("time")
    frame = frame[frame["time"] + pd.Timedelta(minutes=15) <= end_utc]
    if frame.empty:
        raise ValueError("No completed candles returned")
    if (frame["time"].diff().dropna() != pd.Timedelta(minutes=15)).any():
        raise ValueError("Delta response contains missing candle intervals")
    return frame.reset_index(drop=True)


def _delta_request(start_seconds, end_seconds):
    query = urlencode({"symbol": "BTCUSD", "resolution": "15m",
                       "start": int(start_seconds), "end": int(end_seconds)})
    req = Request(DELTA_HISTORY_URL + "?" + query,
                  headers={"Accept": "application/json", "User-Agent": "BTCUSD-Research/Phase3"})
    with urlopen(req, timeout=15) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("Delta API reported an unsuccessful response")
    return payload.get("result")


def _load_delta_cache():
    if not SYNC_CACHE_PATH.is_file():
        return None
    payload = json.loads(SYNC_CACHE_PATH.read_text(encoding="utf-8"))
    if payload.get("source") != DELTA_HISTORY_URL or payload.get("symbol") != "BTCUSD":
        raise ValueError("Historical cache source/symbol mismatch")
    records = payload.get("candles", [])
    if not records:
        return None
    return _validate_delta_frame(records, pd.Timestamp.now(tz="UTC"))


def _save_delta_cache(frame):
    records = []
    for row in frame.itertuples(index=False):
        records.append({"time": int(row.time.timestamp()), "open": row.open,
                        "high": row.high, "low": row.low, "close": row.close})
    payload = {"source": DELTA_HISTORY_URL, "symbol": "BTCUSD",
               "resolution": "15m", "candles": records}
    temp = SYNC_CACHE_PATH.with_name(SYNC_CACHE_PATH.name + ".tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, SYNC_CACHE_PATH)


@st.cache_data(ttl=SYNC_REFRESH_SECONDS, show_spinner="Syncing Delta 15m candles...")
def synchronize_delta_history():
    """Fetch enough warm-up candles and append verified completed bars.

    Fail closed: an incomplete or stale cache is not passed to the strategy.
    """
    now = pd.Timestamp.now(tz="UTC")
    completed_end = now.floor("15min")  # Exclusive end for completed candles.
    try:
        cache = _load_delta_cache()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        cache = None
        cache_warning = f"Local cache rejected: {exc}"
    else:
        cache_warning = None

    # Revalidate the most recent bars to catch revisions and stale cache data.
    if cache is not None and len(cache) >= SYNC_MIN_BARS:
        start = max(cache["time"].iloc[-1] - pd.Timedelta(hours=2),
                    completed_end - pd.Timedelta(days=40))
    else:
        start = completed_end - pd.Timedelta(minutes=15 * (SYNC_MIN_BARS + 64))
    start = start.floor("15min")
    parts = []
    try:
        cursor = start
        while cursor < completed_end:
            stop = min(cursor + pd.Timedelta(minutes=15 * SYNC_MAX_REQUEST_BARS),
                       completed_end)
            batch = _delta_request(int(cursor.timestamp()), int(stop.timestamp()) - 1)
            if not batch:
                raise ValueError(f"Empty API response for {cursor.isoformat()}")
            part = _validate_delta_frame(batch, completed_end)
            expected_first = cursor
            expected_last = stop - pd.Timedelta(minutes=15)
            if part["time"].iloc[0] != expected_first or part["time"].iloc[-1] != expected_last:
                raise ValueError(f"API candle coverage incomplete: {cursor.isoformat()} to {stop.isoformat()}")
            parts.append(part)
            cursor = stop
        if not parts:
            raise ValueError("No new candle intervals requested")
        fetched = pd.concat(parts, ignore_index=True)
        if cache is not None:
            # Do not silently merge inconsistent overlapping candles.
            old = cache.set_index("time")
            fresh = fetched.set_index("time")
            shared = old.index.intersection(fresh.index)
            if len(shared):
                difference = (old.loc[shared, ["open","high","low","close"]]
                              - fresh.loc[shared, ["open","high","low","close"]]).abs()
                if (difference > 0.011).any().any():
                    raise ValueError("API history disagrees with cached candles; manual review required")
            combined = pd.concat([cache, fetched], ignore_index=True)
            combined = combined.drop_duplicates("time", keep="last").sort_values("time")
        else:
            combined = fetched
        combined = _validate_delta_frame(
            [{"time": int(r.time.timestamp()), "open": r.open, "high": r.high,
              "low": r.low, "close": r.close} for r in combined.itertuples(index=False)],
            completed_end)
        if len(combined) < SYNC_MIN_BARS:
            raise ValueError("Insufficient historical warm-up candles")
        if combined["time"].iloc[-1] != completed_end - pd.Timedelta(minutes=15):
            raise ValueError("Most recent completed 15m candle is missing")
        _save_delta_cache(combined)
        return combined, {"source": "DELTA_INDIA_PUBLIC_API", "online": True,
                          "last_candle_utc": combined["time"].iloc[-1].isoformat(),
                          "count": len(combined), "warning": cache_warning}
    except Exception as exc:
        return None, {"source": "DELTA_INDIA_PUBLIC_API", "online": False,
                      "error": str(exc), "warning": cache_warning}


def infer_capture_intervals(evidence, chart_local_date):
    """Identify *candidate* UTC candle intervals from a confirmed chart-local date.

    Chart clocks do not date themselves. Do not assume a screenshot's file date
    or the current date is the chart-local date. Crosshair-selected header OHLC
    cannot be assigned to the current interval merely from the chart clock.
    """
    from datetime import date as _date, time as _time, timedelta as _timedelta
    from datetime import datetime as _datetime, timezone as _timezone
    results = []
    try:
        local_date = _date.fromisoformat(str(chart_local_date).strip())
    except (ValueError, TypeError):
        local_date = None
    for item in evidence:
        tf = item.get("assigned_timeframe") or item.get("timeframe")
        clock = item.get("chart_clock_local")
        offset = item.get("chart_utc_offset_hours")
        state = item.get("crosshair_status", "UNKNOWN")
        base = {"Screenshot": item.get("filename", ""), "Timeframe": tf,
                "Chart clock": f"{clock} UTC{offset:+d}" if clock and offset is not None else "UNREADABLE",
                "Chart clock UTC (date confirmed)": "UNCONFIRMED",
                 "Capture UTC": "UNCONFIRMED", "Candidate candle OPEN UTC": "UNCONFIRMED",
                "Candle status": "UNCONFIRMED", "Evidence": ""}
        if not local_date:
            base["Evidence"] = "Enter and confirm the chart-local calendar date (YYYY-MM-DD)"
        elif not clock or offset is None:
            base["Evidence"] = "Chart clock/timezone unreadable; cannot derive capture UTC"
        elif tf not in TF_MINUTES:
            base["Evidence"] = "Timeframe not confirmed"
        else:
            try:
                local_dt = _datetime.combine(local_date, _time.fromisoformat(clock))
                capture = (local_dt - _timedelta(hours=offset)).replace(tzinfo=_timezone.utc)
                minutes = TF_MINUTES[tf]
                open_ts = int(capture.timestamp()) // (minutes * 60) * (minutes * 60)
                candle_open = _datetime.fromtimestamp(open_ts, tz=_timezone.utc)
                base["Chart clock UTC (date confirmed)"] = capture.isoformat()
                base["Candidate candle OPEN UTC"] = candle_open.isoformat()
                if state == "HISTORICAL_CROSSHAIR_VISIBLE":
                    base["Candle status"] = "CROSSHAIR — HEADER ASSOCIATION UNCONFIRMED"
                    base["Evidence"] = "Current UTC interval is calculated, but the header may show a selected historical candle"
                elif item.get("countdown_visible"):
                    base["Candle status"] = "LIVE COUNTDOWN — FORMING"
                    base["Evidence"] = "Countdown visible; current interval is a candidate only; header association unproven"
                else:
                    base["Candle status"] = "CURRENT INTERVAL — HEADER UNCONFIRMED"
                    base["Evidence"] = "Clock establishes current interval, not which candle the header OHLC represents"
            except (ValueError, OverflowError):
                base["Evidence"] = "Invalid clock or date"
        results.append(base)
    return results



def derive_shared_capture_utc(capture_intervals):
    """Only use independently corroborating chart clocks with a confirmed local date.

    This establishes a screenshot clock instant, NOT the candle represented by
    the OHLC header. A single readable clock or disagreement fails closed.
    """
    observed = []
    for item in capture_intervals:
        value = item.get("Chart clock UTC (date confirmed)")
        if not value or value == "UNCONFIRMED":
            continue
        stamp = parse_utc_time(value)
        if stamp is not None:
            observed.append(stamp)
    if len(observed) < 2:
        return None, "At least two readable chart clocks with confirmed chart date required"
    if (max(observed) - min(observed)).total_seconds() > 150:
        return None, "Chart clocks disagree by more than 150 seconds"
    return min(observed), "Corroborated chart-clock time (header candle association unconfirmed)"


def inspect_forming_candle_candidates(history, rows):
    """Read-only live-candle comparison; never marks screenshot candles complete.

    The public API can revise a forming candle between screenshot capture and
    retrieval. A mismatch is therefore inconclusive, not an OCR failure.
    """
    output = []
    if history is None or history.empty:
        return output
    now = pd.Timestamp.now(tz="UTC")
    boundary = now.floor("15min")
    live_bar = None
    live_error = None
    try:
        records = _delta_request(int(boundary.timestamp()),
                                 int((boundary + pd.Timedelta(minutes=15)).timestamp()) - 1)
        candidates = [r for r in (records or [])
                      if pd.to_numeric(r.get("time"), errors="coerce") == int(boundary.timestamp())]
        if len(candidates) == 1:
            item = candidates[0]
            values = {k: float(item[k]) for k in ("open", "high", "low", "close")}
            if (all(math.isfinite(v) and v > 0 for v in values.values())
                    and values["low"] <= min(values["open"], values["close"])
                    <= max(values["open"], values["close"]) <= values["high"]):
                live_bar = {"time": boundary, **values}
            else:
                live_error = "API forming candle failed OHLC checks"
        else:
            live_error = "Current forming 15m bar not available from API"
    except (ValueError, TypeError, KeyError, OSError, URLError, HTTPError) as exc:
        live_error = f"Forming-candle API request failed: {exc}"

    for row in rows:
        tf = row.get("timeframe")
        if tf not in ("15m", "4h"):
            continue
        try:
            observed = {k: float(row[k]) for k in ("open", "high", "low", "close")}
            valid = (all(math.isfinite(v) and v > 0 for v in observed.values())
                     and observed["low"] <= min(observed["open"], observed["close"])
                     <= max(observed["open"], observed["close"]) <= observed["high"])
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            output.append({"Timeframe": tf, "Candidate candle UTC": "UNKNOWN",
                           "Status": "OCR INCOMPLETE", "Difference": "",
                           "Explanation": "Screenshot OHLC is missing or invalid"})
            continue
        start = boundary if tf == "15m" else now.floor("4h")
        completed = history.loc[(history["time"] >= start) &
                                (history["time"] < boundary)].copy()
        if tf == "15m":
            completed = completed.iloc[0:0]
        records = completed.to_dict("records")
        if live_bar is not None and live_bar["time"] >= start:
            records.append(live_bar)
        if not records:
            output.append({"Timeframe": tf, "Candidate candle UTC": start.isoformat(),
                           "Status": "NOT AVAILABLE", "Difference": "",
                           "Explanation": live_error or "No API bars in forming interval"})
            continue
        frame = pd.DataFrame(records).sort_values("time")
        expected = int((boundary - start) / pd.Timedelta(minutes=15)) + 1
        if len(frame) != expected:
            output.append({"Timeframe": tf, "Candidate candle UTC": start.isoformat(),
                           "Status": "PARTIAL API COVERAGE", "Difference": "",
                           "Explanation": live_error or "Missing interval in forming candle"})
            continue
        current = {"open": float(frame.iloc[0]["open"]),
                   "high": float(frame["high"].max()),
                   "low": float(frame["low"].min()),
                   "close": float(frame.iloc[-1]["close"])}
        differences = {k: round(observed[k] - current[k], 2) for k in current}
        matched = all(abs(d) <= 0.011 for d in differences.values())
        output.append({
            "Timeframe": tf, "Candidate candle UTC": start.isoformat(),
            "Status": "PROVISIONAL MATCH" if matched else "DIFFERENT SNAPSHOT / UNCONFIRMED",
            "Difference": ", ".join(f"{k}={v:+.2f}" for k, v in differences.items()),
            "Explanation": ("Current API forming candle; comparison is time-sensitive. "
                            "Does not prove screenshot timestamp or completion."),
        })
    return output


def investigate_screenshot_intervals(history, rows, intervals):
    """Never compare old screenshot OHLC with today's live API candle.

    Historical completed OHLC cannot reconstruct the evolving forming-candle
    snapshot. Only show interval availability and evidence limitations.
    """
    output = []
    if history is None or history.empty:
        return output
    by_tf = {r.get("timeframe"): r for r in rows}
    for entry in intervals or []:
        tf = entry.get("Timeframe")
        if tf not in ("15m", "4h"):
            continue
        raw = entry.get("Candidate candle OPEN UTC")
        try:
            start = pd.Timestamp(raw)
            if start.tzinfo is None:
                raise ValueError("UTC timezone missing")
        except (ValueError, TypeError):
            output.append({"Timeframe": tf, "Candidate candle UTC": "UNCONFIRMED",
                           "Status": "DATE / CLOCK REQUIRED",
                           "Explanation": entry.get("Evidence", "")})
            continue
        end = start + pd.Timedelta(minutes=TF_MINUTES[tf])
        now = pd.Timestamp.now(tz="UTC")
        if entry.get("Candle status", "").startswith("CROSSHAIR"):
            status = "HISTORICAL SELECTION — NO LIVE COMPARISON"
        elif entry.get("Candle status", "").startswith("LIVE COUNTDOWN"):
            status = "FORMING AT SCREENSHOT — SNAPSHOT NOT RECONSTRUCTIBLE"
        elif end > now:
            status = "INTERVAL NOT YET COMPLETE — NOT COMPARABLE"
        else:
            status = "HISTORICAL INTERVAL — HEADER UNCONFIRMED"
        bars = history.loc[(history["time"] >= start) & (history["time"] < end)]
        count = len(bars)
        required = TF_MINUTES[tf] // 15
        output.append({
            "Timeframe": tf, "Candidate candle UTC": start.isoformat(),
            "Interval end UTC": end.isoformat(), "Status": status,
            "15m historical bars": f"{count}/{required}",
            "Explanation": ("Current interval is inferred from chart clock and confirmed date, assuming UTC-epoch timeframe anchoring. "
                            "A completed historical candle cannot validate OHLC captured while "
                            "it was forming. Header-to-interval association remains unverified."),
        })
    return output


def render_sync_diagnostics(history, sync_info, rows, validation_details, forming_details=None, capture_intervals=None):
    with st.expander("Historical synchronization & OCR verification", expanded=False):
        st.write("**Source:** Delta Exchange India public BTCUSD traded-price candles")
        st.write("**Connection:** " + ("API sync completed" if sync_info.get("online") else "Unavailable"))
        if sync_info.get("error"):
            st.error(sync_info["error"])
        if sync_info.get("warning"):
            st.warning(sync_info["warning"])
        if history is not None:
            st.write(f"**Completed 15m candles:** {len(history):,}")
            st.write(f"**Latest completed candle UTC:** {history['time'].iloc[-1]}")
            for tf in TIMEFRAMES:
                aggregated = aggregate_history(history, tf)
                st.write(f"**{tf}:** {len(aggregated):,} complete candles")
        st.markdown("**Chart clock → UTC interval (capture-date confirmation required)**")
        if capture_intervals:
            for entry in capture_intervals:
                with st.expander(f"{entry.get('Timeframe', '?')} — UTC interval details", expanded=False):
                    for key, value in entry.items():
                        st.write(f"**{key}:** {value}")
        st.caption("The chart clock is not independent capture-time proof. UTC intervals assume "
                   "epoch-aligned 15m/4H bars pending exchange alignment confirmation. "
                   "The UTC interval is an inferred candidate, not an independently verified "
                   "header-candle timestamp. Forming candles and crosshair-selected candles "
                   "remain excluded from strategy inputs.")
        st.markdown("**Chart clock OCR and candle selection evidence**")
        for e in st.session_state.get("ocr_evidence", []):
            tf = e.get("assigned_timeframe") or e.get("timeframe") or "?"
            with st.expander(f"{tf} — clock / countdown / crosshair evidence", expanded=False):
                st.write("**Screenshot:**", e.get("filename", ""))
                st.json(e.get("ohlc_debug", {}))
                st.write("**Chart clock:**", e.get("chart_clock_local") or "UNREADABLE")
                st.write("**UTC offset:**", e.get("chart_utc_offset_hours"))
                st.write("**Countdown detected:**", bool(e.get("countdown_visible")))
                st.write("**Countdown seconds:**", e.get("countdown_seconds"))
                st.write("**Red label locator:**", e.get("red_label_debug", "NOT AVAILABLE"))
                st.write("**Countdown source:**", e.get("countdown_source", "OCR" if e.get("countdown_visible") else "NONE"))
                st.write("**Crosshair status:**", e.get("crosshair_status", "UNKNOWN"))
                st.write("**Crosshair label OCR:**", e.get("crosshair_label_ocr", ""))
                st.write("**Manual fallback:**", e.get("manual_countdown_status", ""))
                with st.expander("Raw OCR text (debug only)", expanded=False):
                    st.code("Clock OCR: " + str(e.get("chart_clock_ocr", "")) +
                            "\\nCountdown OCR: " + str(e.get("live_countdown_ocr", "")),
                            language="text")
        st.markdown("**Screenshot extraction — visible OCR values (not assumed verified)**")
        for r in rows:
            tf = r.get("timeframe", "?")
            with st.expander(f"{tf} — extracted OHLC", expanded=False):
                st.write(f"**Open:** {r.get('open')} · **High:** {r.get('high')}")
                st.write(f"**Low:** {r.get('low')} · **Close:** {r.get('close')}")
                st.write("**Candle UTC:** " + str(r.get("candle_open_utc") or "UNCONFIRMED"))
                st.write("**Completed at capture:** UNCONFIRMED")
                if r.get("ocr_notes"):
                    st.caption(str(r["ocr_notes"]))
        st.caption("A missing price is intentionally left blank. Never repair an OCR digit using an assumed market value.")
        if validation_details:
            st.markdown("**Historical comparison — evidence, not trading approval**")
            for d in validation_details:
                with st.expander(f"{d.get('Timeframe', '?')} — historical comparison", expanded=False):
                    for key, value in d.items():
                        st.write(f"**{key}:** {value}")
        if forming_details:
            st.markdown("**15m / 4H interval investigation (historical snapshot-safe)**")
            for d in forming_details:
                with st.expander(f"{d.get('Timeframe', '?')} — interval investigation", expanded=False):
                    for key, value in d.items():
                        st.write(f"**{key}:** {value}")
            st.caption("Historical completed OHLC cannot recreate a forming screenshot. "
                       "No old screenshot is compared with today's forming API candle. "
                       "No candidate here is used by strategies.")
        st.caption("Unique historical OHLC matches do not prove screenshot capture time or crosshair state. "
                   "A historical candle may have been selected on the chart.")


# ============================================================
# 14. INTEGRATED FOUR TABLES
# ============================================================

def phase4_history_eligibility(history, sync_info, now_utc=None):
    """Fail-closed eligibility for a completed-history research preview.

    This does not establish screenshot alignment or authorize live trading.
    """
    issues = []
    now = pd.Timestamp.now(tz="UTC") if now_utc is None else pd.Timestamp(now_utc)
    if now.tzinfo is None:
        return None, ["Current UTC time has no timezone"]
    now = now.tz_convert("UTC")
    if not isinstance(sync_info, dict) or sync_info.get("online") is not True:
        issues.append("Delta API sync not confirmed online for this evaluation")
    if history is None or not isinstance(history, pd.DataFrame) or history.empty:
        return None, issues + ["Completed 15m history unavailable"]
    required = {"time", "open", "high", "low", "close"}
    if not required.issubset(history.columns):
        return None, issues + ["Historical OHLC columns missing"]
    times = pd.to_datetime(history["time"], utc=True, errors="coerce")
    if times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
        issues.append("Historical timestamps invalid, duplicated or unsorted")
    if len(times) < SYNC_MIN_BARS:
        issues.append("Insufficient historical warm-up candles")
    if len(times) > 1 and not (times.diff().dropna() == pd.Timedelta(minutes=15)).all():
        issues.append("Historical 15m series has missing intervals")
    expected_last = now.floor("15min") - pd.Timedelta(minutes=15)
    if times.iloc[-1] != expected_last:
        issues.append(
            f"History not current: last {times.iloc[-1].isoformat()}, "
            f"expected {expected_last.isoformat()}"
        )
    if (times >= now.floor("15min")).any():
        issues.append("Historical series contains an incomplete or future candle")
    boundary = (times.iloc[-1] + pd.Timedelta(minutes=15)).floor("h")
    if boundary > now.floor("h"):
        issues.append("Decision boundary lies in the future")
    return (None if issues else boundary), issues



def render_phase4_transparent_explanation(result, failure=None, scope="Historical"):
    """Read-only explanation of engine output; never creates a decision or fill."""
    st.markdown(f"#### {scope} — signal explanation")
    if result is None:
        st.warning("NOT EVALUATED — " + str(failure or "Eligibility requirements not met."))
        st.caption("No strategy evaluation took place. No entry, stop or target is available.")
        return

    signal = result.get("signal", "NOT EVALUATED")
    strategy = result.get("strategy") or "NONE"
    st.write(f"**Result:** {signal}  |  **Selected strategy:** {strategy}")
    st.write(f"**Decision UTC:** {result.get('decision_time_utc') or 'UNAVAILABLE'}")
    st.write(f"**Completed 15m signal candle OPEN UTC:** "
             f"{result.get('signal_candle_open_utc') or 'UNAVAILABLE'}")
    st.write(f"**Data source:** {result.get('data_source') or 'UNAVAILABLE'}")
    st.write(f"**Frozen strategy version:** {result.get('strategy_version') or 'UNAVAILABLE'}")
    st.write(f"**Engine explanation:** {result.get('reason') or 'Not provided by engine'}")

    if signal in ("BUY", "SELL"):
        from phase6_terminal_view import render_terminal_css, _cards, _price, _text
        render_terminal_css(st.session_state.get("display_mode", "Desktop"))
        _cards([
            ("ENTRY (INDICATIVE LIMIT)", _price(result.get("entry"))),
            ("STOP", _price(result.get("stop"))),
            ("TARGET", _price(result.get("target"))),
            ("REWARD / RISK", _text(result.get("rr"))),
        ])
        st.write(f"**Indicative order expiry UTC:** {result.get('expiry_utc') or 'UNAVAILABLE'}")
        st.caption("Entry, stop, target and reward/risk are engine-modeled research levels, "
                   "not observed exchange fills, orders, or guaranteed outcomes.")
    elif signal == "NO TRADE":
        st.info("NO TRADE — valid historical inputs were evaluated, "
                "but none of the five frozen strategies qualified.")
        st.caption("No entry, stop, target or order expiry applies.")

    candidates = result.get("eligible_candidates") or []
    priority = ("ORIGINAL_WICK", "CONTINUATION", "PULLBACK",
                "REVERSAL", "IMPULSE_PULLBACK")
    candidate_lookup = {item.get("strategy"): item.get("direction")
                        for item in candidates if isinstance(item, dict)}
    st.markdown("**Five-strategy eligibility (frozen priority order)**")
    st.dataframe(pd.DataFrame([
        {"Priority": index, "Strategy": name,
         "Eligibility": "ELIGIBLE" if name in candidate_lookup else "NOT ELIGIBLE",
         "Direction": candidate_lookup.get(name) or "—",
         "Selected": "YES" if name == result.get("strategy") else "NO"}
        for index, name in enumerate(priority, start=1)
    ]), hide_index=True, use_container_width=True)
    indicators = (
        ("1H ATR(14)", "atr_1h_14"),
        ("3H momentum / ATR", "momentum_3h_atr"),
        ("4H +DI", "plus_di_4h"),
        ("4H -DI", "minus_di_4h"),
        ("Relative volatility", "relative_volatility"),
    )
    with st.expander("Indicator evidence used by frozen engine", expanded=False):
        st.dataframe(pd.DataFrame([
            {"Indicator": label, "Value": result.get(key)}
            for label, key in indicators
        ]), hide_index=True, use_container_width=True)
    if scope == "Historical":
        st.caption("Historical completed-candle research only. NOT screenshot-verified, "
                   "NOT a current trading signal, and NOT an exchange order.")
    else:
        st.caption("Screenshot-aligned research is not proof of an exchange fill or order.")


def render_compact_historical_verification(rows, history, historical_decision):
    """Only the seven user-requested read-only evidence sections."""
    def fmt(value, decimals=1):
        try:
            number = float(value)
            return f"{number:.{decimals}f}" if math.isfinite(number) else "NOT VERIFIED"
        except (TypeError, ValueError):
            return "NOT VERIFIED"

    def line(label, value):
        return f"{label}: {value}"

    output = ["========== OCR: ALL FOUR TIMEFRAMES =========="]
    row_map = {row.get("timeframe"): row for row in rows}
    for tf in TIMEFRAMES:
        row = row_map.get(tf, {})
        output.extend(["", tf])
        vals = [row.get(k) for k in ("open", "high", "low", "close")]
        try:
            o, h, l, c = map(float, vals)
            valid = all(math.isfinite(x) and x > 0 for x in (o, h, l, c)) and l <= min(o, c) <= max(o, c) <= h
        except (TypeError, ValueError):
            valid = False
        if valid:
            body, span = abs(c-o), h-l
            direction = "BULLISH" if c > o else "BEARISH" if c < o else "DOJI"
            output.extend([
                f"  OPEN={fmt(o)} HIGH={fmt(h)} LOW={fmt(l)} CLOSE={fmt(c)}",
                f"  BODY={fmt(body, 2)} RANGE={fmt(span, 2)}",
                f"  BODY/RANGE={fmt(100*body/span, 2) + '%' if span else 'NOT VERIFIED'}",
                f"  DIRECTION={direction}",
            ])
        else:
            output.extend([
                "  OPEN=NOT VERIFIED HIGH=NOT VERIFIED LOW=NOT VERIFIED CLOSE=NOT VERIFIED",
                "  BODY=NOT VERIFIED RANGE=NOT VERIFIED",
                "  BODY/RANGE=NOT VERIFIED",
                "  DIRECTION=NOT VERIFIED",
            ])

    d = historical_decision or {}
    output.extend([
        "",
        "========== FROZEN FIVE-STRATEGY ENGINE ==========",
        f"Engine version: {d.get('strategy_version') or 'NOT EVALUATED'}",
        r"History source: delta_btcusd_data\\BTCUSD_15m.csv",
        f"Historical decision UTC: {d.get('decision_time_utc') or 'NOT EVALUATED'}",
        "OCR candle timestamps: " + (
            "VERIFIED" if st.session_state.get("last_strategy_decision") is not None
            else "NOT VERIFIED"
        ),
        "",
        "========== ACTUAL ENGINE DECISION ==========",
        f"SIGNAL   : {d.get('signal') or 'NOT EVALUATED'}",
        f"STRATEGY : {d.get('strategy') or 'NONE'}",
        f"REASON   : {d.get('reason') or 'NOT EVALUATED'}",
        f"DECISION : {d.get('decision_time_utc') or 'NOT EVALUATED'}",
        f"CANDLE   : {d.get('signal_candle_open_utc') or 'NOT EVALUATED'}",
        "",
        "========== EXACT ENGINE SIGNAL CANDLE ==========",
    ])
    candle = None
    if history is not None and d.get("signal_candle_open_utc"):
        try:
            target = pd.Timestamp(d["signal_candle_open_utc"])
            match = history.loc[pd.to_datetime(history["time"], utc=True) == target]
            if len(match) == 1:
                candle = match.iloc[0]
        except (KeyError, ValueError, TypeError):
            pass
    for key in ("open", "high", "low", "close"):
        output.append(f"{key.upper():6s} : {fmt(candle[key]) if candle is not None else 'NOT EVALUATED'}")

    output.extend(["", "========== COMPUTED INDICATORS =========="])
    for key in ("atr_1h_14", "momentum_3h_atr", "plus_di_4h",
                "minus_di_4h", "relative_volatility"):
        output.append(f"{key}: {d.get(key) if d.get(key) is not None else 'NOT EVALUATED'}")

    output.extend(["", "========== FIVE STRATEGY CONFIRMATION =========="])
    candidates = {item.get("strategy"): item.get("direction")
                  for item in d.get("eligible_candidates", [])
                  if isinstance(item, dict)}
    for name in ("ORIGINAL_WICK", "CONTINUATION", "PULLBACK",
                 "REVERSAL", "IMPULSE_PULLBACK"):
        outcome = (candidates.get(name) or "NOT ELIGIBLE") if historical_decision else "NOT EVALUATED"
        output.append(f"{name:20s}: {outcome}")

    output.extend([
        "",
        "========== FINAL ==========",
        f"Historical engine result: {d.get('signal') or 'NOT EVALUATED'}",
        f"Selected strategy: {d.get('strategy') or 'NONE'}",
    ])
    st.code("\n".join(output), language=None)


def render_integrated_research(screenshots):
    """OCR preflight and conservative strategy gate."""
    evidence = []
    detected_by_hash = {}
    for shot in screenshots:
        try:
            detected = restricted_ocr(shot["bytes"])
        except (OSError, RuntimeError, ValueError, pytesseract.TesseractError) as exc:
            detected = {"timeframe": None, "ohlc": {}, "symbol": None,
                        "issues": [f"OCR failed: {exc}"], "raw_header": "",
                        "timestamp_utc": None, "candle_closed": None,
                        "crosshair_status": "UNKNOWN"}
        detected_by_hash[shot["sha256"]] = detected
        evidence.append({"filename": shot["name"], **detected})

    assignments = {}
    with st.sidebar:
        if screenshots:
            ambiguous = any(item.get("timeframe") is None for item in evidence)
            with st.expander("Review detected timeframes", expanded=ambiguous):
                for shot_index, (shot, detected) in enumerate(zip(screenshots, evidence)):
                    options = ["Unassigned", *TIMEFRAMES]
                    guessed = detected.get("timeframe")
                    choice = st.selectbox(
                        shot["name"], options,
                        index=options.index(guessed) if guessed in TIMEFRAMES else 0,
                        key="chart_tf_" + str(shot_index) + "_" + shot["sha256"])
                    if choice != "Unassigned":
                        assignments[shot["sha256"]] = choice

    duplicates = len(set(shot["sha256"] for shot in screenshots)) != len(screenshots)
    assignment_error = len(assignments) != len(set(assignments.values()))
    fingerprint = ("phase3_pillow_red_label_v2_field_consensus", tuple(sorted((shot["name"], shot["sha256"]) for shot in screenshots)),
                   tuple(sorted(assignments.items())))
    if st.session_state.get("ocr_fingerprint") != fingerprint:
        rows, extracted = assemble_rows(screenshots, assignments, detected_by_hash)
        st.session_state["market_rows"] = rows
        st.session_state["ocr_evidence"] = extracted
        st.session_state["ocr_fingerprint"] = fingerprint
    rows = st.session_state.get("market_rows", [blank_row(tf) for tf in TIMEFRAMES])
    # Confirmation is explicitly user-entered screenshot transcription, not
    # automatic OCR and never evidence of the candle timestamp/completion.
    with st.expander("Hybrid OCR — review uncertain OHLC fields", expanded=True):
        st.caption("Only uncertain fields need your confirmation. Check the screenshot; "
                   "do not use historical CSV values. This does NOT verify candle time.")
        for tf in TIMEFRAMES:
            row = next((r for r in rows if r.get("timeframe") == tf), None)
            if row is None:
                continue
            missing = [field for field in ("open", "high", "low", "close")
                       if row.get(field) is None]
            if not missing:
                st.success(f"{tf}: all four prices extracted (timestamp unverified)")
                continue
            st.write(f"**{tf} — confirm: {', '.join(missing)}**")
            matching = next((shot for shot in screenshots
                             if assignments.get(shot["sha256"]) == tf), None)
            if matching is not None:
                st.image(matching["bytes"], caption=f"{tf} source screenshot",
                         use_container_width=True)
            for field in missing:
                key = "hybrid_manual_" + tf + "_" + field
                raw = st.text_input(f"{tf} {field.upper()} from screenshot",
                                    key=key, placeholder="e.g. 65139.5")
                confirmed = st.checkbox(
                    f"I checked {tf} {field.upper()} against the screenshot",
                    key="hybrid_confirm_" + tf + "_" + field)
                if raw.strip() and confirmed:
                    try:
                        value = float(raw.replace(",", ""))
                        if not (math.isfinite(value) and value > 0):
                            raise ValueError("Invalid price")
                        row[field] = value
                        row["source"] = "OCR_PLUS_USER_TRANSCRIPTION"
                        row["ocr_notes"] = (row.get("ocr_notes", "") +
                                            f"; {field} user-transcribed from screenshot")
                    except ValueError:
                        st.error(f"{tf} {field}: enter a valid positive price")
            if all(row.get(k) is not None for k in
                   ("open", "high", "low", "close")):
                o, hi, lo, c = (float(row[k]) for k in
                                ("open", "high", "low", "close"))
                if not lo <= min(o, c) <= max(o, c) <= hi:
                    st.error(f"{tf}: OHLC prices conflict; review screenshot values")
                    for field in missing:
                        row[field] = None

    # Date controls are rendered in main() on every rerun, not only after ANALYZE.
    # Reading the widget state here prevents an old result from silently persisting.
    chart_local_date = st.session_state.get("phase3_chart_local_date", "").strip()
    date_confirmed = bool(st.session_state.get("phase3_date_confirmed", False))
    try:
        from datetime import date as _chart_date
        _chart_date.fromisoformat(chart_local_date)
        valid_chart_date = True
    except (ValueError, TypeError):
        valid_chart_date = False
    # Apply manual fallback only after OCR and only for a confirmed 4H screenshot.
    # A manual reading cannot override a conflicting OCR countdown.
    for ev in st.session_state.get("ocr_evidence", []):
        if (ev.get("assigned_timeframe") or ev.get("timeframe")) != "4h":
            continue
        if "original_ocr_countdown_visible" not in ev:
            ev["original_ocr_countdown_visible"] = bool(ev.get("countdown_visible"))
            ev["original_ocr_countdown_seconds"] = ev.get("countdown_seconds")
        ev["countdown_visible"] = ev["original_ocr_countdown_visible"]
        ev["countdown_seconds"] = ev["original_ocr_countdown_seconds"]
        ev["countdown_source"] = "OCR" if ev.get("countdown_visible") else "NONE"
        ev["manual_countdown_status"] = ""
        if not st.session_state.get("phase3_4h_manual_confirm", False):
            continue
        raw_manual = st.session_state.get("phase3_4h_manual_countdown", "")
        seconds, reason = parse_confirmed_countdown(
            raw_manual, "4h", ev.get("chart_clock_local"),
            ev.get("chart_utc_offset_hours"))
        ev["manual_countdown_status"] = reason
        if seconds is None:
            st.warning(f"4H manual countdown NOT applied: {reason}. "
                       "Check the 4H chart clock and countdown; no candle was verified.")
        elif not ev.get("countdown_visible"):
            st.info("4H countdown accepted as USER-TRANSCRIBED screenshot evidence; "
                    "header OHLC is still not independently verified.")
        if seconds is not None and not ev.get("countdown_visible"):
            ev["countdown_visible"] = True
            ev["countdown_seconds"] = seconds
            ev["countdown_source"] = "USER-TRANSCRIBED"
        elif seconds is not None and ev.get("countdown_seconds") != seconds:
            ev["manual_countdown_status"] = "OCR and manual countdown disagree; review screenshot"
            ev["countdown_visible"] = False
            ev["countdown_source"] = "CONFLICT"
    capture_intervals = infer_capture_intervals(
        st.session_state.get("ocr_evidence", []),
        chart_local_date if date_confirmed and valid_chart_date else None,
    )

    history, sync_info = synchronize_delta_history()
    hist_error = sync_info.get("error")

    # A confirmed chart-local date plus agreeing chart clocks can establish
    # the capture instant, but NEVER prove which candle the header represents.
    capture_utc, capture_evidence = derive_shared_capture_utc(capture_intervals)
    # The screenshot header can refer to a crosshair-selected historical candle.
    # No screenshot OHLC is silently added to historical data or marked completed.
    validation_details = []
    if history is not None:
        rows, validation_details = validate_historical_rows(
            rows, history,
            source_metadata={"independent_delta_api_verified": sync_info.get("online") is True},
            capture_time_utc=capture_utc,
        )
    # The validator checks OHLC/time/source, but not the header-to-candle
    # association. Do not show VERIFIED without that independent evidence.
    for detail in validation_details:
        if detail.get("Status") == "VERIFIED":
            detail["Status"] = "LOCAL MATCH — HEADER UNCONFIRMED"
            detail["Issues"] = (str(detail.get("Issues") or "") + "; "
                                "Screenshot header-to-candle association not independently proven").strip("; ")
    # A forming screenshot is not a completed historical OHLC sample.
    # Preserve the raw candidate count but do not call this an OCR mismatch.
    evidence_by_tf = {
        e.get("assigned_timeframe") or e.get("timeframe"): e
        for e in st.session_state.get("ocr_evidence", [])
    }
    interval_by_tf = {i.get("Timeframe"): i for i in capture_intervals}
    for detail in validation_details:
        tf = detail.get("Timeframe") or detail.get("timeframe")
        if tf not in ("15m", "4h"):
            continue
        ev = evidence_by_tf.get(tf, {})
        iv = interval_by_tf.get(tf, {})
        if (ev.get("countdown_visible")
                and iv.get("Candidate candle OPEN UTC") != "UNCONFIRMED"
                and not str(iv.get("Candle status", "")).startswith("CROSSHAIR")):
            detail["Status"] = "FORMING — HISTORICAL SNAPSHOT UNVERIFIABLE"
            detail["Historical OHLC"] = "NOT APPLICABLE TO FORMING SNAPSHOT"
            detail["Completed at capture"] = False
            detail["Screenshot interval UTC"] = iv.get("Candidate candle OPEN UTC")
            detail["Reason"] = (
                "Countdown detected. Completed historical OHLC cannot reproduce "
                "the screenshot-time provisional OHLC; header association remains unverified."
            )

    features = [f for row in rows if (f := candle_features(row)) is not None]

    blockers = []
    if duplicates:
        blockers.append("Identical screenshot uploaded more than once.")
    if assignment_error:
        blockers.append("Two screenshots have the same assigned timeframe.")
    if len(screenshots) != 4 or len(assignments) != 4:
        blockers.append("Assign all four screenshots (15m, 1h, 3h, 4h).")
    for row in rows:
        result = validate_row(row)
        if not result["ready_for_history_match"]:
            blockers.append(row["timeframe"] + ": OHLC or timestamp needs verification.")
    if hist_error:
        blockers.append(hist_error)
    if history is not None and history["time"].iloc[-1] != pd.Timestamp.now(tz="UTC").floor("15min") - pd.Timedelta(minutes=15):
        blockers.append("Historical feed is stale")
    if history is None:
        blockers.append("Continuous 15m candle history unavailable.")

    # Phase 4: independent completed-history research preview.
    # Screenshot validation remains a separate, fail-closed gate.
    decision = None
    failure = None
    history_decision = None
    history_failure = None
    history_boundary = None
    history_boundary, eligibility_issues = phase4_history_eligibility(
        history, sync_info
    )
    if hist_error:
        eligibility_issues.append(str(hist_error))
    if history_boundary is not None and not hist_error:
        try:
            history_decision = evaluate(history, history_boundary)
            history_decision["data_source"] = "DELTA_INDIA_PUBLIC_API_COMPLETED_15M"
        except Exception as exc:
            history_failure = f"Strategy engine rejected input: {type(exc).__name__}: {exc}"
    else:
        history_failure = "; ".join(eligibility_issues) or "Historical inputs unavailable."

    if not blockers and history_decision is not None:
        try:
            screenshot_times = [parse_utc_time(r.get("candle_open_utc")) for r in rows]
            if any(dt is None for dt in screenshot_times):
                failure = "Screenshot candle timestamps cannot be established reliably."
            elif max(pd.Timestamp(x) for x in screenshot_times) >= pd.Timestamp(
                history_decision["decision_time_utc"]
            ):
                failure = "Historical decision predates screenshot candle evidence."
            else:
                # A prior screenshot candle timestamp does not prove that
                # the latest historical decision belongs to this capture.
                # Keep completed-history research separate from screenshot
                # evaluation until an independently verified snapshot exists.
                failure = (
                    "Screenshot capture time and exact historical decision "
                    "snapshot are not independently verified."
                )
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            failure = str(exc)
    else:
        failure = "; ".join(blockers) or history_failure

    st.session_state["last_strategy_decision"] = decision
    st.session_state["strategy_gate"] = {"blockers": blockers}
    st.session_state["phase4_history_preview"] = history_decision
    st.session_state["phase4_history_error"] = history_failure
    st.session_state["phase4_history_blockers"] = eligibility_issues

    render_compact_historical_verification(rows, history, history_decision)
    return

    st.markdown("### Phase 4 · Step 5 — Strategy eligibility")
    st.dataframe(pd.DataFrame([
        {"Evaluation": "Completed-history research",
         "Status": "EVALUATED" if history_decision is not None else "NOT EVALUATED",
         "Reason": (history_decision.get("reason", "Eligible historical evaluation")
                    if history_decision is not None else history_failure)},
        {"Evaluation": "Screenshot-aligned research",
         "Status": "EVALUATED" if decision is not None else "NOT EVALUATED",
         "Reason": (decision.get("reason", "Eligible screenshot evaluation")
                    if decision is not None else failure)},
    ]), hide_index=True, use_container_width=True)
    st.caption("NO TRADE means the engine evaluated valid data and found no setup. "
               "NOT EVALUATED means eligibility failed. Historical results are not "
               "screenshot-verified, current trading signals, or exchange orders.")
    st.markdown("### Phase 4 · Completed-history strategy research")
    st.caption(
        "Separate historical research preview. Not a screenshot-verified or current "
        "signal; does not create journal entries or modeled trades."
    )
    if history_decision:
        st.info(
            f"Historical result: {history_decision['signal']} · "
            f"Strategy: {history_decision.get('strategy') or 'NONE'} · "
            f"Decision UTC: {history_decision['decision_time_utc']}"
        )
        with st.expander("Five-strategy historical decision details", expanded=False):
            st.json({
                "signal": history_decision["signal"],
                "strategy": history_decision["strategy"],
                "eligible_candidates": history_decision["eligible_candidates"],
                "signal_candle_open_utc": history_decision["signal_candle_open_utc"],
                "decision_time_utc": history_decision["decision_time_utc"],
                "data_source": history_decision["data_source"],
                "strategy_version": history_decision["strategy_version"],
                "entry": history_decision["entry"],
                "stop": history_decision["stop"],
                "target": history_decision["target"],
                "reason": history_decision["reason"],
            })
    else:
        st.warning("Historical strategy NOT EVALUATED — " + (
            history_failure or "Required historical inputs unavailable."
        ))
    st.markdown("#### Step 7 · Research journal safety")
    st.caption("ANALYZE is read-only. Saving below records the historical decision "
               "only; it does not create a PENDING trade, simulate fills, or submit orders.")
    if history_decision is not None:
        st.write("Decision identity: " + str(history_decision.get("decision_time_utc", "—"))
                 + " · " + str(history_decision.get("strategy_version", "—")))
        # The callback runs before Streamlit reruns the script. Save the exact
        # previously rendered decision, rather than relying on the new run's
        # uploader/history state to still be present.
        st.session_state["phase4_step7_pending_decision"] = dict(history_decision)
        st.caption("Use Save Trade below the main research result to record this decision.")
    else:
        st.info("No eligible historical decision to record.")
    render_phase4_transparent_explanation(history_decision, history_failure, "Historical")
    render_phase4_transparent_explanation(decision, failure, "Screenshot-aligned")
    st.markdown("### Screenshot-Aligned Research Preview")
    left, middle, right = st.columns(3)
    left.metric("Market data", "READY" if decision else "NEEDS DATA")
    middle.metric("Selected strategy", decision.get("strategy") or "NONE" if decision else "—")
    right.metric("Signal", decision["signal"] if decision else "NOT EVALUATED")

    st.markdown("#### Trading Signal")
    if decision:
        from phase6_terminal_view import render_terminal_css, _cards, _price, _text
        render_terminal_css(st.session_state.get("display_mode", "Desktop"))
        _cards([
            ("ENTRY", _price(decision.get("entry"))),
            ("STOP", _price(decision.get("stop"))),
            ("TARGET", _price(decision.get("target"))),
            ("REWARD / RISK", _text(decision.get("rr"))),
            ("EXPIRY UTC", _text(decision.get("expiry_utc"))),
        ])
        st.caption(f"Decision UTC: {decision['decision_time_utc']} · {decision.get('reason','')}")
        st.caption("Preview only: no decision, setup, fill or outcome is automatically saved.")
    else:
        st.warning("NOT EVALUATED — " + (failure or "Required strategy inputs unavailable."))
        st.caption("Missing inputs must never be reported as NO TRADE.")

    analysis_left, analysis_right = st.columns(2)
    with analysis_left:
        st.markdown("#### Essential Market Analysis")
        st.write("**Trend:** —")
        st.write("**Momentum:** —")
        st.write("**Volatility:** —")
        st.write("**ATR (14):** —")
        st.write("**Candle status:** " + ("Verified" if decision else "Not verified"))
    with analysis_right:
        st.markdown("#### Five-Strategy Evaluation")
        names = ["ORIGINAL_WICK", "CONTINUATION", "PULLBACK", "REVERSAL", "IMPULSE_PULLBACK"]
        eligible = {c["strategy"] for c in (decision or {}).get("eligible_candidates", [])}
        st.dataframe(pd.DataFrame([{
            "Strategy": name,
            "Result": ("ELIGIBLE" if name in eligible else "NOT ELIGIBLE") if decision else "NOT EVALUATED"
        } for name in names]), hide_index=True, use_container_width=True)

    forming_details = investigate_screenshot_intervals(
        history, rows, capture_intervals) if history is not None else []
    st.markdown("#### Phase 3 — Screenshot verification summary")
    summary_items = []
    historic_match_by_tf = {
        d.get("Timeframe"): d for d in validation_details
    }
    interval_lookup = {i.get("Timeframe"): i for i in capture_intervals}
    for tf in TIMEFRAMES:
        ev = evidence_by_tf.get(tf, {})
        iv = interval_lookup.get(tf, {})
        is_forming = (ev.get("countdown_visible")
                      and iv.get("Candidate candle OPEN UTC") != "UNCONFIRMED"
                      and not str(iv.get("Candle status", "")).startswith("CROSSHAIR"))
        summary_items.append({
            "TF": tf,
            "Current UTC interval": iv.get("Candidate candle OPEN UTC", "UNCONFIRMED"),
            "Historical OHLC candidate": (
                historic_match_by_tf.get(tf, {}).get("Candle OPEN UTC", "NONE")
                if tf in ("1h", "3h") else "NOT APPLICABLE"),
            "Screenshot evidence": ("LIVE CANDLE FORMING (HEADER UNPROVEN)" if is_forming else
                                    "HISTORICAL CROSSHAIR" if ev.get("crosshair_status") == "HISTORICAL_CROSSHAIR_VISIBLE"
                                    else "HEADER UNCONFIRMED"),
            "Countdown source": ev.get("countdown_source", "NONE"),
            "Verification": ("SNAPSHOT NOT RECONSTRUCTIBLE" if is_forming else
                             "REVIEW HISTORICAL MATCH" if tf in ("1h", "3h") else
                             "UNCONFIRMED"),
        })
    for item in summary_items:
        ev = evidence_by_tf.get(item["TF"], {})
        with st.expander(f"{item['TF']} · {item['Screenshot evidence']}", expanded=True):
            st.write(f"**Current chart interval:** {item['Current UTC interval']}")
            if item["TF"] in ("1h", "3h"):
                st.write(f"**Historical OHLC candidate:** {item['Historical OHLC candidate']}")
            if item["TF"] in ("1h", "3h"):
                st.write("**Crosshair label OCR:** " +
                         str(ev.get("crosshair_label_ocr") or "NOT READABLE"))
                st.write("**Crosshair evidence:** " +
                         str(ev.get("crosshair_status") or "UNCONFIRMED"))
                crosshair_check = crosshair_label_consistency(
                    ev.get("crosshair_label_ocr"),
                    ev.get("chart_utc_offset_hours"),
                    item["Historical OHLC candidate"],
                    item["TF"])
                if crosshair_check.startswith("LABEL WITHIN"):
                    st.success("Crosshair date/time agrees with the historical OHLC candle interval.")
                elif crosshair_check.startswith("LABEL CONFLICTS"):
                    st.error("Crosshair date/time CONFLICTS with the historical OHLC candidate.")
                else:
                    st.warning("Crosshair association cannot be confirmed: " + crosshair_check)
                st.caption("This is supporting interval evidence, NOT independent "
                           "proof that header OHLC belongs to the selected candle.")

                st.caption("A unique OHLC match is a candidate, not proof the "
                           "crosshair selected that candle. Confirm against the "
                           "visible chart date before accepting association.")
            st.write(f"**Countdown source:** {item['Countdown source']}")
            if item["TF"] == "4h":
                auto_seconds = ev.get("original_ocr_countdown_seconds",
                                      ev.get("countdown_seconds"))
                auto_seen = ev.get("original_ocr_countdown_visible",
                                   ev.get("countdown_source") == "OCR")
                if auto_seen and auto_seconds is not None:
                    st.success(f"Automatic 4H OCR: PASS — {auto_seconds} seconds "
                               "(independent of manual fallback)")
                else:
                    st.warning("Automatic 4H OCR: NOT DETECTED. "
                               "Any accepted countdown is manual screenshot evidence.")
                if item["Countdown source"] == "USER-TRANSCRIBED":
                    st.info("Manual fallback is active. Disable it to check "
                            "the OCR-only result; the status above always "
                            "reports the original OCR outcome.")
            st.write(f"**Verification:** {item['Verification']}")
    st.caption("A countdown proves a live candle is forming, not that the header OHLC "
               "belongs to it. No forming screenshot is treated as a verified completed candle.")
    render_sync_diagnostics(history, sync_info, rows, validation_details, forming_details, capture_intervals)
    with st.expander("Diagnostics · open only when something fails", expanded=False):
        st.caption("Advanced data shown as individual records for readability. "
                   "These values are evidence, not trading approval.")
        diagnostic_sections = [
            ("Screenshot extraction", rows),
            ("Indicator features", features if features is not None else []),
            ("Historical comparison", validation_details),
        ]
        for section_title, records in diagnostic_sections:
            if isinstance(records, pd.DataFrame):
                records = records.to_dict("records")
            if not records:
                continue
            with st.expander(section_title, expanded=False):
                for index, record in enumerate(records, start=1):
                    if not isinstance(record, dict):
                        record = {"Value": str(record)}
                    label = (record.get("timeframe") or record.get("Timeframe")
                             or record.get("tf") or f"Record {index}")
                    with st.expander(str(label), expanded=False):
                        for field, value in record.items():
                            if isinstance(value, (dict, list, tuple)):
                                st.write(f"**{field}:**")
                                st.json(value)
                            else:
                                st.write(f"**{field}:** {value}")
        with st.expander("OCR notes", expanded=False):
            for evidence in st.session_state.get("ocr_evidence", []):
                if evidence.get("issues"):
                    st.write(str(evidence.get("filename", "Screenshot")) + ":")
                    for issue in evidence["issues"]:
                        st.write("• " + str(issue))
        st.write("Historical data:", hist_error or
                 (f"{len(history)} candles through {history['time'].iloc[-1]}"
                  if history is not None else "Unavailable"))
        st.caption("No exchange orders are submitted. A modeled fill is not "
                   "an exchange-confirmed fill.")



def phase4_step7_save_callback():
    """Save a previously displayed historical decision before the widget rerun."""
    snapshot = st.session_state.get("phase4_step7_pending_decision")
    if not isinstance(snapshot, dict):
        st.session_state["phase4_step7_feedback"] = (
            "error", "No historical decision snapshot available. Click ANALYZE first."
        )
        return
    try:
        with get_connection() as conn:
            outcome = save_historical_research_record(conn, snapshot)
        messages = {
            "SAVED": ("success", "Historical decision recorded. No setup or trade was created."),
            "ALREADY_SAVED": ("info", "This decision is already recorded. No duplicate was created."),
            "CONFLICTING_DECISION": (
                "error", "Conflicting decision exists at this UTC boundary/version. Nothing was overwritten."
            ),
        }
        st.session_state["phase4_step7_feedback"] = messages.get(
            outcome, ("error", f"Unexpected save result: {outcome}")
        )
        if outcome in ("SAVED", "ALREADY_SAVED"):
            st.session_state["phase6_saved_snapshot"] = dict(snapshot)
            st.session_state["phase6_screen"] = "terminal"
    except (ValueError, sqlite3.Error) as exc:
        st.session_state["phase4_step7_feedback"] = (
            "error", f"Decision not saved: {exc}"
        )


def render_phase4_step7_feedback():
    """Show save result after a rerun, even if the analysis view disappears."""
    feedback = st.session_state.get("phase4_step7_feedback")
    if feedback:
        kind, message = feedback
        getattr(st, kind)(message)


def save_historical_research_record(conn, decision):
    """Explicit, idempotent decision-only snapshot; never create a trade/setup.

    A decision boundary + frozen strategy version is a logical research event.
    Refuse conflicting reruns rather than inserting a second event with a new hash.
    """
    if not isinstance(decision, dict) or decision.get("signal") not in ("BUY", "SELL", "NO TRADE"):
        raise ValueError("Only evaluated historical decisions can be recorded.")
    if not decision.get("decision_key") or not decision.get("decision_time_utc") or not decision.get("strategy_version"):
        raise ValueError("Decision identity is incomplete.")
    if decision.get("data_source") != "DELTA_INDIA_PUBLIC_API_COMPLETED_15M":
        raise ValueError("Historical record requires validated Delta completed-history provenance.")
    setup_database(conn)
    payload = dict(decision)
    payload["journal_record_type"] = "HISTORICAL_DECISION_ONLY"
    payload["execution_status"] = "NO_EXCHANGE_ORDER"
    payload["fill_status"] = "NOT_SIMULATED"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            """SELECT decision_key FROM strategy_decisions
               WHERE decision_time_utc=? AND strategy_version=? LIMIT 1""",
            (decision["decision_time_utc"], decision["strategy_version"])
        ).fetchone()
        if existing is not None:
            conn.rollback()
            return ("ALREADY_SAVED" if existing[0] == decision["decision_key"]
                    else "CONFLICTING_DECISION")
        conn.execute(
            """INSERT INTO strategy_decisions
               (decision_key,created_at,decision_time_utc,strategy_version,signal,strategy,payload_json)
               VALUES (?,?,?,?,?,?,?)""",
            (decision["decision_key"], now, decision["decision_time_utc"],
             decision["strategy_version"], decision["signal"], decision.get("strategy"),
             json.dumps(payload, sort_keys=True))
        )
        conn.commit()
        return "SAVED"
    except Exception:
        conn.rollback()
        raise


def render_minimal_journal():
    """Integrated journal; frozen engine, OCR, and saved decisions remain unchanged."""
    from research_journal_integrated import render_journal
    render_journal(get_connection, PROJECT_DIR,
                   st.session_state.get("display_mode", "Desktop"),
                   manual_renderer=render_trade_journal)


def render_terminal_styles():
    st.markdown("""
    <style>
    .block-container {max-width: 1500px; padding-top: 1.25rem;}
    div[data-testid="stMetric"] {border:1px solid rgba(130,150,180,.24);
        border-radius:12px;padding:14px 16px;background:rgba(110,130,160,.055);}
    div[data-testid="stMetricLabel"] {font-size:.82rem;}
    div[data-testid="stMetricValue"] {font-weight:750;}
    div[data-testid="stExpander"] {border-radius:12px;}
    div[data-testid="stFileUploader"] {border-radius:12px;}
    </style>
    """, unsafe_allow_html=True)


def render_signal_overview():
    st.markdown("## BTCUSD · Trading Signal Terminal")
    st.caption("Screenshot-based research · No real-money order placement · "
               "Only verified strategy inputs can produce a live decision")
    decision = st.session_state.get("last_strategy_decision")
    gate = st.session_state.get("strategy_gate", {})
    blockers = gate.get("blockers", [])
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Market data", "CHECK REQUIRED" if not decision else
                  ("REVIEW" if blockers else "READY"))
    with c2:
        st.metric("Selected strategy", (decision.get("strategy") or "NONE")
                  if decision else "NOT EVALUATED")
    with c3:
        st.metric("Trading signal", decision.get("signal", "NOT EVALUATED")
                  if decision else "NOT EVALUATED")
    if decision:
        cols = st.columns(4)
        for col, (label, key) in zip(cols, [
            ("Entry", "entry"), ("Stop-loss", "stop"),
            ("Take-profit", "target"), ("Risk / reward", "rr")]):
            with col:
                val = decision.get(key)
                st.metric(label, f"{val:,.2f}" if isinstance(val, (int, float)) else
                          (str(val) if val is not None else "—"))
        st.caption("Decision timestamp: " + str(decision.get("decision_time_utc", "unknown"))
                   + " · Source: completed historical candles, not a live exchange order")
    else:
        st.info("Upload screenshots and supply contiguous, up-to-date 15m history below. "
                "The signal stays NOT EVALUATED until the required inputs are available.")


# ============================================================
# 15. MAIN
# ============================================================

def render_footer():
    st.divider()
    st.caption("BTCUSD Research · Delta Exchange · Research only · No live orders")


def render_decision_first_summary(screenshots, analysis_requested):
    """Decision-first presentation. Never promote history preview to screenshot verification."""
    st.markdown("## TRADE DECISION")
    st.caption("Delta Exchange BTCUSD · Research only · No live orders")

    if not analysis_requested:
        st.info("NOT EVALUATED — Upload four charts, confirm the chart date, then select ANALYZE.")
        st.metric("Screenshots uploaded", f"{len(screenshots)}/4")
        return

    screenshot_decision = st.session_state.get("last_strategy_decision")
    history_decision = st.session_state.get("phase4_history_preview")
    history_error = st.session_state.get("phase4_history_error")
    blockers = st.session_state.get("strategy_gate", {}).get("blockers", [])

    if screenshot_decision is None:
        st.warning("NOT VERIFIED — No screenshot-aligned trade decision is confirmed.")
        st.caption("Screenshot evidence must be linked to a specific completed candle before "
                   "a screenshot-verified signal can be displayed.")
    else:
        st.success("SCREENSHOT-ALIGNED RESEARCH — " +
                   str(screenshot_decision.get("signal", "NOT EVALUATED")))

    st.markdown("#### Latest eligible completed-history research")
    if history_decision is None:
        st.info("NOT EVALUATED — " + str(history_error or "Historical verification incomplete."))
    else:
        result_col, strategy_col = st.columns(2)
        with result_col:
            _research_result_card("Historical engine result", str(history_decision.get("signal", "NOT EVALUATED")))
        with strategy_col:
            _research_result_card("Selected strategy", str(history_decision.get("strategy") or "NONE"))
        st.caption("Independent historical research only — NOT a screenshot-verified or live signal.")
        st.caption("Decision UTC: " + str(history_decision.get("decision_time_utc", "unknown")))
        if history_decision.get("signal") in ("BUY", "SELL"):
            cols = st.columns(3)
            for col, label, field in zip(cols, ("Entry", "Stop-loss", "Target"),
                                         ("entry", "stop", "target")):
                value = history_decision.get(field)
                with col:
                    _research_result_card(label, f"{value:,.2f}" if isinstance(value, (int, float))
                                          else "—")

    if isinstance(history_decision, dict):
        st.session_state["phase4_step7_pending_decision"] = dict(history_decision)
        st.button("Save Trade", type="primary", use_container_width=True,
                  key="main_save_trade", on_click=phase4_step7_save_callback,
                  help="Saves the historical research decision to the journal; does not place an order.")
    else:
        st.button("Save Trade", disabled=True, use_container_width=True,
                  key="main_save_trade_disabled",
                  help="Analyze and obtain an eligible historical decision before saving.")
    feedback = st.session_state.get("phase4_step7_feedback")
    if isinstance(feedback, tuple) and len(feedback) == 2:
        level, message = feedback
        getattr(st, level if level in ("success", "info", "warning", "error") else "info")(message)

    if blockers:
        st.caption("Screenshot verification blockers: " + "; ".join(map(str, blockers[:4])))
    st.caption("Open **View Decision Details** below for OCR, historical matching, "
               "five-strategy eligibility, explanations and journal.")


def _research_result_card(label, value):
    """Readable metric card without Streamlit metric ellipsis; layout stays unchanged."""
    import html
    safe_label = html.escape(str(label))
    safe_value = html.escape(str(value))
    st.markdown(
        '<div style="background:#FFFFFF;border:1px solid #CBD5E1;'
        'border-radius:12px;padding:14px 12px;min-height:94px;'
        'box-sizing:border-box;min-width:0;width:100%;">'
        '<div style="font-size:12px;color:#475569;line-height:1.35;'
        'overflow-wrap:anywhere;">' + safe_label + '</div>'
        '<div style="font-size:clamp(17px, 4vw, 29px);color:#17212F;'
        'font-weight:400;line-height:1.3;margin-top:7px;'
        'white-space:normal;overflow-wrap:anywhere;word-break:break-word;">'
        + safe_value + '</div></div>',
        unsafe_allow_html=True,
    )


def render_ocr_overview():
    """Display the four actual OCR rows together, never simulated prices."""
    rows = st.session_state.get("market_rows") or []
    st.markdown("#### Extracted OHLC · All Timeframes")
    mode = st.session_state.get("display_mode", "Desktop")
    for tf in TIMEFRAMES:
        row = next((r for r in rows if r.get("timeframe") == tf), {})
        def fmt(key):
            value = row.get(key)
            return f"{value:,.2f}" if isinstance(value, (float, int)) else "NOT VERIFIED"
        o, h, l, c = (row.get(k) for k in ("open", "high", "low", "close"))
        complete = all(isinstance(v, (int, float)) for v in (o,h,l,c))
        body = abs(c-o) if complete else None
        rng = h-l if complete else None
        ratio = 100*body/rng if complete and rng > 0 else None
        direction = ("BULLISH" if c>o else "BEARISH" if c<o else "DOJI") if complete else "NOT VERIFIED"
        with st.container(border=True):
            st.markdown(f"**{tf}**")
            st.code(
                f"OPEN={fmt('open')}  HIGH={fmt('high')}\n"
                f"LOW={fmt('low')}  CLOSE={fmt('close')}\n"
                f"BODY={body:.2f}  RANGE={rng:.2f}\n"
                f"BODY/RANGE={ratio:.2f}%\n"
                f"DIRECTION={direction}" if complete else
                f"OPEN={fmt('open')}  HIGH={fmt('high')}\n"
                f"LOW={fmt('low')}  CLOSE={fmt('close')}\n"
                "BODY=NOT VERIFIED  RANGE=NOT VERIFIED\n"
                "BODY/RANGE=NOT VERIFIED\nDIRECTION=NOT VERIFIED",
                language=None,
            )


def render_saved_model_results(previous_month_only=False):
    """Read actual persisted engine decisions; never synthesize results."""
    from datetime import date
    with get_connection() as conn:
        decisions = [dict(r) for r in conn.execute(
            "SELECT decision_time_utc, signal, strategy, payload_json, archived_at "
            "FROM strategy_decisions ORDER BY decision_time_utc DESC LIMIT 500"
        ).fetchall()]
    today = date.today()
    first_this_month = today.replace(day=1)
    previous_month_end = first_this_month - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    if previous_month_only:
        decisions = [d for d in decisions if
                     previous_month_start.isoformat() <= str(d["decision_time_utc"])[:10]
                     <= previous_month_end.isoformat()]
        st.caption(f"Completed month: {previous_month_start:%B %Y}")
    if not decisions:
        st.info("No saved model results for this period.")
        return
    st.caption("Saved model research results · Historical decisions, not executed trades")
    mode = st.session_state.get("display_mode", "Desktop")
    view = []
    for d in decisions:
        try:
            payload = json.loads(d["payload_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        view.append({
            "Date / Time": d["decision_time_utc"],
            "Signal": d["signal"],
            "Strategy": d["strategy"] or "NONE",
            "Entry": payload.get("entry"),
            "Target": payload.get("target"),
            "SL": payload.get("stop"),
            "Status": "Archived" if d["archived_at"] else "Saved",
        })
    if mode == "Desktop":
        st.dataframe(pd.DataFrame(view), hide_index=True, use_container_width=True)
    else:
        for item in view:
            with st.container(border=True):
                st.markdown(f"**{item['Signal']} · {item['Strategy']}**")
                st.caption(str(item["Date / Time"]))
                for label in ("Entry", "Target", "SL", "Status"):
                    st.write(f"**{label}:** {item[label] if item[label] is not None else '—'}")


def main():
    initialize_database()
    with st.sidebar:
        st.markdown("### BTCUSD Research")
        page = st.radio("Navigation", ["Research Page", "Trade Journal", "Research Analytics"],
                        key="btcusd_main_navigation")
        mode = render_display_mode()
        st.markdown("### Screenshot Upload")
        screenshots = screenshot_workspace()
        st.caption("One multi-file uploader · 15m, 1h, 3h, 4h")
        analyze = st.button("ANALYZE", type="primary", use_container_width=True,
                            key="sidebar_analyze")
        st.caption("Runs research; does not place an order.")

    # Keep the existing terminal accessible from the Research page.
    apply_theme(mode)
    apply_approved_theme()

    if page == "Trade Journal":
        st.title("Trade Journal")
        section = st.radio("Journal section", ["List of Trades", "Verification", "Archive"],
                           horizontal=(mode == "Desktop"))
        if section == "List of Trades":
            render_minimal_journal()
        elif section == "Verification":
            from research_journal_integrated import render_journal
            render_journal(get_connection, PROJECT_DIR, mode,
                           manual_renderer=render_trade_journal)
        else:
            with get_connection() as conn:
                archived = [dict(r) for r in conn.execute(
                    "SELECT decision_time_utc, signal, strategy, archived_at "
                    "FROM strategy_decisions WHERE archived_at IS NOT NULL "
                    "ORDER BY archived_at DESC").fetchall()]
            if archived:
                if mode == "Desktop":
                    st.dataframe(pd.DataFrame(archived), hide_index=True,
                                 use_container_width=True)
                else:
                    for item in archived:
                        with st.container(border=True):
                            st.write(item)
            else:
                st.info("No archived decisions.")
        return

    if page == "Research Analytics":
        st.title("Research Analytics")
        section = st.radio("Analytics section",
                           ["Previous Research", "Research of Previous Month"],
                           horizontal=(mode == "Desktop"))
        if section == "Previous Research":
            st.subheader("Previous Research — Model Results")
            render_saved_model_results()
            with st.expander("Existing Journal Analytics", expanded=False):
                from research_journal_integrated import render_analytics
                render_analytics(get_connection)
            with st.expander("Phase 5 Historical Analytics", expanded=False):
                render_phase5_analytics_dashboard(PROJECT_DIR)
        else:
            render_saved_model_results(previous_month_only=True)
        return

    st.title("BTCUSD Research")
    st.caption("Delta Exchange · Research only · No live orders")
    if st.session_state.get("phase6_screen") == "terminal":
        if st.button("Back to Research"):
            st.session_state["phase6_screen"] = "analysis"
            st.rerun()
        from phase6_terminal_view import render_phase6_terminal
        snapshot = st.session_state.get("phase6_saved_snapshot")
        if isinstance(snapshot, dict):
            render_phase6_terminal(snapshot, mode)
            return
        st.session_state["phase6_screen"] = "analysis"

    fingerprint = tuple(sorted((shot["name"], shot["sha256"]) for shot in screenshots))
    if st.session_state.get("phase1_upload_fingerprint") != fingerprint:
        st.session_state["phase1_upload_fingerprint"] = fingerprint
        st.session_state["phase1_run_requested"] = False
        st.session_state["last_strategy_decision"] = None
        st.session_state["phase4_history_preview"] = None
        st.session_state["phase4_history_error"] = None
        st.session_state["strategy_gate"] = {"blockers": []}
    if analyze:
        st.session_state["phase1_run_requested"] = True

    # Display main decision FIRST; analysis fills the same session state on rerun.
    decision_area = st.container()
    with st.expander("OCR Extraction & Date Confirmation", expanded=False):
        st.markdown("#### Research Date & Time")
        st.caption("Review the chart date used for historical matching. "
                   "Existing verification rules still apply.")
        st.text_input("Chart-local date (YYYY-MM-DD, UTC−6)",
                      key="phase3_chart_local_date", placeholder="YYYY-MM-DD")
        st.checkbox("Confirm date if automatic verification is unavailable",
                    key="phase3_date_confirmed")
        st.caption("No duplicate OHLC, candle metrics, or strategy results here.")

    date_signature = (st.session_state.get("phase3_chart_local_date", ""),
                      st.session_state.get("phase3_date_confirmed", False))
    if st.session_state.get("phase3_date_signature") != date_signature:
        st.session_state["phase3_date_signature"] = date_signature
        if screenshots:
            st.session_state["phase1_run_requested"] = True
    manual_signature = (
        st.session_state.get("phase3_4h_manual_countdown", "").strip(),
        bool(st.session_state.get("phase3_4h_manual_confirm", False)),
    )
    if st.session_state.get("phase3_manual_signature") != manual_signature:
        st.session_state["phase3_manual_signature"] = manual_signature
        if screenshots:
            st.session_state["phase1_run_requested"] = True

    if st.session_state.get("phase1_run_requested"):
        with st.expander("Historical Verification & Five-Strategy Comparison",
                         expanded=False):
            with st.expander("4H countdown fallback (only if OCR fails)", expanded=False):
                st.text_input("4H countdown (HH:MM:SS)",
                              key="phase3_4h_manual_countdown")
                st.checkbox("I read this countdown directly from the 4H screenshot",
                            key="phase3_4h_manual_confirm")
            render_integrated_research(screenshots)
    else:
        st.info("Upload charts and select ANALYZE to run the existing OCR and strategy workflow.")

    with decision_area:
        render_decision_first_summary(
            screenshots, bool(st.session_state.get("phase1_run_requested"))
        )
    st.caption("Existing OCR, verification, journal, and frozen strategy engine retained.")


if __name__ == "__main__":
    main()
