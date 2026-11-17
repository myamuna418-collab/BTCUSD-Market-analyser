"""Additive journal outcome review and period reports. No engine/OCR mutation."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import streamlit as st

OUTCOMES = ("TARGET", "STOP", "TIMEOUT", "UNFILLED")
REVIEW = "NEEDS CONFIRMATION"
AUTO = "AUTO VERIFIED — MODELED"
USER = "USER CONFIRMED"

def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS decision_outcome_reviews(
        decision_key TEXT PRIMARY KEY, status TEXT NOT NULL,
        outcome TEXT, evidence_source TEXT NOT NULL, evidence_notes TEXT NOT NULL,
        realized_r REAL, evaluated_at TEXT NOT NULL, evidence_hash TEXT,
        FOREIGN KEY(decision_key) REFERENCES strategy_decisions(decision_key))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS decision_outcome_review_audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT, decision_key TEXT NOT NULL,
        changed_at TEXT NOT NULL, previous_status TEXT, previous_outcome TEXT,
        new_status TEXT NOT NULL, new_outcome TEXT, evidence_source TEXT NOT NULL,
        notes TEXT NOT NULL, evidence_hash TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS journal_period_reports(
        period_type TEXT NOT NULL, period_key TEXT NOT NULL, revision INTEGER NOT NULL,
        generated_at TEXT NOT NULL, source_hash TEXT NOT NULL, report_json TEXT NOT NULL,
        PRIMARY KEY(period_type,period_key,revision))""")

def read_decisions(conn, archived=None):
    clause = ("WHERE archived_at IS NULL" if archived is False else
              "WHERE archived_at IS NOT NULL" if archived is True else "")
    rows = conn.execute("SELECT * FROM strategy_decisions "+clause+
                        " ORDER BY decision_time_utc DESC").fetchall()
    return [dict(r) for r in rows]

def reviews(conn):
    schema(conn)
    return {r["decision_key"]:dict(r) for r in conn.execute("SELECT * FROM decision_outcome_reviews")}

def record_review(conn,key,status,outcome,source,notes,evidence_hash=None):
    if status not in (REVIEW,AUTO,USER):
        raise ValueError("Invalid review status")
    if status == REVIEW and outcome is not None:
        raise ValueError("Unresolved reviews cannot contain an outcome")
    if status != REVIEW and outcome not in OUTCOMES:
        raise ValueError("Invalid resolved outcome")
    if status == USER and not notes.strip():
        raise ValueError("Evidence notes are required for manual confirmation")
    row=conn.execute("SELECT signal,payload_json FROM strategy_decisions WHERE decision_key=?",(key,)).fetchone()
    if row is None or row["signal"] not in ("BUY","SELL"):
        raise ValueError("Only saved BUY/SELL decisions can be reviewed")
    payload=json.loads(row["payload_json"])
    if status == AUTO and (not evidence_hash or not source.startswith("LOCAL_15M_")):
        raise ValueError("Automated outcome requires historical evidence")
    r = (float(payload.get("rr") or .5) if outcome=="TARGET" else
         -1.0 if outcome=="STOP" else None)
    old=conn.execute("SELECT status,outcome,evidence_hash FROM decision_outcome_reviews WHERE decision_key=?",(key,)).fetchone()
    if old and old["status"]==USER and status!=USER:
        return False  # Manual decisions must not be overwritten by refreshes.
    if old and (old["status"],old["outcome"],old["evidence_hash"]) == (status,outcome,evidence_hash):
        return False
    stamp=now_utc()
    with conn:
        conn.execute("""INSERT INTO decision_outcome_reviews
            (decision_key,status,outcome,evidence_source,evidence_notes,realized_r,evaluated_at,evidence_hash)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(decision_key) DO UPDATE SET
            status=excluded.status,outcome=excluded.outcome,
            evidence_source=excluded.evidence_source,evidence_notes=excluded.evidence_notes,
            realized_r=excluded.realized_r,evaluated_at=excluded.evaluated_at,
            evidence_hash=excluded.evidence_hash""",
            (key,status,outcome,source,notes,r,stamp,evidence_hash))
        conn.execute("""INSERT INTO decision_outcome_review_audit
            (decision_key,changed_at,previous_status,previous_outcome,new_status,new_outcome,
             evidence_source,notes,evidence_hash) VALUES(?,?,?,?,?,?,?,?,?)""",
            (key,stamp,old["status"] if old else None,old["outcome"] if old else None,
             status,outcome,source,notes,evidence_hash))
    return True

def _utc(value):
    t=pd.Timestamp(value)
    if t.tzinfo is None:
        raise ValueError("Timestamp missing timezone")
    return t.tz_convert("UTC")

def evaluate_outcome(payload, history):
    """Conservative independent replay of frozen engine's 4 entry / 48 hold bars.
    An intrabar ordering ambiguity is NOT resolved by invented tick sequencing.
    """
    if payload.get("signal") not in ("BUY","SELL"):
        return None,None,"NO TRADE",None
    required=("entry","stop","target","decision_time_utc","expiry_utc")
    if any(payload.get(k) is None for k in required):
        return REVIEW,None,"Missing saved entry, stop, target, decision time, or expiry",None
    try:
        start=_utc(payload["decision_time_utc"])
        expiry=_utc(payload["expiry_utc"])
        entry,stop,target=(float(payload[k]) for k in ("entry","stop","target"))
    except (ValueError,TypeError,OverflowError):
        return REVIEW,None,"Saved trade rules are malformed",None
    if expiry != start+pd.Timedelta(hours=1):
        return REVIEW,None,"Saved entry expiry differs from frozen 4-candle rule",None
    if not (stop<entry<target if payload["signal"]=="BUY" else target<entry<stop):
        return REVIEW,None,"Saved price ordering invalid",None
    if history is None or history.empty:
        return REVIEW,None,"Validated 15m history unavailable",None
    future=history.loc[history["time"]>=start].head(52).reset_index(drop=True)
    if future.empty or future.iloc[0]["time"]!=start:
        return REVIEW,None,"15m history does not begin at the decision boundary",None
    for i in range(1,len(future)):
        if future.iloc[i]["time"] != future.iloc[i-1]["time"]+pd.Timedelta(minutes=15):
            return REVIEW,None,"Missing or duplicate 15m candles in verification window",None
    # A forming bar is not admissible.
    now=pd.Timestamp.now(tz="UTC")
    future=future[future["time"]+pd.Timedelta(minutes=15)<=now].reset_index(drop=True)
    if future.empty:
        return REVIEW,None,"No completed future candles",None
    sign=1 if payload["signal"]=="BUY" else -1
    fill=None
    for i,bar in future.head(4).iterrows():
        if bar["low"]<=entry<=bar["high"]:
            fill=i
            break
    evidence_hash=hashlib.sha256(pd.util.hash_pandas_object(
        future[["time","open","high","low","close"]],index=False).values.tobytes()).hexdigest()
    if fill is None:
        if len(future)>=4:
            return AUTO,"UNFILLED","No entry touch in four complete contiguous entry candles",evidence_hash
        return REVIEW,None,"Entry window has not completed",evidence_hash
    holding=future.iloc[fill:fill+48]
    for j,bar in enumerate(holding.itertuples(index=False)):
        stop_hit=(bar.low<=stop if sign==1 else bar.high>=stop)
        target_hit=(bar.high>=target if sign==1 else bar.low<=target)
        if j==0 and (stop_hit or target_hit):
            return REVIEW,None,"Entry candle touched an exit level; fill/exit order unknown",evidence_hash
        if stop_hit and target_hit:
            return REVIEW,None,"Target and stop touched in the same 15m candle",evidence_hash
        if stop_hit:
            return AUTO,"STOP","Stop touched after entry on a distinct candle",evidence_hash
        if j>0 and target_hit:
            return AUTO,"TARGET","Target touched on a post-entry candle",evidence_hash
    if len(holding)>=48:
        return AUTO,"TIMEOUT","48 contiguous completed holding candles without exit",evidence_hash
    return REVIEW,None,"Holding window incomplete; awaiting future candles",evidence_hash

def validated_history(project_dir):
    path=Path(project_dir)/"BTCUSD_15m.csv"
    if not path.is_file():
        raise ValueError("BTCUSD_15m.csv not found")
    raw=pd.read_csv(path)
    needed={"time","open","high","low","close"}
    if not needed.issubset(raw.columns):
        raise ValueError("Historical CSV is missing required candle columns")
    result=raw[list(needed)].copy()
    result["time"]=pd.to_datetime(result["time"],utc=True,errors="raise")
    for field in ("open","high","low","close"):
        result[field]=pd.to_numeric(result[field],errors="raise")
    if result.isna().any().any():
        raise ValueError("Historical CSV has null candle values")
    if result["time"].duplicated().any():
        raise ValueError("Historical CSV has duplicate 15m timestamps")
    if not ((result["low"]>0)&(result["low"]<=result[["open","close"]].min(axis=1))&
            (result["high"]>=result[["open","close"]].max(axis=1))).all():
        raise ValueError("Historical CSV has invalid OHLC candles")
    if not ((result["time"].dt.minute%15==0)&(result["time"].dt.second==0)).all():
        raise ValueError("Historical CSV has non-15m-aligned timestamps")
    return result.sort_values("time").reset_index(drop=True)

def verify_one(conn,decision,history):
    key=decision["decision_key"]
    existing=reviews(conn).get(key)
    if existing and existing["status"]==USER:
        return existing
    try:
        payload=json.loads(decision["payload_json"])
        status,outcome,reason,digest=evaluate_outcome(payload,history)
    except (ValueError,TypeError,KeyError) as exc:
        status,outcome,reason,digest=REVIEW,None,"Verification error: "+str(exc),None
    if status is not None:
        record_review(conn,key,status,outcome,"LOCAL_15M_MODELED_REPLAY",reason,digest)
    return reviews(conn).get(key)

def restore_decision(conn,key):
    stamp=now_utc()
    with conn:
        changed=conn.execute("UPDATE strategy_decisions SET archived_at=NULL WHERE decision_key=? AND archived_at IS NOT NULL",(key,))
        if changed.rowcount!=1:
            raise ValueError("Decision is not archived")
        conn.execute("""INSERT INTO historical_decision_archive_audit
            (decision_key,changed_at,action,evidence_source,notes)
            VALUES(?,?,'RESTORED','USER_CONFIRMED_RESTORE','Restored to active journal; original payload retained')""",(key,stamp))

def period_key(date,kind):
    return date[:7] if kind=="MONTH" else date[:4]

def report_snapshot(conn,kind,key):
    all_rows=read_decisions(conn)
    state=reviews(conn)
    relevant=[r for r in all_rows if period_key(r["decision_time_utc"],kind)==key]
    outcomes=[(r,state.get(r["decision_key"])) for r in relevant if r["signal"] in ("BUY","SELL")]
    resolved=[(r,v) for r,v in outcomes if v and v["status"] in (AUTO,USER)]
    win=sum(v["outcome"]=="TARGET" for _,v in resolved)
    loss=sum(v["outcome"]=="STOP" for _,v in resolved)
    by_strategy={}
    for r,v in outcomes:
        name=r["strategy"] or "UNKNOWN"
        slot=by_strategy.setdefault(name,{"decisions":0,"target":0,"stop":0,"timeout":0,"unfilled":0,"unresolved":0})
        slot["decisions"]+=1
        k=v["outcome"].lower() if v and v["outcome"] else "unresolved"
        slot[k]+=1
    return {"period_type":kind,"period":key,"decision_count":len(relevant),
            "trade_decisions":len(outcomes),"no_trade":sum(r["signal"]=="NO TRADE" for r in relevant),
            "auto_verified":sum(v["status"]==AUTO for _,v in resolved),
            "user_confirmed":sum(v["status"]==USER for _,v in resolved),
            "unresolved":len(outcomes)-len(resolved),
            "target":win,"stop":loss,
            "timeout":sum(v["outcome"]=="TIMEOUT" for _,v in resolved),
            "unfilled":sum(v["outcome"]=="UNFILLED" for _,v in resolved),
            "win_rate_pct":round(100*win/(win+loss),2) if win+loss else None,
            "known_r":round(sum(v["realized_r"] for _,v in resolved if v["realized_r"] is not None),3),
            "strategies":by_strategy,
            "caveat":"Research outcomes only. No independent exchange execution verification."}

def update_closed_reports(conn):
    schema(conn)
    current=pd.Timestamp.now(tz="UTC")
    decisions=read_decisions(conn)
    for kind in ("MONTH","YEAR"):
        keys=sorted({period_key(r["decision_time_utc"],kind) for r in decisions})
        for key in keys:
            end=(pd.Period(key,freq="M").end_time if kind=="MONTH" else
                 pd.Period(key,freq="Y").end_time).tz_localize("UTC")
            if end>=current:
                continue
            report=report_snapshot(conn,kind,key)
            payload=json.dumps(report,sort_keys=True)
            digest=hashlib.sha256(payload.encode()).hexdigest()
            last=conn.execute("""SELECT revision,source_hash FROM journal_period_reports
                WHERE period_type=? AND period_key=? ORDER BY revision DESC LIMIT 1""",(kind,key)).fetchone()
            if last and last["source_hash"]==digest:
                continue
            revision=last["revision"]+1 if last else 1
            with conn:
                conn.execute("""INSERT INTO journal_period_reports
                    (period_type,period_key,revision,generated_at,source_hash,report_json)
                    VALUES(?,?,?,?,?,?)""",(kind,key,revision,now_utc(),digest,payload))

def report_history(conn):
    return [dict(r) for r in conn.execute("""SELECT * FROM journal_period_reports
        ORDER BY period_key DESC,period_type,revision DESC""").fetchall()]

def _status_text(review):
    return (review["status"] + (" · "+review["outcome"] if review["outcome"] else "")
            if review else "NOT REVIEWED")

@st.dialog("Verify / Confirm Research Outcome",width="large")
def verify_dialog(decision,connection_factory,project_dir):
    st.caption("Historical 15m OHLC modeling · Not exchange execution verification")
    st.write("**"+decision["signal"]+" · "+(decision["strategy"] or "UNKNOWN")+"**")
    payload=json.loads(decision["payload_json"])
    cols=st.columns(3)
    for col,name in zip(cols,("entry","stop","target")):
        col.metric(name.title(),f'{payload.get(name):,.2f}' if isinstance(payload.get(name),(float,int)) else "—")
    with connection_factory() as conn:
        schema(conn)
        previous=reviews(conn).get(decision["decision_key"])
    st.write("Current: **"+_status_text(previous)+"**")
    if previous:
        st.caption(previous["evidence_notes"])
    if st.button("Run automatic verification",key="verify_run_"+decision["decision_key"],type="primary"):
        try:
            history=validated_history(project_dir)
            with connection_factory() as conn:
                verify_one(conn,decision,history)
            st.rerun()
        except (OSError,ValueError,sqlite3.Error) as exc:
            st.error("Verification could not run: "+str(exc))
    st.divider()
    st.write("**Manual confirmation (only with supporting evidence)**")
    with st.form("confirm_"+decision["decision_key"]):
        outcome=st.selectbox("Observed outcome",OUTCOMES)
        notes=st.text_area("Evidence notes (required)",placeholder="Explain source, timing and why the outcome is supported")
        confirm=st.form_submit_button("Save user-confirmed outcome")
    if confirm:
        try:
            with connection_factory() as conn:
                schema(conn)
                record_review(conn,decision["decision_key"],USER,outcome,"USER_REPORTED_EVIDENCE",notes)
            st.rerun()
        except (ValueError,sqlite3.Error) as exc:
            st.error(str(exc))

def render_journal(connection_factory,project_dir,display_mode="Auto",manual_renderer=None):
    from phase6_decision_journal import show_decision_details, confirm_decision_archive
    with connection_factory() as conn:
        schema(conn)
        active=read_decisions(conn,archived=False)
        state=reviews(conn)
    st.subheader("Trading Journal")
    st.caption("Saved frozen-engine decisions · Research only · No live orders")
    trades=[r for r in active if r["signal"] in ("BUY","SELL")]
    resolved=[state[r["decision_key"]] for r in trades if r["decision_key"] in state and state[r["decision_key"]]["status"] in (AUTO,USER)]
    wins=sum(v["outcome"]=="TARGET" for v in resolved)
    losses=sum(v["outcome"]=="STOP" for v in resolved)
    cols=st.columns(4)
    for col,label,value in zip(cols,("Decisions","Auto Verified","Needs Review","Win Rate"),
        (len(active),sum(v["status"]==AUTO for v in resolved),
         sum(not state.get(r["decision_key"]) or state[r["decision_key"]]["status"]==REVIEW for r in trades),
         f"{100*wins/(wins+losses):.1f}%" if wins+losses else "—")):
        col.metric(label,value)
    search=st.text_input("Search decisions",placeholder="Date, strategy, signal or outcome",key="integrated_search")
    filt=st.selectbox("Filter",("All","BUY / SELL","NO TRADE","Needs Review","Auto Verified","User Confirmed"),key="integrated_filter")
    limit=st.selectbox("Rows per page",(10,20,50),key="integrated_page_size")
    visible=[]
    for r in active:
        v=state.get(r["decision_key"])
        if filt=="BUY / SELL" and r["signal"]=="NO TRADE": continue
        if filt=="NO TRADE" and r["signal"]!="NO TRADE": continue
        if filt=="Needs Review" and (r["signal"]=="NO TRADE" or (v and v["status"]!=REVIEW)): continue
        if filt=="Auto Verified" and (not v or v["status"]!=AUTO): continue
        if filt=="User Confirmed" and (not v or v["status"]!=USER): continue
        searchable=" ".join(str(x) for x in (r["decision_time_utc"],r["signal"],r["strategy"],_status_text(v)))
        if search.lower() not in searchable.lower(): continue
        visible.append(r)
    page_count=max(1,(len(visible)+limit-1)//limit)
    page=st.selectbox("Page",list(range(1,page_count+1)),key="integrated_page")
    st.caption(f"{len(visible)} matching decisions · Page {page} of {page_count}")
    head=st.columns([.5,2,1,1.5,.7,.8,.45])
    for c,h in zip(head,("Archive","Decision UTC","Signal","Strategy","Outcome","View","Verify")):
        c.caption(h)
    for r in visible[(page-1)*limit:page*limit]:
        key=r["decision_key"]
        v=state.get(key)
        cols=st.columns([.5,2,1,1.5,.7,.8,.45],vertical_alignment="center")
        with cols[0]:
            st.checkbox("Select",key="phase6_decision_selected_"+key,label_visibility="collapsed")
        cols[1].write(r["decision_time_utc"].replace("T"," ")[:16])
        cols[2].write(r["signal"])
        cols[3].write(r["strategy"] or "—")
        cols[4].caption(v["outcome"] if v and v["outcome"] else ("Review" if r["signal"]!="NO TRADE" else "—"))
        with cols[5]:
            if st.button("View",key="integrated_view_"+key):
                show_decision_details(r,display_mode)
        with cols[6]:
            if r["signal"] in ("BUY","SELL") and st.button("Verify",key="integrated_verify_"+key):
                verify_dialog(r,connection_factory,project_dir)
    selected=[r["decision_key"] for r in active if st.session_state.get("phase6_decision_selected_"+r["decision_key"])]
    if st.button(f"Archive selected ({len(selected)})",disabled=not selected,key="integrated_archive"):
        confirm_decision_archive(tuple(selected),connection_factory)
    st.divider()
    with st.expander("Verification queue",expanded=False):
        queue=[r for r in trades if not state.get(r["decision_key"]) or state[r["decision_key"]]["status"]==REVIEW]
        st.caption(f"{len(queue)} unresolved BUY/SELL decisions")
        if st.button("Verify all unresolved from validated local 15m history",disabled=not queue):
            try:
                history=validated_history(project_dir)
                with connection_factory() as conn:
                    for r in queue:
                        verify_one(conn,r,history)
                st.rerun()
            except (OSError,ValueError,sqlite3.Error) as exc:
                st.error(str(exc))
        for r in queue[:50]:
            c1,c2=st.columns([4,1])
            c1.write(f'{r["decision_time_utc"][:16]} · {r["signal"]} · {r["strategy"]}')
            if c2.button("Review",key="queue_"+r["decision_key"]):
                verify_dialog(r,connection_factory,project_dir)
    with st.expander("Archived historical decisions",expanded=False):
        with connection_factory() as conn:
            archived=read_decisions(conn,archived=True)
        st.caption(f"{len(archived)} retained archived decisions")
        for r in archived[:100]:
            c1,c2,c3=st.columns([4,1,1])
            c1.write(f'{r["decision_time_utc"][:16]} · {r["signal"]} · {r["strategy"] or "—"}')
            if c2.button("View",key="arch_view_"+r["decision_key"]):
                show_decision_details(r,display_mode)
            if c3.button("Restore",key="arch_restore_"+r["decision_key"]):
                with connection_factory() as conn:
                    restore_decision(conn,r["decision_key"])
                st.rerun()
    if st.checkbox("Show manual research trades (separate journal)",key="integrated_manual_toggle"):
        st.caption("Manual entries are not frozen-engine decisions and are excluded from strategy analytics.")
        if manual_renderer is not None:
            manual_renderer(display_mode)

def render_analytics(connection_factory):
    with connection_factory() as conn:
        schema(conn)
        decisions=read_decisions(conn)
        state=reviews(conn)
        update_closed_reports(conn)
        reports=report_history(conn)
    st.subheader("Journal Research Analytics")
    st.caption("Journal-based outcomes only. The Phase 5 fixed historical artifact is shown separately below.")
    trade=[r for r in decisions if r["signal"] in ("BUY","SELL")]
    resolved=[(r,state[r["decision_key"]]) for r in trade if r["decision_key"] in state and state[r["decision_key"]]["status"] in (AUTO,USER)]
    counts={o:sum(v["outcome"]==o for _,v in resolved) for o in OUTCOMES}
    cols=st.columns(4)
    for col,label,value in zip(cols,("Trade decisions","Auto modeled","User confirmed","Unresolved"),
        (len(trade),sum(v["status"]==AUTO for _,v in resolved),
         sum(v["status"]==USER for _,v in resolved),len(trade)-len(resolved))):
        col.metric(label,value)
    st.write("**Outcome distribution**")
    st.bar_chart(pd.DataFrame([counts]).T.rename(columns={0:"Decisions"}))
    strategy_rows=[]
    for name in sorted(set(r["strategy"] or "UNKNOWN" for r in trade)):
        relevant=[v for r,v in resolved if (r["strategy"] or "UNKNOWN")==name]
        wins=sum(v["outcome"]=="TARGET" for v in relevant)
        losses=sum(v["outcome"]=="STOP" for v in relevant)
        strategy_rows.append({"Strategy":name,"TARGET":wins,"STOP":losses,
            "TIMEOUT":sum(v["outcome"]=="TIMEOUT" for v in relevant),
            "UNFILLED":sum(v["outcome"]=="UNFILLED" for v in relevant),
            "Win rate %":round(100*wins/(wins+losses),2) if wins+losses else None,
            "Known modeled R":round(sum(v["realized_r"] for v in relevant if v["realized_r"] is not None),2)})
    st.write("**Five-strategy comparison**")
    if strategy_rows: st.dataframe(pd.DataFrame(strategy_rows),hide_index=True,use_container_width=True)
    else: st.info("No confirmed outcomes yet.")
    st.write("**Direction comparison**")
    direction_rows=[]
    for direction in ("BUY","SELL"):
        relevant=[v for r,v in resolved if r["signal"]==direction]
        direction_rows.append({"Direction":direction,**{o:sum(v["outcome"]==o for v in relevant) for o in OUTCOMES}})
    st.dataframe(pd.DataFrame(direction_rows),hide_index=True,use_container_width=True)
    st.divider()
    st.subheader("Monthly / Yearly UTC Reports")
    st.caption("Only closed calendar periods are snapshotted. Late confirmations create new immutable revisions.")
    if reports:
        table=[]
        seen=set()
        for report in reports:
            ident=(report["period_type"],report["period_key"])
            if ident in seen: continue
            seen.add(ident)
            data=json.loads(report["report_json"])
            table.append({"Type":report["period_type"],"Period":report["period_key"],
                "Revision":report["revision"],"Decisions":data["decision_count"],
                "TARGET":data["target"],"STOP":data["stop"],"Unresolved":data["unresolved"],
                "Win rate %":data["win_rate_pct"]})
        st.dataframe(pd.DataFrame(table),hide_index=True,use_container_width=True)
        st.download_button("Export latest period summaries (CSV)",
            pd.DataFrame(table).to_csv(index=False).encode("utf-8"),
            "journal_period_summaries.csv","text/csv")
        with st.expander("Report history and revision details"):
            options=[f'{r["period_type"]} {r["period_key"]} · revision {r["revision"]}' for r in reports]
            chosen=st.selectbox("Saved report",range(len(reports)),format_func=lambda i:options[i])
            st.json(json.loads(reports[chosen]["report_json"]))
            st.download_button("Download selected report (JSON)",
                reports[chosen]["report_json"],"research_report.json","application/json")
    else:
        st.info("No closed UTC periods with saved decisions yet.")
    with st.expander("Outcome audit trail"):
        with connection_factory() as conn:
            audit=pd.read_sql_query("SELECT * FROM decision_outcome_review_audit ORDER BY id DESC",conn)
        if not audit.empty: st.dataframe(audit,hide_index=True,use_container_width=True)
        else: st.caption("No outcome reviews have been recorded.")
