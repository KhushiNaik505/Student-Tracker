# Deploying StudentTrack to a public URL (Neon + Streamlit Community Cloud)

1. **Hosted PostgreSQL (Neon, free).** Create a project at neon.tech. Copy the **direct** connection details
   (turn OFF "Connection pooling" in the dialog: LISTEN/NOTIFY and advisory locks do not work through the pooler).
   You need host, database, user, password.
2. **Load the schema** from your PC: put those values in `.env`
   (`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SSLMODE=require`, `DB_SKIP_CREATE=1`), then run `python scripts/init_db.py`.
3. **Push to GitHub** (never commit `.env`, `secrets.toml` or the service-account JSON; `.gitignore` already blocks them).
4. **Streamlit Community Cloud** (share.streamlit.io): New app -> pick the repo, branch `main`, main file `app.py`.
   Open **Advanced settings -> Secrets** and paste the contents of `.streamlit/secrets.toml.example` with your real values
   (`GOOGLE_SERVICE_ACCOUNT_JSON` = whole key JSON inside `'''...'''`, `GOOGLE_SHEET_ID`, and the DB values).
5. Deploy. The public URL (https://<name>.streamlit.app) is what you submit.
6. Note: the background worker (`sync_worker.py`) cannot run on Streamlit Cloud. Saves made in the hosted GUI sync to the sheet;
   changes made directly in the database and sheet-to-database edits are processed when the worker runs on your PC
   (point its `.env` at the same Neon database) or when someone clicks **Sync Now**.
