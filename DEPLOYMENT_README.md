# BTCUSD Research App - Streamlit Community Cloud

This package preserves the Python code and approved UI from BTCUSD_Research_Metric_Dots_Fixed(1).zip without modification.

## Publish
1. Create a new GitHub repository (private is recommended for source code, though the Streamlit app itself will be public).
2. Upload the *contents* of this ZIP to the repository root. Do not upload the ZIP itself.
3. Visit https://share.streamlit.io/ and choose Create app.
4. Select your repository, branch, and entrypoint `btcusd_research_app.py`.
5. Choose an available app URL and deploy.
6. Test desktop/mobile, screenshots (OCR), strategy results, and analytics.

## Critical limitations
- The existing `btcusd_research_journal.sqlite3` was deliberately excluded to avoid publishing private trade records.
- The app currently creates/uses a local SQLite database. On Streamlit Community Cloud, this is ephemeral and shared by visitors. DO NOT use the public app to store confidential or persistent journal records.
- For durable multi-user journaling, implement authentication and a managed database in a separate, reviewed change. This package does not change journal logic.
- Tesseract OCR is installed via packages.txt; Python dependencies via requirements.txt.
- The original trading logic and visual design were not edited.
- Public apps should be tested before sharing with others; deployment is not guaranteed until cloud build succeeds.
