
"""Local historical OHLC matching. Research only; no network, model deserialization or orders."""
from datetime import datetime, timezone, timedelta
import math
import pandas as pd

TIMEFRAMES = {"15m":15,"1h":60,"3h":180,"4h":240}
FIELDS = ("open","high","low","close")

def aggregate_history(history, timeframe):
    minutes = TIMEFRAMES[timeframe]
    f = history.copy().sort_values("time")
    f["time"] = pd.to_datetime(f["time"], utc=True)
    f = f.set_index("time")
    if minutes == 15:
        out = f.reset_index()[["time",*FIELDS]].copy()
        out["count"] = 1
        return out
    # UTC epoch anchored. Exchange-specific 3h/4h anchor must be independently confirmed.
    rule = f"{minutes}min"
    grouped = f.resample(rule, origin="epoch", label="left", closed="left")
    out = grouped.agg({"open":"first","high":"max","low":"min","close":"last"})
    out["count"] = grouped["open"].count()
    out = out[out["count"] == minutes//15].dropna().reset_index()
    return out

def find_candidates(row, history, tolerance=0.011):
    tf = row.get("timeframe")
    if tf not in TIMEFRAMES or history is None:
        return [], "Missing historical data or unknown timeframe"
    try:
        values = [float(row[k]) for k in FIELDS]
        o,h,l,c = values
        if not all(math.isfinite(v) and v>0 for v in values) or l>min(o,c) or h<max(o,c):
            return [], "Missing or invalid screenshot OHLC"
    except (TypeError,ValueError,KeyError):
        return [], "Missing or invalid screenshot OHLC"
    frame = aggregate_history(history,tf)
    mask = pd.Series(True,index=frame.index)
    for key,value in zip(FIELDS,values):
        mask &= (frame[key]-value).abs() <= tolerance
    return frame.loc[mask].to_dict("records"), None

def validate_historical_rows(rows, history, source_metadata=None, capture_time_utc=None):
    """Returns rows with unique auto-time, and detailed evidence. No guessed times."""
    meta = source_metadata or {}
    independent = meta.get("independent_delta_api_verified") is True
    now = datetime.now(timezone.utc)
    results=[]
    updated=[]
    for row in rows:
        row=dict(row)
        tf=row.get("timeframe")
        matches, err=find_candidates(row,history)
        issues=[err] if err else []
        chosen=None
        if len(matches)==1:
            chosen=pd.Timestamp(matches[0]["time"]).to_pydatetime()
            existing=str(row.get("candle_open_utc") or "").strip()
            if existing:
                try:
                    manual=datetime.fromisoformat(existing.replace("Z","+00:00"))
                    if manual.tzinfo is None or manual.astimezone(timezone.utc)!=chosen:
                        issues.append("Entered candle time disagrees with unique OHLC match")
                        chosen=None
                except ValueError:
                    issues.append("Invalid manually entered candle timestamp")
                    chosen=None
            if chosen:
                row["candle_open_utc"]=chosen.isoformat().replace("+00:00","Z")
        elif len(matches)>1:
            issues.append(f"{len(matches)} historical candles match; timestamp ambiguous")
        elif not err:
            issues.append("No matching OHLC in available historical data")
        complete_now=None
        complete_at_capture=None
        if chosen:
            close_time=chosen+timedelta(minutes=TIMEFRAMES[tf])
            complete_now=close_time<=now
            if capture_time_utc is not None:
                complete_at_capture=close_time<=capture_time_utc
            if not complete_now:
                issues.append("Matched candle has not yet closed")
        if not independent:
            issues.append("Local dataset is not independently Delta API verified")
        if capture_time_utc is None:
            issues.append("Screenshot capture time not independently established")
        if not matches:
            status="INSUFFICIENT DATA" if history is None else "MISMATCH"
        elif len(matches)>1:
            status="AMBIGUOUS"
        elif chosen is None:
            status="MISMATCH"
        elif not complete_now:
            status="INCOMPLETE"
        elif not independent:
            status="LOCAL MATCH — UNVERIFIED SOURCE"
        elif complete_at_capture is None:
            status="LOCAL MATCH — CAPTURE UNCONFIRMED"
        elif not complete_at_capture:
            status="INCOMPLETE"
        else:
            status="VERIFIED"
        results.append({
            "Timeframe":tf,
            "OHLC structure":"PASS" if not err else "FAIL",
            "Candidates":len(matches),
            "Candle OPEN UTC":row.get("candle_open_utc") or "",
            "Completed now":complete_now,
            "Completed at capture":complete_at_capture,
            "Historical OHLC":"MATCH" if len(matches)==1 else ("AMBIGUOUS" if len(matches)>1 else "NO MATCH"),
            "Source independently verified":independent,
            "Status":status,
            "Issues":"; ".join(issues),
        })
        updated.append(row)
    return updated,results
