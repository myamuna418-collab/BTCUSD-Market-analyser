"""BTCUSD Research — approved standalone Streamlit theme.
Import and call apply_theme() after st.set_page_config().
No backend, OCR, journal, or strategy logic is changed.
"""
import streamlit as st

COLORS = {
    "background": "#F5F7FA",
    "sidebar": "#EDF2F7",
    "card": "#FFFFFF",
    "text": "#17212F",
    "muted": "#475569",
    "primary": "#1D4ED8",
    "selected_bg": "#DBEAFE",
    "selected_text": "#1E40AF",
    "border": "#CBD5E1",
    "table_header": "#E2E8F0",
}

def apply_theme() -> None:
    """Apply the approved responsive light theme to the current Streamlit page."""
    st.markdown("""
<style>
:root {
  --btc-bg:#F5F7FA; --btc-sidebar:#EDF2F7; --btc-card:#FFFFFF;
  --btc-text:#17212F; --btc-muted:#475569; --btc-primary:#1D4ED8;
  --btc-selected:#DBEAFE; --btc-selected-text:#1E40AF;
  --btc-border:#CBD5E1; --btc-table:#E2E8F0;
}
html, body, [data-testid="stApp"], .stApp {
  background:var(--btc-bg); color:var(--btc-text);
}
[data-testid="stAppViewContainer"], [data-testid="stMain"] {
  background:var(--btc-bg);
}
[data-testid="stSidebar"], [data-testid="stSidebarContent"] {
  background:var(--btc-sidebar);
}
[data-testid="stSidebar"] * { color:var(--btc-text); }
h1,h2,h3,h4,h5,h6,p,label,span {
  overflow-wrap:break-word;
}
[data-testid="stMarkdownContainer"] { color:var(--btc-text); }
[data-testid="stCaptionContainer"] { color:var(--btc-muted); }
[data-testid="stVerticalBlockBorderWrapper"] > div[data-testid="stVerticalBlock"] {
  border-color:var(--btc-border);
}
div[data-testid="stMetric"] {
  background:var(--btc-card);
  border:1px solid var(--btc-border);
  border-radius:12px;
  padding:14px;
}
[data-testid="stMetricLabel"], [data-testid="stMetricValue"] {
  color:var(--btc-text);
}
.stButton > button[kind="primary"],
button[data-testid="stBaseButton-primary"] {
  background:var(--btc-primary);
  border:1px solid var(--btc-primary);
  color:white;
  border-radius:8px;
}
.stButton > button[kind="primary"]:hover,
button[data-testid="stBaseButton-primary"]:hover {
  background:#1E40AF; color:white;
}
.stButton > button[kind="secondary"],
button[data-testid="stBaseButton-secondary"] {
  background:var(--btc-card);
  color:var(--btc-text);
  border:1px solid var(--btc-border);
  border-radius:8px;
}
[data-testid="stExpander"] {
  background:var(--btc-card);
  border:1px solid var(--btc-border);
  border-radius:12px;
  overflow:hidden;
}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary * {
  color:var(--btc-text);
}
[data-testid="stFileUploader"] {
  background:var(--btc-card);
  border:1px solid var(--btc-border);
  border-radius:10px;
  padding:8px;
}
[data-testid="stDataFrame"], [data-testid="stTable"] {
  background:var(--btc-card);
  border:1px solid var(--btc-border);
  border-radius:10px;
}
table { border-collapse:collapse; width:100%; }
thead tr, th {
  background:var(--btc-table) !important;
  color:var(--btc-text) !important;
}
td { color:var(--btc-text); border-bottom:1px solid var(--btc-border); }
input, textarea, [data-baseweb="select"] {
  color:var(--btc-text);
}
.btc-card {
  background:var(--btc-card);
  border:1px solid var(--btc-border);
  border-radius:12px;
  padding:16px;
  margin-bottom:12px;
}
.btc-card-title { color:var(--btc-text); font-weight:700; margin-bottom:8px; }
.btc-muted { color:var(--btc-muted); }
.btc-selected {
  background:var(--btc-selected);
  color:var(--btc-selected-text);
  border:1px solid #93C5FD;
  border-radius:8px;
  padding:10px;
}
.btc-result-grid {
  display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px;
}
.btc-ocr-grid {
  display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px;
}
.btc-record-grid {
  display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px;
}
.btc-field {
  background:var(--btc-sidebar); padding:10px; border-radius:8px;
  min-width:0; overflow-wrap:anywhere;
}
.btc-field-label { font-size:.78rem; color:var(--btc-muted); }
.btc-field-value { color:var(--btc-text); font-weight:650; }
.btc-mobile-card { display:none; }
@media (max-width:768px) {
  .block-container { padding:1rem .75rem 2rem !important; }
  .btc-result-grid, .btc-ocr-grid { grid-template-columns:1fr; }
  .btc-record-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .btc-desktop-table { display:none !important; }
  .btc-mobile-card { display:block; }
  .btc-card { padding:12px; }
  h1 { font-size:1.6rem !important; }
  h2 { font-size:1.3rem !important; }
}

/* Approved light palette: global and nested Streamlit widgets. */
:root { color-scheme: light !important; }
html, body, .stApp, [data-testid="stAppViewContainer"],
[data-testid="stMain"], [data-testid="stMainBlockContainer"] {
  background-color: #F5F7FA !important;
  color: #17212F !important;
}
[data-testid="stSidebar"], [data-testid="stSidebarContent"] {
  background-color: #EDF2F7 !important;
  color: #17212F !important;
}
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
.stApp p, .stApp label, .stApp li,
[data-testid="stMarkdownContainer"], [data-testid="stMarkdownContainer"] *,
[data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] *,
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] *,
[data-testid="stMetricValue"], [data-testid="stMetricValue"] *,
[data-testid="stMetricDelta"], [data-testid="stMetricDelta"] * {
  color: #17212F !important;
  opacity: 1 !important;
}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] *,
.stApp small, .btc-muted, .btc-field-label {
  color: #475569 !important;
  opacity: 1 !important;
}
[data-testid="stMetric"], [data-testid="stExpander"],
[data-testid="stFileUploader"], [data-testid="stDataFrame"],
[data-testid="stTable"], .btc-card, .research-banner {
  background: #FFFFFF !important;
  border-color: #CBD5E1 !important;
  color: #17212F !important;
}
.research-banner h1, .research-banner h2,
.research-banner p, .research-banner .research-subtitle {
  color: #17212F !important;
}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary *,
[data-testid="stExpander"] details,
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] * {
  background-color: transparent;
  color: #17212F !important;
  opacity: 1 !important;
}
[data-testid="stExpander"] summary {
  background: #FFFFFF !important;
}
[data-testid="stAlert"] p, [data-testid="stAlert"] span {
  color: #17212F !important;
}
[data-testid="stFileUploaderDropzone"] {
  background: #F5F7FA !important;
  border-color: #CBD5E1 !important;
}
[data-testid="stFileUploaderDropzone"] *,
[data-testid="stFileUploaderDropzone"] small {
  color: #17212F !important;
}
[data-baseweb="input"], [data-baseweb="textarea"],
[data-baseweb="select"], [data-baseweb="base-input"],
[data-baseweb="popover"] {
  background: #FFFFFF !important;
  color: #17212F !important;
}
input, textarea, [role="combobox"] {
  color: #17212F !important;
  -webkit-text-fill-color: #17212F !important;
}
input::placeholder, textarea::placeholder {
  color: #475569 !important;
  opacity: 1 !important;
}
[data-baseweb="tab-list"] {background: #EDF2F7 !important;}
button[data-baseweb="tab"] {color: #475569 !important;}
button[data-baseweb="tab"][aria-selected="true"] {
  color: #1E40AF !important;
  border-bottom-color: #1D4ED8 !important;
}
.stButton button[kind="primary"], button[data-testid="stBaseButton-primary"],
[data-testid="stFormSubmitButton"] button[kind="primary"] {
  background: #1D4ED8 !important;
  border-color: #1D4ED8 !important;
  color: #FFFFFF !important;
}
.stButton button[kind="primary"] *, button[data-testid="stBaseButton-primary"] *,
[data-testid="stFormSubmitButton"] button[kind="primary"] * {
  color: #FFFFFF !important;
}
.stButton button[kind="secondary"], button[data-testid="stBaseButton-secondary"],
[data-testid="stFileUploaderDropzone"] button {
  background: #FFFFFF !important;
  border-color: #CBD5E1 !important;
  color: #17212F !important;
}
.stButton button[kind="secondary"] *, button[data-testid="stBaseButton-secondary"] * {
  color: #17212F !important;
}
.stButton button:disabled {opacity:.65 !important;}
[data-testid="stDataFrame"] [role="columnheader"],
[data-testid="stTable"] th, table th {
  background: #E2E8F0 !important;
  color: #17212F !important;
}
[data-testid="stTable"] td, table td {
  background: #FFFFFF !important;
  color: #17212F !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] *,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p {
  color: #17212F !important;
}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] * {
  color: #475569 !important;
}
.btc-card *, .btc-field *, .btc-mobile-card * {
  color: #17212F !important;
}
.btc-field-label, .btc-muted {color:#475569 !important;}
@media (max-width:768px) {
  .block-container {max-width:100% !important;}
  [data-testid="stHorizontalBlock"] {flex-wrap:wrap !important;}
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    min-width:min(100%,180px) !important;
    flex:1 1 180px !important;
  }
}


/* Mobile metric text truncation fix: preserve existing layout and logic. */
@media (max-width: 768px) {
  div[data-testid="stMetric"] {
    min-width: 0 !important;
  }
  div[data-testid="stMetric"] [data-testid="stMetricValue"],
  div[data-testid="stMetric"] [data-testid="stMetricLabel"] {
    min-width: 0 !important;
    max-width: 100% !important;
    overflow: visible !important;
    text-overflow: clip !important;
    white-space: normal !important;
    overflow-wrap: anywhere !important;
    word-break: normal !important;
  }
  div[data-testid="stMetric"] [data-testid="stMetricValue"] > div,
  div[data-testid="stMetric"] [data-testid="stMetricValue"] > div > div,
  div[data-testid="stMetric"] [data-testid="stMetricLabel"] > div,
  div[data-testid="stMetric"] [data-testid="stMetricLabel"] p {
    min-width: 0 !important;
    max-width: 100% !important;
    overflow: visible !important;
    text-overflow: clip !important;
    white-space: normal !important;
    overflow-wrap: anywhere !important;
  }
}
</style>
""", unsafe_allow_html=True)

def display_mode_selector(key: str = "btcusd_display_mode") -> str:
    """Manual Desktop/Mobile selector; defaults to Desktop; no Auto option."""
    return st.sidebar.radio("Display Mode", ("Desktop", "Mobile"), index=0, key=key)

def screenshot_uploader(key: str = "btcusd_screenshots"):
    """Single sidebar uploader for all four screenshot files."""
    return st.sidebar.file_uploader(
        "Upload Screenshots (15m, 1h, 3h, 4h)",
        type=("png", "jpg", "jpeg", "webp"),
        accept_multiple_files=True,
        key=key,
    )

def ocr_roll_down():
    """Closed-by-default container for existing OCR rendering logic."""
    return st.expander("OCR Extraction & Date Confirmation", expanded=False)

def theme_demo() -> None:
    """Optional standalone visual smoke test; uses no fabricated market data."""
    st.set_page_config(page_title="BTCUSD Theme Preview", layout="wide")
    apply_theme()
    mode = display_mode_selector()
    screenshots = screenshot_uploader()
    st.sidebar.caption("One uploader for all four timeframes.")
    st.title("BTCUSD Research")
    st.caption(f"Theme preview · {mode} selected · {len(screenshots or [])} file(s) selected")
    page = st.sidebar.radio("Navigation", ("Research Page", "Trade Journal", "Research Analytics"))
    if page == "Research Page":
        st.subheader("Main Research Result")
        st.info("Awaiting analysis — no strategy output available.")
        cols = st.columns(3) if mode == "Desktop" else [st.container()]
        for label in ("Entry", "Target", "Stop Loss"):
            with cols[("Entry", "Target", "Stop Loss").index(label)] if mode == "Desktop" else st.container():
                st.metric(label, "—")
        with ocr_roll_down():
            st.write("Shared Research Date & Time: Not detected")
            for tf in ("15m", "1h", "3h", "4h"):
                st.markdown(f"**{tf}**")
                st.code("OPEN=NOT VERIFIED  HIGH=NOT VERIFIED\nLOW=NOT VERIFIED   CLOSE=NOT VERIFIED\nBODY=NOT VERIFIED  RANGE=NOT VERIFIED\nBODY/RANGE=NOT VERIFIED\nDIRECTION=NOT VERIFIED")
            st.caption("Manual correction appears only when needed.")
        st.subheader("Five-Strategy Comparison")
        st.caption("Waiting for existing strategy engine.")
        st.subheader("Historical Verification")
        st.caption("Existing seven sections will be rendered during integration.")
        st.button("Save Selected Result to Trade Journal", type="primary", disabled=True)
    elif page == "Trade Journal":
        st.subheader("Trade Journal")
        choice = st.radio("Section", ("List of Trades", "Verification", "Archive"), horizontal=mode=="Desktop")
        st.info(f"{choice}: waiting for existing journal backend.")
    else:
        st.subheader("Research Analytics")
        choice = st.radio("Section", ("Previous Research", "Research of Previous Month"))
        st.info(f"{choice}: waiting for existing analytics backend.")

if __name__ == "__main__":
    theme_demo()
