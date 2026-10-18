"""Sync tests: real PostgreSQL + FakeSpreadsheet (our logic is real; only Google is simulated)."""
import threading
import time

import pytest

import sync
import sync_worker
from tests.fake_sheets import FakeApiError, FakeSpreadsheet

pytestmark = pytest.mark.live_pg


def run_sync(sheet):
    return sync.sync_all(lambda: sheet, sleep=lambda s: None)


def test_15_db_to_sheets_full_sync(populated):
    d, ids = populated
    sh = FakeSpreadsheet()
    r = run_sync(sh)
    assert r.ok and set(sh.sheets) == set(sync.MANAGED)
    assert sh.sheets["Students"].values[0] == ["student_id", "full_name", "email", "current_status"]
    names = {row[1] for row in sh.rows("Students")}
    assert names == {"Asha Verma", "Bharat Rao"}
    assert len(sh.rows("Semesters")) == 3 and len(sh.rows("Subject_Marks")) == 3 and len(sh.rows("Gap_Years")) == 1
    # relationships preserved through keys
    sem_student_ids = {row[1] for row in sh.rows("Semesters")}
    assert sem_student_ids == {ids["a"]}
    # NULL stays blank (not 0)
    th_row = [row for row in sh.rows("Subject_Marks") if row[2] == ids["th"]][0]
    assert th_row[4] == ""


def test_16_updates_and_deletes_reflected_without_duplicates(populated):
    d, ids = populated
    sh = FakeSpreadsheet()
    assert run_sync(sh).ok
    d.update("student", ids["b"], {"full_name": "Bharat R. Rao"})
    d.delete("semester", ids["s3"])
    d.insert("student", {"full_name": "Chitra", "email": None, "current_status": "Studying"})
    assert run_sync(sh).ok
    rows = sh.rows("Students")
    assert len(rows) == 3 and len({r[0] for r in rows}) == 3           # no duplicate IDs
    assert "Bharat R. Rao" in {r[1] for r in rows} and "Bharat Rao" not in {r[1] for r in rows}
    assert len(sh.rows("Semesters")) == 2
    d.delete("student", ids["a"])                                      # cascade delete -> children vanish from sheet too
    assert run_sync(sh).ok
    assert len(sh.rows("Students")) == 2 and sh.rows("Semesters") == [] and sh.rows("Gap_Years") == []


def test_16b_sync_is_idempotent_no_api_writes_when_unchanged(populated):
    sh = FakeSpreadsheet()
    first = run_sync(sh)
    assert first.api_writes == len(sync.MANAGED)
    writes_before = sh.writes
    again = run_sync(sh)
    assert again.ok and again.api_writes == 0 and sh.writes == writes_before
    assert set(again.tables.values()) == {"unchanged"}


def test_16c_unrelated_worksheet_is_not_touched(populated):
    sh = FakeSpreadsheet()
    mine = sh.add_worksheet("My Notes", 10, 5)
    mine.values = [["keep", "me"]]
    run_sync(sh)
    assert mine.values == [["keep", "me"]]


def test_18_api_failures_reported_not_hidden(populated):
    sh = FakeSpreadsheet(fail_with=FakeApiError(403))
    r = run_sync(sh)
    assert r.ok is False and "Share the spreadsheet" in r.error
    sh = FakeSpreadsheet(fail_with=FakeApiError(503))
    r = run_sync(sh)
    assert r.ok is False and "temporarily unreachable" in r.error   # retried, then reported


def test_overlapping_sync_is_refused(populated):
    d, _ = populated
    started, release = threading.Event(), threading.Event()

    class SlowSheet(FakeSpreadsheet):
        def worksheet(self, title):
            started.set(); release.wait(5)
            return super().worksheet(title)
    t = threading.Thread(target=lambda: run_sync(SlowSheet()))
    t.start(); assert started.wait(5)
    second = run_sync(FakeSpreadsheet())
    release.set(); t.join()
    assert second.ok is False and "already running" in second.error


def wait_for(cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def worker(populated):
    sh = FakeSpreadsheet()
    w = sync_worker.Worker(spreadsheet_provider=lambda: sh, debounce=0.5, heartbeat=1, poll_interval=0)
    stop = threading.Event()
    t = threading.Thread(target=w.run, args=(stop,), daemon=True)
    t.start()
    assert w.listening.wait(5) and wait_for(lambda: w.syncs >= 1)  # initial catch-up sync
    yield populated, sh, w
    stop.set(); t.join(5)


def test_17_direct_sql_update_detected_by_worker(worker):
    (d, ids), sh, w = worker
    assert "Asha Verma" in {r[1] for r in sh.rows("Students")}
    # direct database change, NOT through the app's functions: raw SQL on a separate connection
    conn = d.connect(); conn.autocommit = True
    conn.cursor().execute("UPDATE student SET full_name='Asha V. Direct' WHERE student_id=%s", (ids["a"],))
    assert wait_for(lambda: "Asha V. Direct" in {r[1] for r in sh.rows("Students")}), "worker did not sync the UPDATE"
    conn.cursor().execute("DELETE FROM gap_year")
    assert wait_for(lambda: sh.rows("Gap_Years") == []), "worker did not sync the DELETE"
    conn.cursor().execute("INSERT INTO subject (subject_code, subject_name, subject_type) VALUES ('NEW1','Brand new','Both')")
    assert wait_for(lambda: "NEW1" in {r[1] for r in sh.rows("Subjects")}), "worker did not sync the INSERT"
    conn.close()


def test_17b_rolled_back_change_is_never_synced(worker):
    (d, ids), sh, w = worker
    before = w.syncs
    conn = d.connect()  # autocommit off
    conn.cursor().execute("UPDATE student SET full_name='SHOULD NOT APPEAR' WHERE student_id=%s", (ids["a"],))
    time.sleep(1.5)
    assert w.syncs == before, "a notification was sent before COMMIT"
    conn.rollback(); conn.close()
    time.sleep(1.5)
    assert w.syncs == before and "SHOULD NOT APPEAR" not in {r[1] for r in sh.rows("Students")}


def test_17c_rapid_changes_are_coalesced(worker):
    (d, ids), sh, w = worker
    before = w.syncs
    conn = d.connect(); conn.autocommit = True
    cur = conn.cursor()
    for i in range(15):
        cur.execute("INSERT INTO student (full_name) VALUES (%s)", (f"Burst {i}",))
    assert wait_for(lambda: len([r for r in sh.rows("Students") if r[1].startswith("Burst")]) == 15)
    time.sleep(1.2)
    assert w.syncs - before <= 3, f"15 rapid commits caused {w.syncs - before} syncs"
    conn.close()


def test_17d_worker_heartbeat_visible_to_gui(worker):
    (d, ids), sh, w = worker
    assert wait_for(lambda: (d.worker_status() or {}).get("alive") is True)
    assert d.worker_status()["last_result"] == "ok"


def test_17e_worker_survives_connection_loss(worker):
    (d, ids), sh, w = worker
    killer = d.connect(); killer.autocommit = True
    killer.cursor().execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query LIKE 'LISTEN%%' AND pid <> pg_backend_pid()")
    killer.cursor().execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state='idle'")
    time.sleep(2.5)
    conn = d.connect(); conn.autocommit = True
    conn.cursor().execute("UPDATE student SET full_name='After Reconnect' WHERE student_id=%s", (ids["a"],))
    assert wait_for(lambda: "After Reconnect" in {r[1] for r in sh.rows("Students")}, 20), "worker did not reconnect"
    assert w.reconnects >= 1, "the connection was never actually lost - test proves nothing"
    conn.close(); killer.close()


def test_17f_cascade_delete_notifies_child_tables(worker):
    (d, ids), sh, w = worker
    assert len(sh.rows("Semesters")) == 3
    conn = d.connect(); conn.autocommit = True
    conn.cursor().execute("DELETE FROM student WHERE student_id=%s", (ids["a"],))  # only the parent is deleted explicitly
    assert wait_for(lambda: sh.rows("Semesters") == [] and sh.rows("Subject_Marks") == [] and sh.rows("Gap_Years") == [])
    conn.close()
