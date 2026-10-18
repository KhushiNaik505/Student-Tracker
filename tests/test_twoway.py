"""Two-way sync (Google Sheet -> database). Real PostgreSQL + FakeSpreadsheet standing in for Google.
A 'user edit' is simulated by changing the cells of the fake worksheet exactly as a person typing in Sheets would."""
import threading
import time
from datetime import date

import pytest

import sync
import sync_worker
from tests.fake_sheets import FakeSpreadsheet

pytestmark = pytest.mark.live_pg
DATA = [t for t in sync.MANAGED if t != "Sync_Log"]


def run(sh, **kw):
    return sync.sync_all(lambda: sh, sleep=lambda s: None, **kw)


def row_of(sh, title, pk):
    for r in sh.sheets[title].values[1:]:
        if str(r[0]) == str(pk):
            return r
    raise KeyError((title, pk))


def edit(sh, title, pk, col, value):
    row_of(sh, title, pk)[sync.MANAGED[title][1].index(col)] = value


def cell(sh, title, pk, col):
    return str(row_of(sh, title, pk)[sync.MANAGED[title][1].index(col)])


def add_row(sh, title, **cells):
    headers = sync.MANAGED[title][1]
    sh.sheets[title].values.append([cells.get(h, "") for h in headers])


def data_snapshot():
    snap = sync.snapshot()
    return {t: snap[t] for t in DATA}


def log_rows(d, outcome=None):
    rows = sync.recent_log(50)
    return [r for r in rows if outcome is None or r["outcome"] == outcome]


@pytest.fixture
def synced(populated):
    d, ids = populated
    ids["edu"] = d.insert("education_record", {"student_id": ids["a"], "level": "Secondary", "board": "CBSE", "completion_year": 2018, "percentage": 90})
    ids["sem_inar"] = d.insert("seminar", {"student_id": ids["a"], "title": "AI", "seminar_type": "Internal", "event_date": date(2023, 2, 14)})
    cat = d.list_categories()[0]["category_id"]
    ids["act"] = d.insert("activity", {"student_id": ids["a"], "category_id": cat, "title": "Hack", "activity_date": date(2023, 3, 1)})
    sh = FakeSpreadsheet()
    assert run(sh).ok
    return d, ids, sh


# ------------------------------------------------------------------ valid edits
def test_valid_edit_is_saved_to_database(synced):
    d, ids, sh = synced
    edit(sh, "Students", ids["b"], "full_name", "Bharat K. Rao")
    r = run(sh)
    assert r.ok and r.pulled_applied == 1 and r.pulled_rejected == []
    assert d.get_student(ids["b"])["full_name"] == "Bharat K. Rao"
    assert cell(sh, "Students", ids["b"], "full_name") == "Bharat K. Rao"
    entry = log_rows(d, "applied")[0]
    assert entry["worksheet"] == "Students" and entry["record_id"] == str(ids["b"])


def test_several_valid_edits_across_worksheets(synced):
    d, ids, sh = synced
    edit(sh, "Semesters", ids["s1"], "sgpa", "7.5")
    edit(sh, "Education", ids["edu"], "percentage", "91.25")
    edit(sh, "Seminars", ids["sem_inar"], "seminar_type", "External")
    edit(sh, "Subject_Marks", 2, "theory_marks", "75")
    edit(sh, "Gap_Years", ids["gap"], "reason", "Family reasons")
    edit(sh, "Activities", ids["act"], "activity_date", "2023-03-02")
    r = run(sh)
    assert r.ok and r.pulled_applied == 6 and not r.pulled_rejected
    assert float(d.list_semesters(ids["a"])[0]["sgpa"]) == 7.5
    assert float(d.list_education(ids["a"])[0]["percentage"]) == 91.25
    assert d.list_seminars(ids["a"])[0]["seminar_type"] == "External"
    assert float([m for m in d.marks_report(ids["a"]) if m["mark_id"] == 2][0]["theory_marks"]) == 75
    assert d.list_gaps(ids["a"])[0]["reason"] == "Family reasons"
    assert d.list_activities(ids["a"])[0]["activity_date"] == date(2023, 3, 2)


def test_blank_numeric_cell_becomes_null_not_zero(synced):
    d, ids, sh = synced
    edit(sh, "Semesters", ids["s1"], "cgpa", "")
    assert run(sh).pulled_applied == 1
    assert d.list_semesters(ids["a"])[0]["cgpa"] is None


def test_applied_edit_is_stable_and_idempotent(synced):
    d, ids, sh = synced
    edit(sh, "Students", ids["b"], "full_name", "Changed Once")
    run(sh)
    before = sh.writes
    again = run(sh)
    assert again.ok and again.pulled_applied == 0 and not again.pulled_rejected and again.api_writes == 0 and sh.writes == before


def test_reordering_rows_in_sheet_is_not_an_edit(synced):
    d, ids, sh = synced
    sh.sheets["Students"].values[1:] = list(reversed(sh.sheets["Students"].values[1:]))
    r = run(sh)
    assert r.ok and r.pulled_applied == 0 and not r.pulled_rejected


# ------------------------------------------------------------------ invalid edits: rejected, reverted, explained
BAD = [
    ("Semesters", lambda i: i["s1"], "sgpa", "11", "between 0 and 10"),
    ("Semesters", lambda i: i["s1"], "sgpa", "abc", "must be a number"),
    ("Semesters", lambda i: i["s1"], "academic_year", "2023-25", "start year"),
    ("Semesters", lambda i: i["s1"], "student_id", "9999", "does not exist"),
    ("Semesters", lambda i: i["s1"], "semester_no", "2.5", "whole number"),
    ("Subject_Marks", lambda i: 2, "theory_marks", "101", "between 0 and 100"),
    ("Subject_Marks", lambda i: 1, "lab_marks", "50", "Theory-only"),
    ("Students", lambda i: i["b"], "current_status", "Retired", "Status must be"),
    ("Students", lambda i: i["b"], "email", "not-an-email", "Email format"),
    ("Students", lambda i: i["b"], "email", "ASHA@example.com", "Duplicate"),
    ("Students", lambda i: i["b"], "full_name", "", "required"),
    ("Seminars", lambda i: i["sem_inar"], "event_date", "15/02/2023", "YYYY-MM-DD"),
    ("Seminars", lambda i: i["sem_inar"], "seminar_type", "Sometimes", "Internal or External"),
    ("Gap_Years", lambda i: i["gap"], "end_year", "2000", "before start year"),
    ("Education", lambda i: i["edu"], "level", "PhD", "Level must be"),
    ("Education", lambda i: i["edu"], "percentage", "120", "between 0 and 100"),
    ("Activities", lambda i: i["act"], "category_id", "777", "does not exist"),
    ("Subjects", lambda i: 1, "subject_type", "Practical", "Theory, Lab or Both"),
]


@pytest.mark.parametrize("title,pk,col,value,expect", BAD, ids=[f"{b[0]}.{b[2]}={b[3]}" for b in BAD])
def test_invalid_edit_rejected_reverted_and_logged(synced, title, pk, col, value, expect):
    d, ids, sh = synced
    pk = pk(ids)
    original, before = cell(sh, title, pk, col), data_snapshot()
    edit(sh, title, pk, col, value)
    r = run(sh)
    assert r.ok, r.error
    assert r.pulled_applied == 0 and len(r.pulled_rejected) == 1
    assert data_snapshot() == before, "database must not change"
    assert cell(sh, title, pk, col) == original, "sheet must be restored to the database value"
    msg = log_rows(d, "rejected")[0]["message"]
    assert expect.lower() in msg.lower(), msg
    assert sync.norm(sh.sheets["Sync_Log"].values[1][5]) == "rejected"          # visible inside the spreadsheet too


def test_invalid_edit_does_not_block_valid_edit_in_same_run(synced):
    d, ids, sh = synced
    edit(sh, "Semesters", ids["s1"], "sgpa", "11")
    edit(sh, "Students", ids["b"], "full_name", "Still Applied")
    r = run(sh)
    assert r.pulled_applied == 1 and len(r.pulled_rejected) == 1
    assert d.get_student(ids["b"])["full_name"] == "Still Applied"


# ------------------------------------------------------------------ new rows
def test_new_row_is_inserted_and_gets_database_id(synced):
    d, ids, sh = synced
    add_row(sh, "Students", full_name="Chitra Menon", email="chitra@example.com", current_status="Studying")
    r = run(sh)
    assert r.ok and r.pulled_applied == 1
    s = d.search_students("chitra")[0]
    assert s["email"] == "chitra@example.com"
    assert str(s["student_id"]) in {str(x[0]) for x in sh.sheets["Students"].values[1:]}   # ID filled in on the sheet
    assert len(sh.sheets["Students"].values) == 4                                           # header + 3 students, no duplicate


def test_new_row_invalid_is_rejected_and_removed_from_sheet(synced):
    d, ids, sh = synced
    add_row(sh, "Semesters", student_id=str(ids["b"]), semester_no="1", academic_year="2023-24", sgpa="12")
    r = run(sh)
    assert r.pulled_applied == 0 and len(r.pulled_rejected) == 1
    assert d.list_semesters(ids["b"]) == []
    assert len(sh.sheets["Semesters"].values) == 4                                          # header + 3 real semesters


def test_new_row_with_typed_id_is_rejected(synced):
    d, ids, sh = synced
    add_row(sh, "Students", student_id="500", full_name="Sneaky", current_status="Studying")
    r = run(sh)
    assert r.pulled_applied == 0 and "assigned by the database" in r.pulled_rejected[0]["message"]
    assert d.search_students("sneaky") == []


def test_new_marks_row_checks_subject_type(synced):
    d, ids, sh = synced
    add_row(sh, "Subject_Marks", semester_id=str(ids["s3"]), subject_id=str(ids["th"]), theory_marks="60", lab_marks="40")
    assert run(sh).pulled_applied == 0
    add_row(sh, "Subject_Marks", semester_id=str(ids["s3"]), subject_id=str(ids["th"]), theory_marks="60")
    assert run(sh).pulled_applied == 1


# ------------------------------------------------------------------ protections
def test_editing_an_id_cell_is_rejected(synced):
    d, ids, sh = synced
    before = data_snapshot()
    edit(sh, "Students", ids["b"], "student_id", "999")
    r = run(sh)
    assert r.pulled_applied == 0 and r.pulled_rejected
    assert data_snapshot() == before and str(row_of(sh, "Students", ids["b"])[0]) == str(ids["b"])


def test_deleting_a_sheet_row_does_not_delete_database_record(synced):
    d, ids, sh = synced
    sh.sheets["Students"].values = [r for r in sh.sheets["Students"].values if str(r[0]) != str(ids["a"])]
    r = run(sh)
    assert r.ok and r.pulled_applied == 0 and "restored" in r.pulled_rejected[0]["message"]
    assert d.get_student(ids["a"]) is not None and len(d.list_semesters(ids["a"])) == 3   # nothing cascaded
    assert str(ids["a"]) in {str(x[0]) for x in sh.sheets["Students"].values[1:]}          # row is back


def test_conflict_database_wins(synced):
    d, ids, sh = synced
    edit(sh, "Students", ids["b"], "full_name", "From Sheet")
    d.update("student", ids["b"], {"full_name": "From Database"})        # changed in the DB after the last sync
    r = run(sh)
    assert r.pulled_applied == 0 and "same time" in r.pulled_rejected[0]["message"]
    assert d.get_student(ids["b"])["full_name"] == "From Database"
    assert cell(sh, "Students", ids["b"], "full_name") == "From Database"


def test_database_change_is_not_mistaken_for_sheet_edit(synced):
    d, ids, sh = synced
    d.update("student", ids["b"], {"full_name": "DB Side Edit"})
    r = run(sh)
    assert r.ok and r.pulled_applied == 0 and not r.pulled_rejected
    assert cell(sh, "Students", ids["b"], "full_name") == "DB Side Edit"


def test_changed_header_row_ignores_that_worksheet(synced):
    d, ids, sh = synced
    sh.sheets["Students"].values[0][1] = "Name"
    edit(sh, "Students", ids["b"], "full_name", "Should Not Apply")
    r = run(sh)
    assert r.pulled_applied == 0 and "Header row" in r.pulled_rejected[0]["message"]
    assert d.get_student(ids["b"])["full_name"] == "Bharat Rao" and sh.sheets["Students"].values[0][1] == "full_name"


def test_read_only_worksheets_cannot_change_database(synced):
    d, ids, sh = synced
    sh.sheets["Activity_Categories"].values[1][1] = "Hacked"
    sh.sheets["Sync_Log"].values.append(["99", "x", "Students", "2", "1", "applied", "fake"])
    r = run(sh)
    assert r.ok and r.pulled_applied == 0
    assert [c["category_name"] for c in d.list_categories()][0] == "Technical"
    assert sh.sheets["Activity_Categories"].values[1][1] == "Technical"
    assert all(row[2] != "fake" and row[6] != "fake" for row in sh.sheets["Sync_Log"].values[1:])


def test_no_baseline_means_no_pull_and_no_crash(populated):
    d, ids = populated
    sh = FakeSpreadsheet()
    assert run(sh).ok
    with d.transaction() as cur:
        cur.execute("DELETE FROM sync_baseline")
    edit(sh, "Students", ids["b"], "full_name", "Unverifiable")
    r = run(sh)
    assert r.ok and r.pulled_applied == 0
    assert d.get_student(ids["b"])["full_name"] == "Bharat Rao"       # database stayed the source of truth


def test_two_way_can_be_switched_off(synced):
    d, ids, sh = synced
    edit(sh, "Students", ids["b"], "full_name", "Ignored")
    r = run(sh, pull=False)
    assert r.pulled_applied == 0 and d.get_student(ids["b"])["full_name"] == "Bharat Rao"
    assert cell(sh, "Students", ids["b"], "full_name") == "Bharat Rao"  # one-way behaviour: sheet overwritten


def test_date_and_year_columns_are_formatted_as_text_once(synced):
    d, ids, sh = synced
    for t in ("Semesters", "Seminars", "Activities"):
        assert sh.sheets[t].formats and sh.sheets[t].formats[0][1] == {"numberFormat": {"type": "TEXT"}}
    n = len(sh.sheets["Seminars"].formats)
    edit(sh, "Students", ids["b"], "full_name", "Trigger another sync")
    run(sh)
    assert len(sh.sheets["Seminars"].formats) == n


def test_google_failure_during_pull_leaves_database_untouched(populated):
    from tests.fake_sheets import FakeApiError
    d, ids = populated
    r = run(FakeSpreadsheet(fail_with=FakeApiError(403)))
    assert r.ok is False and r.pulled_applied == 0 and "Share the spreadsheet" in r.error


# ------------------------------------------------------------------ the background worker does the polling
def wait_for(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_worker_polls_sheet_and_applies_valid_edit_rejects_invalid(populated):
    d, ids = populated
    sh = FakeSpreadsheet()
    w = sync_worker.Worker(spreadsheet_provider=lambda: sh, debounce=0.3, heartbeat=1, poll_interval=1)
    stop = threading.Event()
    t = threading.Thread(target=w.run, args=(stop,), daemon=True)
    t.start()
    try:
        assert w.listening.wait(5) and wait_for(lambda: w.syncs >= 2)       # catch-up sync + at least one idle poll
        time.sleep(0.5)
        edit(sh, "Students", ids["b"], "full_name", "Edited In Sheet")       # a person types in Google Sheets
        assert wait_for(lambda: d.get_student(ids["b"])["full_name"] == "Edited In Sheet"), "worker never saw the sheet edit"
        edit(sh, "Semesters", ids["s1"], "sgpa", "11")                       # and makes a mistake
        assert wait_for(lambda: any("between 0 and 10" in r["message"] for r in sync.recent_log(20)))
        assert wait_for(lambda: cell(sh, "Semesters", ids["s1"], "sgpa") in ("7", "7.0"))
        assert float(d.list_semesters(ids["a"])[0]["sgpa"]) == 7.0
    finally:
        stop.set(); t.join(5)
