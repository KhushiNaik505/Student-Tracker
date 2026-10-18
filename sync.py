"""PostgreSQL <-> Google Sheets synchronisation. PostgreSQL is ALWAYS the source of truth.

ONE-WAY (database -> sheet), always on
  * Managed worksheets are FULLY REFRESHED from the database (never appended => no duplicates; DB deletes propagate).
  * Only managed worksheets are touched. A worksheet is rewritten only if it differs from the database.
  * All tables are read in ONE read-only REPEATABLE READ transaction (consistent committed snapshot).
  * Overlapping syncs are prevented (threading.Lock + Postgres advisory lock).
  * Transient Google errors (429/5xx/network) are retried with back-off; others fail at once.

TWO-WAY (sheet -> database), on by default (TWO_WAY_SYNC=0 disables)
  Every sync run does PULL first, then PUSH, so a user's sheet edit is never overwritten before it is examined.
  * An edit is detected by comparing the live sheet with the BASELINE (what we wrote last time), stored in sync_baseline.
  * A changed row is applied only if (a) it passes the SAME validation as the GUI (validation.py) and the database
    constraints, and (b) the database row has not changed since the baseline (otherwise: conflict, database wins).
  * A row with an empty ID cell is a NEW record: validated, inserted, and the database assigns the ID.
  * Invalid edits are rejected; the next push restores the database value, and the reason is written to sync_log
    (shown in the 'Sync_Log' worksheet and the GUI).
  * Row deletions in the sheet are NOT applied (they are restored): a stray keypress must not cascade-delete a student.
    Delete records in the GUI or the database.
  * ID columns are never editable; Activity_Categories and Sync_Log are read-only.
"""
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import psycopg2
import psycopg2.extras

import config
import db
import validation as v

log = logging.getLogger("sync")

# worksheet title -> (SQL, headers)  -- ordered by primary key so output is deterministic
MANAGED = {
    "Students": ("SELECT student_id, full_name, email, current_status FROM student ORDER BY student_id",
                 ["student_id", "full_name", "email", "current_status"]),
    "Education": ("SELECT education_id, student_id, level, board, completion_year, percentage FROM education_record ORDER BY education_id",
                  ["education_id", "student_id", "level", "board", "completion_year", "percentage"]),
    "Semesters": ("SELECT semester_id, student_id, semester_no, academic_year, sgpa, cgpa FROM semester ORDER BY semester_id",
                  ["semester_id", "student_id", "semester_no", "academic_year", "sgpa", "cgpa"]),
    "Subjects": ("SELECT subject_id, subject_code, subject_name, subject_type FROM subject ORDER BY subject_id",
                 ["subject_id", "subject_code", "subject_name", "subject_type"]),
    "Subject_Marks": ("SELECT mark_id, semester_id, subject_id, theory_marks, lab_marks FROM semester_marks ORDER BY mark_id",
                      ["mark_id", "semester_id", "subject_id", "theory_marks", "lab_marks"]),
    "Gap_Years": ("SELECT gap_id, student_id, start_year, end_year, reason FROM gap_year ORDER BY gap_id",
                  ["gap_id", "student_id", "start_year", "end_year", "reason"]),
    "Seminars": ("SELECT seminar_id, student_id, title, seminar_type, event_date FROM seminar ORDER BY seminar_id",
                 ["seminar_id", "student_id", "title", "seminar_type", "event_date"]),
    "Activity_Categories": ("SELECT category_id, category_name FROM activity_category ORDER BY category_id",
                            ["category_id", "category_name"]),
    "Activities": ("SELECT activity_id, student_id, category_id, title, activity_date FROM activity ORDER BY activity_id",
                   ["activity_id", "student_id", "category_id", "title", "activity_date"]),
    # read-only activity log of two-way sync (latest 50 entries, newest first)
    "Sync_Log": ("""SELECT log_id, to_char(logged_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS logged_at_utc,
                           worksheet, sheet_row, record_id, outcome, message
                    FROM (SELECT * FROM sync_log ORDER BY log_id DESC LIMIT 50) t ORDER BY log_id DESC""",
                 ["log_id", "logged_at_utc", "worksheet", "sheet_row", "record_id", "outcome", "message"]),
}
TABLE_OF = {"Students": "student", "Education": "education_record", "Semesters": "semester", "Subjects": "subject",
            "Subject_Marks": "semester_marks", "Gap_Years": "gap_year", "Seminars": "seminar", "Activities": "activity"}
PULL_ORDER = ["Students", "Subjects", "Education", "Semesters", "Subject_Marks", "Gap_Years", "Seminars", "Activities"]  # parents first
READ_ONLY = ["Activity_Categories"]
# columns that must stay TEXT in Sheets so that typing 2023-24 or 2023-02-14 is not auto-converted to a date
TEXT_COLUMNS = {"Semesters": [3], "Seminars": [4], "Activities": [4]}
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
HEADER_KEY = "__header__"
_LOCAL_LOCK = threading.Lock()
_FORMATTED = set()
ADVISORY_KEY = 727401  # arbitrary constant identifying "StudentTrack sync"

DDL = """
CREATE TABLE IF NOT EXISTS sync_baseline (worksheet VARCHAR(30) NOT NULL, row_key VARCHAR(40) NOT NULL,
    cells JSONB NOT NULL, PRIMARY KEY (worksheet, row_key));
CREATE TABLE IF NOT EXISTS sync_log (log_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT now(), worksheet VARCHAR(30) NOT NULL, sheet_row INTEGER, record_id TEXT,
    outcome VARCHAR(10) NOT NULL CHECK (outcome IN ('applied','rejected')), message TEXT NOT NULL);
"""  # same as schema.sql; repeated so an older database upgrades itself automatically


class SyncError(Exception):
    pass


class SyncNotConfigured(SyncError):
    pass


class SyncBusy(SyncError):
    pass


class _Reject(Exception):
    pass


@dataclass
class SyncResult:
    ok: bool = False
    started: datetime = field(default_factory=datetime.now)
    finished: datetime = None
    tables: dict = field(default_factory=dict)  # title -> 'updated' | 'unchanged' | 'error'
    error: str = None
    api_writes: int = 0
    pulled_applied: int = 0                      # sheet edits saved to the database
    pulled_rejected: list = field(default_factory=list)  # sheet edits refused (dicts with worksheet,row,key,message)


# ---------- cell conversion ----------
def to_cell(v_):
    """DB value -> value sent to Sheets (None -> empty string; dates -> ISO text)."""
    if v_ is None:
        return ""
    if isinstance(v_, bool):
        return str(v_)
    if isinstance(v_, Decimal):
        return float(v_)
    if isinstance(v_, (date, datetime)):
        return v_.isoformat()
    return v_


def norm(x):
    """Normalise a cell so that 8.50 / 8.5 / '8.5' compare equal (Sheets returns text)."""
    s = "" if x is None else str(x).strip()
    try:
        return format(Decimal(s).normalize(), "f") if s else s
    except InvalidOperation:
        return s


def ensure_tables():
    with db.transaction() as cur:
        cur.execute(DDL)


def snapshot():
    """Read every managed table from one consistent committed snapshot -> {title: [[header], rows...]}."""
    ensure_tables()
    conn = db.connect()
    try:
        conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
        out = {}
        with conn.cursor() as cur:
            for title, (q, headers) in MANAGED.items():
                cur.execute(q)
                out[title] = [list(headers)] + [[to_cell(c) for c in row] for row in cur.fetchall()]
        return out
    finally:
        conn.close()


# ---------- baseline + log (database side) ----------
def load_baseline():
    out = {}
    for r in db.query("SELECT worksheet, row_key, cells FROM sync_baseline"):
        out.setdefault(r["worksheet"], {})[r["row_key"]] = r["cells"]
    return out


def save_baseline(title, values, existing):
    new = {HEADER_KEY: [norm(c) for c in values[0]]}
    for row in values[1:]:
        new[norm(row[0])] = [norm(c) for c in row]
    if existing.get(title) == new:
        return
    with db.transaction() as cur:
        cur.execute("DELETE FROM sync_baseline WHERE worksheet = %s", (title,))
        psycopg2.extras.execute_values(cur, "INSERT INTO sync_baseline (worksheet, row_key, cells) VALUES %s",
                                       [(title, k, json.dumps(c)) for k, c in new.items()])
    existing[title] = new


def write_log(entries):
    if not entries:
        return
    with db.transaction() as cur:
        cur.executemany("INSERT INTO sync_log (worksheet, sheet_row, record_id, outcome, message) VALUES (%s,%s,%s,%s,%s)",
                        [(e["worksheet"], e["row"], e["key"] or None, e["outcome"], e["message"]) for e in entries])


def recent_log(limit=20):
    ensure_tables()
    return db.query("""SELECT logged_at, worksheet, sheet_row, record_id, outcome, message
                       FROM sync_log ORDER BY log_id DESC LIMIT %s""", (limit,))


# ---------- validation of sheet edits (re-uses the GUI's validators) ----------
def _int(name, txt):
    if txt in (None, ""):
        raise v.ValidationError(f"{name} is required.")
    try:
        f = float(txt)
        if not math.isfinite(f) or f != int(f):
            raise ValueError
    except ValueError:
        raise v.ValidationError(f"{name} must be a whole number.")
    return int(f)


def _num(name, txt):
    if txt in (None, ""):
        return None  # blank = NULL, never 0
    try:
        f = float(txt)
        if not math.isfinite(f):
            raise ValueError
    except ValueError:
        raise v.ValidationError(f"{name} must be a number.")
    return f


def _date(name, txt):
    if txt in (None, ""):
        return None
    try:
        return date.fromisoformat(txt)
    except ValueError:
        raise v.ValidationError(f"{name} must be a date written as YYYY-MM-DD.")


def _marks(r):
    sub = _int("subject_id", r["subject_id"])
    found = db.query("SELECT subject_type FROM subject WHERE subject_id = %s", (sub,))
    return v.marks(_int("semester_id", r["semester_id"]), sub, _num("theory_marks", r["theory_marks"]),
                   _num("lab_marks", r["lab_marks"]), found[0]["subject_type"] if found else None)


BUILD = {
    "Students": lambda r: v.student(r["full_name"], r["email"], r["current_status"]),
    "Subjects": lambda r: v.subject(r["subject_code"], r["subject_name"], r["subject_type"]),
    "Education": lambda r: v.education(_int("student_id", r["student_id"]), r["level"], r["board"],
                                       _int("completion_year", r["completion_year"]), _num("percentage", r["percentage"])),
    "Semesters": lambda r: v.semester(_int("student_id", r["student_id"]), _int("semester_no", r["semester_no"]),
                                      r["academic_year"], _num("sgpa", r["sgpa"]), _num("cgpa", r["cgpa"])),
    "Subject_Marks": _marks,
    "Gap_Years": lambda r: v.gap(_int("student_id", r["student_id"]), _int("start_year", r["start_year"]),
                                 _int("end_year", r["end_year"]), r["reason"]),
    "Seminars": lambda r: v.seminar(_int("student_id", r["student_id"]), r["title"], r["seminar_type"], _date("event_date", r["event_date"])),
    "Activities": lambda r: v.activity(_int("student_id", r["student_id"]), _int("category_id", r["category_id"]),
                                       r["title"], _date("activity_date", r["activity_date"])),
}


def _pad(row, n):
    return (list(row) + [""] * n)[:n]


def pull_from_sheet(current, snap, baseline):
    """Examine the sheets (already read into `current`) and apply valid edits to the database.
    Returns (applied_entries, rejected_entries); both are also written to sync_log."""
    entries = []

    def note(title, row, key, outcome, msg):
        entries.append(dict(worksheet=title, row=row, key=key, outcome=outcome, message=msg))

    for title in PULL_ORDER:
        rows, base = current.get(title) or [], baseline.get(title)
        headers = MANAGED[title][1]
        if not rows or base is None:   # empty/new worksheet, or no baseline yet => nothing to compare against
            continue
        if [str(c).strip() for c in _pad(rows[0], len(headers))] != headers:
            note(title, 1, "", "rejected", "Header row was changed; edits in this worksheet were ignored.")
            continue
        base_rows = {k: c for k, c in base.items() if k != HEADER_KEY}
        db_rows = {norm(r[0]): [norm(c) for c in r] for r in snap[title][1:]}
        seen = set()
        for i, raw in enumerate(rows[1:], start=2):
            cells = [str(c).strip() for c in _pad(raw, len(headers))]
            if not any(cells):
                continue
            normed = [norm(c) for c in cells]
            pk = normed[0]
            try:
                if pk == "":  # NEW record typed into the next empty row
                    new_id = db.insert(TABLE_OF[title], BUILD[title](dict(zip(headers, cells))))
                    note(title, i, str(new_id), "applied", f"Inserted a new record; the database assigned ID {new_id}.")
                    continue
                dup = pk in seen
                seen.add(pk)
                if dup:
                    raise _Reject("Duplicate ID in the sheet; only the first row with this ID is used.")
                if pk not in base_rows:
                    raise _Reject("Unknown ID. IDs are assigned by the database: leave the ID cell empty for a new row.")
                if normed == base_rows[pk]:
                    continue  # untouched
                if pk not in db_rows:
                    raise _Reject("This record no longer exists in the database.")
                if db_rows[pk] != base_rows[pk]:
                    raise _Reject("The record was changed in the database at the same time; the database value was kept.")
                db.update(TABLE_OF[title], int(pk), BUILD[title](dict(zip(headers, cells))))
                note(title, i, pk, "applied", f"Updated record {pk} from the sheet.")
            except _Reject as e:
                note(title, i, pk, "rejected", str(e))
            except v.ValidationError as e:
                note(title, i, pk, "rejected", "Rejected: " + " ".join(e.errors))
            except psycopg2.Error as e:
                note(title, i, pk, "rejected", "Rejected by the database: " + db.friendly_error(e))
            except Exception:  # noqa: BLE001
                note(title, i, pk, "rejected", "Rejected: the row contains an invalid value.")
        gone = [k for k in base_rows if k not in seen]
        for k in gone[:10]:
            note(title, None, k, "rejected", "Row deleted in the sheet was restored. Delete records in the app or database, not in the sheet.")
        if len(gone) > 10:
            note(title, None, "", "rejected", f"...and {len(gone) - 10} more deleted rows were restored.")
    for title in READ_ONLY:
        rows, base = current.get(title) or [], baseline.get(title)
        if not rows or base is None:
            continue
        n = len(MANAGED[title][1])
        now = {norm(r[0]): [norm(c) for c in _pad(r, n)] for r in rows[1:] if any(str(c).strip() for c in r)}
        if now != {k: c for k, c in base.items() if k != HEADER_KEY}:
            note(title, None, "", "rejected", f"{title} is read-only (a fixed list); your changes were discarded.")
    write_log(entries)
    return [e for e in entries if e["outcome"] == "applied"], [e for e in entries if e["outcome"] == "rejected"]


# ---------- Google access ----------
def get_spreadsheet():
    """Open the target spreadsheet with service-account credentials from configuration."""
    cred = config.sheets_credentials_file()
    info = config.sheets_credentials_info()
    sid, name = config.sheets_target()
    if not (cred or info) or not (sid or name):
        raise SyncNotConfigured("Google Sheets is not configured (set GOOGLE_SHEETS_CREDENTIALS_FILE or GOOGLE_SERVICE_ACCOUNT_JSON, and GOOGLE_SHEET_ID).")
    import os
    import gspread
    from google.oauth2.service_account import Credentials
    if info:
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        if not os.path.isfile(cred):
            raise SyncNotConfigured("Service-account JSON file not found at the configured path.")
        creds = Credentials.from_service_account_file(cred, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sid) if sid else client.open(name)


def _is_transient(exc):
    try:
        import requests
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True
    except ImportError:  # pragma: no cover
        pass
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    return status in (408, 429, 500, 502, 503, 504)


def with_retry(fn, attempts=4, base_delay=1.5, sleep=time.sleep):
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if i == attempts - 1 or not _is_transient(exc):
                raise
            sleep(base_delay * (2 ** i))


def _get_or_create(sh, title, rows, cols):
    import gspread
    try:
        return sh.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=max(rows, 2), cols=max(cols, 2))


def _trim(m):
    m = [list(r) for r in m]
    for r in m:
        while r and r[-1] == "":
            r.pop()
    while m and not m[-1]:
        m.pop()
    return m


def _push_worksheet(ws, current, values, sleep):
    """Return 'updated' or 'unchanged'. `current` = what the sheet held when this sync started."""
    want = [[norm(c) for c in r] for r in values]
    have = [[norm(c) for c in r] for r in current]
    if _trim(want) == _trim(have):
        return "unchanged"
    with_retry(ws.clear, sleep=sleep)
    with_retry(lambda: ws.update(values=values, range_name="A1", raw=True), sleep=sleep)
    return "updated"


def _ensure_text_format(sh, ws, title, sleep):
    """Keep date-like columns as TEXT once per process, so retyped dates/years are not auto-converted."""
    key = (getattr(sh, "id", id(sh)), title)
    if key in _FORMATTED or title not in TEXT_COLUMNS:
        return
    try:
        for idx in TEXT_COLUMNS[title]:
            col = chr(65 + idx)
            with_retry(lambda: ws.format(f"{col}2:{col}5000", {"numberFormat": {"type": "TEXT"}}), sleep=sleep)
        _FORMATTED.add(key)
    except Exception as exc:  # noqa: BLE001  (cosmetic/robustness step: never fail the sync because of it)
        log.warning("could not set TEXT format on %s: %s", title, type(exc).__name__)


def sync_all(spreadsheet_provider=None, sleep=time.sleep, pull=None):
    """One sync run: [PULL valid sheet edits ->] PUSH database to sheet.
    Never raises for sync problems: returns SyncResult(ok=False, error=...)."""
    pull = config.two_way_enabled() if pull is None else pull
    res = SyncResult()
    if not _LOCAL_LOCK.acquire(blocking=False):
        res.error = "Another sync is already running in this process."
        res.finished = datetime.now()
        return res
    lock_conn = None
    try:
        lock_conn = db.connect()
        lock_conn.autocommit = True
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_KEY,))
            if not cur.fetchone()[0]:
                res.error = "Another sync (e.g. the background worker) is already running."
                return res
        ensure_tables()
        sh = (spreadsheet_provider or get_spreadsheet)()
        sheets = {}  # title -> (worksheet, values as the sheet holds them right now)
        for title, (_, headers) in MANAGED.items():
            try:
                ws = with_retry(lambda: _get_or_create(sh, title, 50, len(headers)), sleep=sleep)
                sheets[title] = (ws, with_retry(ws.get_all_values, sleep=sleep))
            except Exception as exc:  # noqa: BLE001
                res.tables[title] = "error"
                res.error = f"{title}: {_describe(exc)}"
                return res
        baseline = load_baseline()
        if pull:  # 1) PULL: validate + apply the user's sheet edits BEFORE anything is overwritten
            applied, rejected = pull_from_sheet({t: s[1] for t, s in sheets.items()}, snapshot(), baseline)
            res.pulled_applied, res.pulled_rejected = len(applied), rejected
        data = snapshot()  # 2) PUSH: re-read the committed state (now including accepted edits)
        for title, values in data.items():
            ws, current = sheets[title]
            try:
                state = _push_worksheet(ws, current, values, sleep)
                _ensure_text_format(sh, ws, title, sleep)
                res.tables[title] = state
                res.api_writes += state == "updated"
            except Exception as exc:  # noqa: BLE001
                res.tables[title] = "error"
                res.error = f"{title}: {_describe(exc)}"
                return res
            if title in TABLE_OF or title in READ_ONLY:
                save_baseline(title, values, baseline)
        res.ok = True
    except SyncNotConfigured as exc:
        res.error = str(exc)
    except psycopg2.Error:
        res.error = "Could not read the database for synchronisation."
    except Exception as exc:  # noqa: BLE001
        res.error = _describe(exc)
    finally:
        if lock_conn is not None:
            lock_conn.close()  # releases the session-level advisory lock
        _LOCAL_LOCK.release()
        res.finished = datetime.now()
    return res


def _describe(exc):
    """Short, secret-free description of a Google/API failure."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    name = type(exc).__name__
    if status in (401, 403):
        return f"Google refused access ({status}). Share the spreadsheet with the service-account e-mail as Editor and enable the Sheets + Drive APIs."
    if status == 404 or name == "SpreadsheetNotFound":
        return "Spreadsheet not found. Check GOOGLE_SHEET_ID and that it is shared with the service account."
    if status == 429:
        return "Google API quota exceeded (429). Try again in a minute."
    if _is_transient(exc):
        return "Google Sheets is temporarily unreachable (network/server error). Try Sync Now again later."
    return f"Sync failed: {name}"


def record_worker_state(heartbeat=True, result=None):
    """Worker bookkeeping (best effort; table is not exported or triggered)."""
    try:
        with db.transaction() as cur:
            cur.execute("""INSERT INTO sync_status (component, last_heartbeat) VALUES ('worker', now())
                           ON CONFLICT (component) DO UPDATE SET last_heartbeat = now()""")
            if result is not None:
                cur.execute("""UPDATE sync_status SET last_sync_at = now(), last_result = %s, last_error = %s
                               WHERE component='worker'""", ("ok" if result.ok else "error", result.error))
    except psycopg2.Error:
        pass
