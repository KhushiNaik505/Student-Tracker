# StudentTrack – Student Academic Progression and Portfolio Management System

PostgreSQL + Python + Streamlit + Plotly, with **two-way sync** between PostgreSQL and Google Sheets (database → sheet always; validated sheet edits → database).

## What is in the folder
| File | Purpose |
|---|---|
| `schema.sql` | all 9 tables, constraints, indexes, triggers (safe to re-run) |
| `seed.sql` | fictional demo data: 6 students (safe to re-run, no duplicates) |
| `db.py`, `validation.py`, `progression.py`, `config.py` | database layer, input checks, chart logic, settings |
| `app.py` | the Streamlit GUI (10 pages) |
| `sync.py` | Google Sheets synchronisation |
| `sync_worker.py` | background listener for direct database changes |
| `scripts/init_db.py` | creates the database, applies schema + seed (no `psql` needed) |
| `tests/` | automated tests |
| `USER_TECHNICAL_REPORT.md` | user manual + technical report (ER diagram, normalisation, violation table, two-way sync) |
| `SQL_EXAMPLES.sql` | tested INSERT / UPDATE / DELETE tutorial for pgAdmin |
| `VIVA_GUIDE.md` | viva questions and answers |

## Setup on Windows (about 10 minutes, without Google)

1. **Install** Python 3.11+ (tick "Add to PATH") and PostgreSQL (remember the `postgres` password you choose). Make sure the PostgreSQL service is running.
2. **Open PowerShell in this folder** and create a virtual environment:
   ```powershell
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **Create your settings file** and put your PostgreSQL password in it:
   ```powershell
   copy .env.example .env
   notepad .env
   ```
4. **Create the database, tables and demo data** (this also creates the `studenttrack` database for you):
   ```powershell
   python scripts/init_db.py
   ```
   Wipe and reload the demo at any time with `python scripts/init_db.py --reset` (asks you to type YES).
5. **Start the app:**
   ```powershell
   streamlit run app.py
   ```
   It opens at http://localhost:8501. Everything except Google syncing already works at this point.

## Step 6 – Connect Google Sheets (needs YOUR Google account; I could not do this for you)

1. Go to <https://console.cloud.google.com/>, create a project (e.g. `studenttrack`).
2. **APIs & Services → Library**: enable **Google Sheets API** and **Google Drive API**.
3. **APIs & Services → Credentials → Create credentials → Service account**. Name it, finish.
4. Open the service account → **Keys → Add key → Create new key → JSON**. A `.json` file downloads. Move it somewhere **outside** this folder (e.g. `C:\secrets\`). Never share or commit it.
5. Open the JSON and copy its `client_email` value (looks like `name@project.iam.gserviceaccount.com`).
6. In Google Sheets create a **new blank spreadsheet** (use a dedicated one: the 9 managed worksheets get overwritten). Click **Share**, paste the `client_email`, give **Editor** access.
7. Copy the spreadsheet ID from its URL (`.../spreadsheets/d/<ID>/edit`).
8. Edit `.env`: set `GOOGLE_SHEETS_CREDENTIALS_FILE` (path to the JSON) and `GOOGLE_SHEET_ID`.
9. Restart Streamlit. Open **Google Sheets Synchronization → Sync Now**. The 9 worksheets appear.

## Start the background worker (needed ONLY for direct-database changes)
Open a **second** PowerShell window in this folder:
```powershell
venv\Scripts\activate
python sync_worker.py
```
Leave it running (Ctrl+C stops it). The GUI shows 🟢/🔴 for the worker on the sidebar and Sync page. Changes made *inside the GUI* sync without the worker; changes made with pgAdmin/psql need the worker.

## Upgrading an existing install (you already have it running)
1. Stop Streamlit and the worker (Ctrl+C). 2. Overwrite the project files with the new ones (keep your `.env`!). 3. Optional but tidy: `python scripts/init_db.py --no-seed` (adds the two helper tables; the sync also creates them by itself, and your data is untouched). 4. Start Streamlit and the worker again. 5. Click **Sync Now** once: a new **Sync_Log** tab appears and the baseline is stored. Two-way editing works from the next sync on.

## Two-way sync: how to use and demonstrate it
Keep `python sync_worker.py` running (it checks the sheet every 30 s; **Sync Now** and every GUI save check immediately).
| Try this in Google Sheets | Expected |
|---|---|
| Students tab: change a name, wait ≤ 30 s (or click Sync Now in the app) | Name changes in the app; Sync_Log says `applied`; worker prints "saved 1 edit(s)" |
| Semesters tab: set an `sgpa` to `11` | Reverted to the old value; Sync_Log says `rejected … SGPA must be between 0 and 10.` |
| Students tab: type a new row under the last one, **leave the ID empty** | Student appears in the app; ID filled in on the sheet |
| Type a new row with an ID, or change an ID | Rejected: "IDs are assigned by the database" |
| Delete a whole row | Row comes back; the student is NOT deleted (by design) |
| Change a date in Seminars to `15/02/2023` | Rejected: "must be a date written as YYYY-MM-DD" |
Set `TWO_WAY_SYNC=0` in `.env` for one-way behaviour only (sheet always overwritten).

## Run the tests
Offline tests (no database, no Google) work anywhere (15 tests):
```powershell
pytest tests/test_offline.py
```
Full suite needs a **separate scratch database** (the tests empty its tables):
```powershell
python -c "import psycopg2;c=psycopg2.connect(host='localhost',user='postgres',password=input('pw: '),dbname='postgres');c.autocommit=True;c.cursor().execute('CREATE DATABASE studenttrack_test')"
set TEST_DB_NAME=studenttrack_test
pytest
```
Real-Google test (`tests/test_google_live.py`) skips itself unless step 6 is configured. Expected result of the full suite: **112 passed, 1 skipped**.

## Demo script (5 minutes)
1. Dashboard → KPIs. 2. Student Management → search "sha" (case-insensitive) → add a student.
3. Academic Progression → choose *Priya Nair* (orange gap-year band) then *Rohan Patel* (grey "no record" line at semester 3).
4. Semester Records → try SGPA `11` → rejected. 5. Seminars/Activities tabs.
6. Sync page → Sync Now → show the Google Sheet. 7. Two-way: edit a cell in the sheet (see table above). 8. Start the worker, run in pgAdmin
   `UPDATE student SET full_name='Aarav S. Sharma' WHERE student_id=1;` → the sheet updates in ~3 s.
