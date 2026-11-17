BTCUSD Research — Integrated Journal & Analytics
================================================

Launch from this folder (Windows):
    streamlit run btcusd_research_app.py

Install required packages in your existing Python environment:
    pip install streamlit pandas numpy pillow pytesseract

This bundle retains the original OCR, frozen engine, seven-section historical
verification, phase5 fixed artifact, and supplied SQLite database.

New module:
    research_journal_integrated.py

Features:
    - Compact searchable/paginated decision journal with View and Verify
    - Separate manual research trade journal
    - Evidence-based automatic modeled outcome verification
    - Needs Confirmation queue and audited manual outcome confirmation
    - Audited soft archive and restore
    - Journal analytics, five-strategy comparison, BUY/SELL comparisons
    - Immutable revised monthly/yearly UTC reports after periods close
    - Separate original Phase 5 artifact analytics

IMPORTANT DATA LIMITATION:
    The supplied BTCUSD_15m.csv ends at 2026-10-23 12:45 UTC.
    The nine supplied saved decisions are dated November 2026.
    Thus none of their November outcomes can be automatically verified
    using the supplied historical data. They remain NEEDS CONFIRMATION
    until later validated 15m history is supplied. No results are invented.

Evidence and safety:
    AUTO VERIFIED — MODELED does NOT mean exchange execution.
    Entry-candle exit touches and simultaneous target/stop touches require
    confirmation rather than invented intrabar sequencing.
    Original frozen-engine setup status and decision payloads are untouched.
    Manual confirmation needs notes; outcome changes are audited.

Back up your working directory/database before replacing local files.
Use the bundled SQLite only if you intend to work with the supplied snapshot.
For an existing installation, retain YOUR CURRENT btcusd_research_journal.sqlite3
rather than overwriting it with this snapshot. New tables are created additively
on startup and no existing records are deleted.

Known testing boundary:
    Source files compile, outcome scenarios and SQLite migrations were
    checked. Full Streamlit GUI testing was not possible in this environment
    because Streamlit is not installed here.
