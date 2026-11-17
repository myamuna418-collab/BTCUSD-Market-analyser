BTCUSD HYBRID OCR — INTEGRATION BUILD

Source: BTCUSD_Research_Integrated(1).zip
Changed: btcusd_research_app.py
Added: hybrid_ocr.py
All other original project files retained.

Run on Windows PowerShell (from this extracted folder):
  $env:Path = "C:\Program Files\Tesseract-OCR;" + $env:Path
  streamlit run btcusd_research_app.py

How it works:
- Independently extracts O/H/L/C from screenshot-only OCR passes.
- Keeps fields with agreement across at least two OCR readings.
- Shows uncertain fields in an expandable Streamlit review area, with screenshot.
- A manually entered price is applied only after the corresponding checkbox is checked.
- Does NOT claim candle timestamp/completion verification.
- Does NOT substitute historical CSV prices for screenshot OCR.

TESTS:
- Python syntax compiled: PASS (both modified files)
- Standalone extractor on four provided screenshots:
  15m: 4/4
  1h: 2/4 (Open and High require confirmation)
  3h: 3/4 (High requires confirmation)
  4h: 3/4 (Open requires confirmation)
- Full Windows Streamlit interaction: NOT TESTED here.
- Future screenshots: NOT GUARANTEED to be automatically readable; manual confirmation handles uncertainty.

IMPORTANT:
Do not replace your existing app folder in-place. Extract this ZIP into a new folder,
run it there, and keep your previous working project as backup.
