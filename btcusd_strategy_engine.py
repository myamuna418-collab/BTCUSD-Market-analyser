"""Historical-candle-based five-strategy research engine.
Candidate masks from Combination 485; indicators from Combination 490.
This module does not certify end-to-end replay parity or place exchange orders.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from btcusd_five_strategy_rules import Inputs, eligible_candidates, PRIORITY

VERSION = "C485_C490_RECONSTRUCTION_V2_ENTRY_PROVENANCE_CANDIDATE"

def _bars(history, frequency):
    d = history.set_index("time").sort_index()
    o = d.resample(frequency, label="left", closed="left", origin="epoch").agg(
        {"open":"first", "high":"max", "low":"min", "close":"last"})
    n = d["close"].resample(frequency, label="left", closed="left", origin="epoch").count()
    expected = int(pd.Timedelta(frequency) / pd.Timedelta(minutes=15))
    return o.loc[n == expected].dropna().copy()

def evaluate(history, decision_time):
    """Evaluate completed candles at an hourly UTC boundary, without future leakage."""
    decision = pd.Timestamp(decision_time)
    if decision.tzinfo is None:
        raise ValueError("Decision timestamp must include UTC timezone")
    decision = decision.tz_convert("UTC")
    if decision != decision.floor("h"):
        raise ValueError("Frozen research decisions must occur on hourly UTC boundaries")
    if history is None or history.empty:
        raise ValueError("Historical 15m candle data unavailable")
    h = history.copy()
    h["time"] = pd.to_datetime(h["time"], utc=True)
    h = h[h["time"] + pd.Timedelta(minutes=15) <= decision].sort_values("time")
    if h.empty or h.iloc[-1]["time"] != decision - pd.Timedelta(minutes=15):
        raise ValueError("No contiguous 15m data ending at decision boundary; update history")
    if h["time"].duplicated().any() or (h["time"].diff().dropna() != pd.Timedelta(minutes=15)).any():
        raise ValueError("Missing/duplicated 15m candles in the historical window")
    hour = _bars(h, "1h")
    four = _bars(h, "4h")
    if hour.empty or four.empty or hour.index[-1] != decision - pd.Timedelta(hours=1):
        raise ValueError("Latest completed 1H candle unavailable")
    if four.index[-1] + pd.Timedelta(hours=4) > decision:
        raise ValueError("4H candle still forming")
    prev = hour["close"].shift(1)
    tr = pd.concat([hour["high"]-hour["low"],(hour["high"]-prev).abs(),
                    (hour["low"]-prev).abs()],axis=1).max(axis=1)
    atr = tr.rolling(14,min_periods=14).mean()
    momentum = (hour["close"]-hour["close"].shift(3))/atr
    median = atr.shift(1).rolling(720,min_periods=720).median()
    fp = four["close"].shift(1)
    ftr = pd.concat([four["high"]-four["low"],(four["high"]-fp).abs(),
                     (four["low"]-fp).abs()],axis=1).max(axis=1)
    up = four["high"].diff()
    down = -four["low"].diff()
    plus = pd.Series(np.where((up>down)&(up>0),up,0.0),index=four.index)
    minus = pd.Series(np.where((down>up)&(down>0),down,0.0),index=four.index)
    smooth = ftr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    pdi = 100*plus.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/smooth
    mdi = 100*minus.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/smooth
    bar = h.iloc[-1]
    av,mo,pv,mv,med = map(float,(atr.iloc[-1],momentum.iloc[-1],pdi.iloc[-1],mdi.iloc[-1],median.iloc[-1]))
    if not all(np.isfinite(v) for v in (av,mo,pv,mv,med)) or av<=0 or med<=0:
        raise ValueError("Indicator warm-up insufficient: 720 completed 1H ATR observations required")
    inputs = Inputs(float(bar["open"]),float(bar["high"]),float(bar["low"]),
                    float(bar["close"]),av,pv,mv,mo,True)
    eligible = eligible_candidates(inputs)
    result = {
        "decision_time_utc":decision.isoformat(),
        "signal_candle_open_utc":bar["time"].isoformat(),
        "strategy_version":VERSION,
        "eligible_candidates":[{"strategy":n,"direction":d} for n,d in eligible],
        "atr_1h_14":av,"momentum_3h_atr":mo,"plus_di_4h":pv,"minus_di_4h":mv,
        "relative_volatility":av/med,
        "data_source":"LOCAL_15M_HISTORY_NOT_INDEPENDENTLY_EXCHANGE_VERIFIED",
        "signal":"NO TRADE","strategy":None,"entry":None,"stop":None,
        "target":None,"rr":None,"expiry_utc":None,
        "reason":"No five-strategy candidate eligible at the decision boundary."
    }
    if eligible:
        name,direction = eligible[0] # priority documented in Combination 485
        lower = min(inputs.open,inputs.close)-inputs.low
        upper = inputs.high-max(inputs.open,inputs.close)
        if name == "ORIGINAL_WICK":
            entry = inputs.low + .25*lower if direction=="BUY" else inputs.high-.25*upper
        elif name == "IMPULSE_PULLBACK":
            entry = (inputs.high + inputs.low) / 2.0
        elif name in ("PULLBACK", "CONTINUATION", "REVERSAL"):
            fraction = .50 if name == "PULLBACK" else .25
            proposed = inputs.close - fraction*(inputs.high-inputs.low) if direction=="BUY" else inputs.close + fraction*(inputs.high-inputs.low)
            entry = min(inputs.high, max(inputs.low, proposed))
        else:
            raise ValueError("Unexpected strategy: " + str(name))
        sign = 1 if direction=="BUY" else -1
        result.update(signal=direction,strategy=name,entry=entry,
                      stop=entry-sign*av,target=entry+sign*.5*av,rr=.5,
                      expiry_utc=(decision+pd.Timedelta(hours=1)).isoformat(),
                      reason="First eligible candidate in frozen strategy priority order; indicative limit fill levels.")
    result["decision_key"]=hashlib.sha256(json.dumps({
        "decision":result["decision_time_utc"],"version":VERSION,
        "signal":result["signal"],"strategy":result["strategy"],
        "signal_candle":result["signal_candle_open_utc"],
        "ohlc":[float(bar[k]) for k in ("open","high","low","close")]
    },sort_keys=True).encode()).hexdigest()
    return result

def setup_database(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS strategy_decisions(
        decision_key TEXT PRIMARY KEY, created_at TEXT NOT NULL,
        decision_time_utc TEXT NOT NULL, strategy_version TEXT NOT NULL,
        signal TEXT NOT NULL, strategy TEXT, payload_json TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS research_setups(
        decision_key TEXT PRIMARY KEY, created_at TEXT NOT NULL,
        direction TEXT NOT NULL, strategy TEXT NOT NULL, entry REAL NOT NULL,
        stop REAL NOT NULL, target REAL NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
        updated_at TEXT NOT NULL, outcome_source TEXT NOT NULL DEFAULT 'NONE',
        realized_r REAL, FOREIGN KEY(decision_key) REFERENCES strategy_decisions(decision_key))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS research_setup_audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT, decision_key TEXT NOT NULL,
        changed_at TEXT NOT NULL, previous_status TEXT, new_status TEXT NOT NULL,
        evidence_source TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '')""")

def save_decision(conn, decision):
    setup_database(conn)
    now=datetime.now(timezone.utc).isoformat()
    with conn:
        cur=conn.execute("INSERT OR IGNORE INTO strategy_decisions VALUES(?,?,?,?,?,?,?)",
            (decision["decision_key"],now,decision["decision_time_utc"],
             decision["strategy_version"],decision["signal"],decision["strategy"],
             json.dumps(decision,sort_keys=True)))
        if cur.rowcount and decision["signal"] in ("BUY","SELL"):
            conn.execute("""INSERT OR IGNORE INTO research_setups
                (decision_key,created_at,direction,strategy,entry,stop,target,status,updated_at)
                VALUES (?,?,?,?,?,?,?,'PENDING',?)""",
                (decision["decision_key"],now,decision["signal"],decision["strategy"],
                 decision["entry"],decision["stop"],decision["target"],now))
            conn.execute("""INSERT INTO research_setup_audit
                (decision_key,changed_at,previous_status,new_status,evidence_source,notes)
                VALUES (?,?,NULL,'PENDING','STRATEGY_ENGINE','Research setup created')""",
                (decision["decision_key"],now))
    return bool(cur.rowcount)

def update_setup(conn, key, new_status, evidence_source, notes=""):
    allowed={"PENDING":{"FILLED","UNFILLED","EXPIRED"},
             "FILLED":{"TARGET","STOP","TIMEOUT"}}
    setup_database(conn)
    row=conn.execute("SELECT status,entry,stop,target,direction FROM research_setups WHERE decision_key=?",(key,)).fetchone()
    if row is None: raise ValueError("Unknown setup")
    old,entry,stop,target,direction=row
    if new_status not in allowed.get(old,set()):
        raise ValueError(f"Invalid status transition: {old} -> {new_status}")
    if not evidence_source.strip(): raise ValueError("Evidence source required")
    r = .5 if new_status=="TARGET" else -1.0 if new_status=="STOP" else None
    now=datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("UPDATE research_setups SET status=?,updated_at=?,outcome_source=?,realized_r=? WHERE decision_key=?",
                     (new_status,now,evidence_source,r,key))
        conn.execute("""INSERT INTO research_setup_audit
            (decision_key,changed_at,previous_status,new_status,evidence_source,notes)
            VALUES(?,?,?,?,?,?)""",(key,now,old,new_status,evidence_source,notes))

def advance_from_history(conn, history):
    """Conservative 15m OHLC simulation; gaps never imply elapsed candles.

    This is research-only modeling, not evidence of exchange execution.
    """
    setup_database(conn)
    if history is None or history.empty:
        return []
    bars = history.copy()
    bars["time"] = pd.to_datetime(bars["time"], utc=True)
    bars = bars.sort_values("time")
    now = pd.Timestamp.now(tz="UTC")
    bars = bars[bars["time"] + pd.Timedelta(minutes=15) <= now]
    pending = conn.execute("""SELECT s.decision_key,s.status,s.direction,s.entry,s.stop,s.target,
        d.decision_time_utc FROM research_setups s
        JOIN strategy_decisions d ON d.decision_key=s.decision_key
        WHERE s.status IN ('PENDING','FILLED')""").fetchall()
    updates = []
    interval = pd.Timedelta(minutes=15)
    for key, status, direction, entry, stop, target, decision in pending:
        start = pd.Timestamp(decision)
        if start.tzinfo is None:
            continue
        start = start.tz_convert("UTC")
        future = bars[bars["time"] >= start].head(52)
        if future.empty or future.iloc[0]["time"] != start:
            continue

        # Only the uninterrupted prefix beginning at the decision boundary
        # is usable. A missing or duplicate bar stops evaluation immediately.
        times = future["time"].reset_index(drop=True)
        contiguous_count = 1
        for i in range(1, len(times)):
            if times.iloc[i] != times.iloc[i - 1] + interval:
                break
            contiguous_count += 1
        future = future.iloc[:contiguous_count].reset_index(drop=True)
        sign = 1 if direction == "BUY" else -1
        fill_at = None

        if status == "PENDING":
            for i, (_, bar) in enumerate(future.head(4).iterrows()):
                # A limit price must actually lie inside the candle range.
                # One-sided comparisons alone falsely fill off-range limits.
                if bar["low"] <= entry <= bar["high"]:
                    fill_at = i
                    break
            if fill_at is None:
                if len(future) >= 4:
                    update_setup(conn, key, "UNFILLED",
                                 "LOCAL_HISTORICAL_15M_SIMULATION",
                                 "Four contiguous entry-validity candles elapsed")
                    updates.append((key, "UNFILLED"))
                continue
            update_setup(conn, key, "FILLED",
                         "LOCAL_HISTORICAL_15M_SIMULATION",
                         "fill_open_utc=" + future.iloc[fill_at]["time"].isoformat())
            updates.append((key, "FILLED"))
        else:
            audit = conn.execute("""SELECT notes FROM research_setup_audit
                WHERE decision_key=? AND new_status='FILLED'
                ORDER BY id DESC LIMIT 1""", (key,)).fetchone()
            if not audit or not audit[0].startswith("fill_open_utc="):
                continue
            try:
                fill_time = pd.Timestamp(audit[0].split("=", 1)[1])
            except ValueError:
                continue
            matches = future.index[future["time"] == fill_time].tolist()
            if not matches:
                continue
            fill_at = matches[0]

        holding = future.iloc[fill_at:fill_at + 48]
        for j, (_, bar) in enumerate(holding.iterrows()):
            stop_hit = bar["low"] <= stop if sign == 1 else bar["high"] >= stop
            target_hit = bar["high"] >= target if sign == 1 else bar["low"] <= target
            # Frozen model: stop takes priority; target is not credited
            # on the entry candle.
            if stop_hit:
                update_setup(conn, key, "STOP",
                             "LOCAL_HISTORICAL_15M_SIMULATION",
                             "Stop touched; conservative stop priority")
                updates.append((key, "STOP"))
                break
            if j > 0 and target_hit:
                update_setup(conn, key, "TARGET",
                             "LOCAL_HISTORICAL_15M_SIMULATION",
                             "Target touched on post-entry candle")
                updates.append((key, "TARGET"))
                break
        else:
            if len(holding) >= 48:
                update_setup(conn, key, "TIMEOUT",
                             "LOCAL_HISTORICAL_15M_SIMULATION",
                             "Maximum 48 contiguous holding candles elapsed")
                updates.append((key, "TIMEOUT"))
    return updates
