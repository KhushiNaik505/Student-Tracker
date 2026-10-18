import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def pg(monkeypatch):
    """Clean TEST database with the real schema. Refuses to run unless TEST_DB_NAME contains 'test'
    (the fixture TRUNCATEs tables, so it must never point at your real data)."""
    name = os.environ.get("TEST_DB_NAME", "")
    if "test" not in name.lower():
        pytest.skip("TEST_DB_NAME not set to a database whose name contains 'test'")
    monkeypatch.setenv("DB_NAME", name)
    import db
    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL test database not reachable: {type(exc).__name__}")
    with conn, conn.cursor() as cur:
        cur.execute(open(os.path.join(ROOT, "schema.sql"), encoding="utf-8").read())
        cur.execute("TRUNCATE student, subject, sync_baseline, sync_log RESTART IDENTITY CASCADE")  # cascades to all dependants
    conn.close()
    return db


@pytest.fixture
def populated(pg):
    """Two students with semesters, marks, gap, seminars and activities."""
    d = pg
    ids = {}
    ids["a"] = d.insert("student", {"full_name": "Asha Verma", "email": "asha@example.com", "current_status": "Studying"})
    ids["b"] = d.insert("student", {"full_name": "Bharat Rao", "email": None, "current_status": "Studying"})
    ids["th"] = d.insert("subject", {"subject_code": "T1", "subject_name": "Theory Only", "subject_type": "Theory"})
    ids["both"] = d.insert("subject", {"subject_code": "B1", "subject_name": "Both Subject", "subject_type": "Both"})
    ids["lab"] = d.insert("subject", {"subject_code": "L1", "subject_name": "Lab Only", "subject_type": "Lab"})
    for no, ay, sg, cg in [(3, "2022-23", 8.0, 8.1), (1, "2020-21", 7.0, 7.0), (2, "2020-21", 7.5, 7.25)]:
        ids[f"s{no}"] = d.insert("semester", {"student_id": ids["a"], "semester_no": no, "academic_year": ay, "sgpa": sg, "cgpa": cg})
    d.insert("semester_marks", {"semester_id": ids["s1"], "subject_id": ids["th"], "theory_marks": 80, "lab_marks": None})
    d.insert("semester_marks", {"semester_id": ids["s1"], "subject_id": ids["both"], "theory_marks": 70, "lab_marks": 90})
    d.insert("semester_marks", {"semester_id": ids["s2"], "subject_id": ids["lab"], "theory_marks": None, "lab_marks": 88})
    ids["gap"] = d.insert("gap_year", {"student_id": ids["a"], "start_year": 2021, "end_year": 2021, "reason": "Health"})
    return d, ids
