"""Tests against a real PostgreSQL TEST database (marker: live_pg)."""
from datetime import date

import psycopg2
import pytest

import progression
import validation as v

pytestmark = pytest.mark.live_pg


def count(d, table):
    return d.query(f"SELECT count(*) AS n FROM {table}")[0]["n"]


def test_01_create_student(pg):
    sid = pg.insert("student", v.student("Test One", "t1@example.com", "Studying"))
    assert pg.get_student(sid)["full_name"] == "Test One"


def test_02_edit_student(pg):
    sid = pg.insert("student", v.student("Old Name", None, "Studying"))
    assert pg.update("student", sid, v.student("New Name", "n@example.com", "Graduated")) == 1
    s = pg.get_student(sid)
    assert (s["full_name"], s["current_status"], s["email"]) == ("New Name", "Graduated", "n@example.com")


def test_03_delete_student_cascades(populated):
    d, ids = populated
    d.insert("seminar", {"student_id": ids["a"], "title": "S", "seminar_type": "Internal", "event_date": date(2023, 1, 1)})
    assert count(d, "semester") == 3 and count(d, "semester_marks") == 3
    d.delete("student", ids["a"])
    for t in ["semester", "semester_marks", "gap_year", "seminar", "education_record", "activity"]:
        assert count(d, t) == 0, t
    assert count(d, "subject") == 3  # subjects are shared and must survive


def test_04_case_insensitive_partial_search(populated):
    d, _ = populated
    for term in ["asha", "ASHA", "sha ver", "VeRmA"]:
        assert [r["full_name"] for r in d.search_students(term)] == ["Asha Verma"], term
    assert d.search_students("zzz") == []
    assert len(d.search_students("")) == 2
    assert d.search_students("%") == []  # wildcard characters are matched literally


def test_05_valid_semester_and_marks(populated):
    d, ids = populated
    sem = d.insert("semester", {"student_id": ids["b"], "semester_no": 1, "academic_year": "2023-24", "sgpa": 9.99, "cgpa": 10})
    d.insert("semester_marks", {"semester_id": sem, "subject_id": ids["both"], "theory_marks": 0, "lab_marks": 100})
    r = d.marks_report(ids["b"])
    assert len(r) == 1 and float(r[0]["theory_marks"]) == 0 and float(r[0]["lab_marks"]) == 100


def test_06_sgpa_above_10_rejected(populated):
    d, ids = populated
    with pytest.raises(psycopg2.errors.CheckViolation):
        d.insert("semester", {"student_id": ids["b"], "semester_no": 1, "academic_year": "2023-24", "sgpa": 10.5, "cgpa": 8})
    assert "SGPA must be between 0 and 10" in d.friendly_error(_catch(lambda: d.insert("semester", {"student_id": ids["b"], "semester_no": 2, "academic_year": "2023-24", "sgpa": 11, "cgpa": 8})))


def _catch(fn):
    try:
        fn()
    except psycopg2.Error as e:
        return e
    raise AssertionError("expected a database error")


def test_07_marks_out_of_range_rejected(populated):
    d, ids = populated
    for bad in [dict(theory_marks=101), dict(lab_marks=-1)]:
        with pytest.raises(psycopg2.errors.CheckViolation):
            d.insert("semester_marks", {"semester_id": ids["s3"], "subject_id": ids["both"], **bad})


def test_07b_theory_only_subject_cannot_have_lab_marks(populated):
    d, ids = populated
    e = _catch(lambda: d.insert("semester_marks", {"semester_id": ids["s3"], "subject_id": ids["th"], "theory_marks": 50, "lab_marks": 50}))
    assert "Theory-only" in d.friendly_error(e)


def test_08_nonexistent_foreign_key_rejected(pg):
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        pg.insert("semester", {"student_id": 99999, "semester_no": 1, "academic_year": "2023-24"})


def test_09_duplicates_rejected(populated):
    d, ids = populated
    with pytest.raises(psycopg2.errors.UniqueViolation):
        d.insert("student", {"full_name": "Dup", "email": "ASHA@example.com", "current_status": "Studying"})  # case-insensitive
    with pytest.raises(psycopg2.errors.UniqueViolation):
        d.insert("semester", {"student_id": ids["a"], "semester_no": 1, "academic_year": "2020-21"})
    with pytest.raises(psycopg2.errors.UniqueViolation):
        d.insert("semester_marks", {"semester_id": ids["s1"], "subject_id": ids["th"], "theory_marks": 1})
    d.insert("student", {"full_name": "No Mail 1", "email": None, "current_status": "Studying"})  # many NULL e-mails allowed
    d.insert("student", {"full_name": "No Mail 2", "email": None, "current_status": "Studying"})
    d.insert("education_record", {"student_id": ids["a"], "level": "Secondary", "board": "X", "completion_year": 2018, "percentage": 80})
    with pytest.raises(psycopg2.errors.UniqueViolation):
        d.insert("education_record", {"student_id": ids["a"], "level": "Secondary", "board": "Y", "completion_year": 2019, "percentage": 70})


def test_09b_gap_year_end_before_start_rejected(populated):
    d, ids = populated
    with pytest.raises(psycopg2.errors.CheckViolation):
        d.insert("gap_year", {"student_id": ids["b"], "start_year": 2022, "end_year": 2021, "reason": "x"})


def test_09c_category_delete_restricted(populated):
    d, ids = populated
    cat = d.list_categories()[0]["category_id"]
    d.insert("activity", {"student_id": ids["a"], "category_id": cat, "title": "T", "activity_date": date(2023, 1, 1)})
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        with d.transaction() as cur:
            cur.execute("DELETE FROM activity_category WHERE category_id=%s", (cat,))


def test_10_all_semester_marks_retrieved(populated):
    d, ids = populated
    rep = d.marks_report(ids["a"])
    assert [(r["semester_no"], r["subject_code"]) for r in rep] == [(1, "B1"), (1, "T1"), (2, "L1")]
    t = {r["subject_code"]: r for r in rep}
    assert t["T1"]["lab_marks"] is None and t["L1"]["theory_marks"] is None  # NULL preserved, not 0
    assert len(d.marks_report(ids["a"], ids["s1"])) == 2


def test_11_seminar_filters(populated):
    d, ids = populated
    for t, ty, dt in [("I1", "Internal", date(2023, 1, 5)), ("E1", "External", date(2023, 2, 5)), ("I0", "Internal", date(2022, 1, 5))]:
        d.insert("seminar", {"student_id": ids["a"], "title": t, "seminar_type": ty, "event_date": dt})
    d.insert("seminar", {"student_id": ids["b"], "title": "Other", "seminar_type": "Internal", "event_date": date(2023, 1, 1)})
    assert [s["title"] for s in d.list_seminars(ids["a"], "Internal")] == ["I0", "I1"]
    assert [s["title"] for s in d.list_seminars(ids["a"], "External")] == ["E1"]
    assert len(d.list_seminars(ids["a"])) == 3


def test_12_activity_category_filters(populated):
    d, ids = populated
    cats = {c["category_name"]: c["category_id"] for c in d.list_categories()}
    assert set(cats) == {"Technical", "Co-curricular", "Extra-curricular"}
    for name, title in [("Technical", "T"), ("Co-curricular", "C"), ("Extra-curricular", "X"), ("Technical", "T2")]:
        d.insert("activity", {"student_id": ids["a"], "category_id": cats[name], "title": title, "activity_date": date(2023, 5, 1)})
    assert sorted(a["title"] for a in d.list_activities(ids["a"], "Technical")) == ["T", "T2"]
    assert [a["title"] for a in d.list_activities(ids["a"], "Extra-curricular")] == ["X"]
    assert len(d.list_activities(ids["a"])) == 4 and d.list_activities(ids["b"], "Technical") == []


def test_13_progression_data_ordered_from_db(populated):
    d, ids = populated  # semesters were inserted in the order 3,1,2
    rows = d.progression_rows(ids["a"])
    assert [r["semester_no"] for r in rows] == [1, 2, 3]
    assert [r["sgpa"] for r in rows] == [7.0, 7.5, 8.0] and rows[1]["cgpa"] == 7.25
    assert progression.build_progression(rows, [])["missing"] == []


def test_14_gap_year_indicator_from_db(populated):
    d, ids = populated
    d.update("semester", ids["s3"], {"academic_year": "2022-23"})
    p = progression.build_progression(d.progression_rows(ids["a"]), d.list_gaps(ids["a"]))
    assert len(p["gaps"]) == 1 and (p["gaps"][0]["x0"], p["gaps"][0]["x1"]) == (2, 3) and p["gaps"][0]["reason"] == "Health"
    assert d.progression_rows(ids["b"]) == []  # student without semesters -> empty, no invented data


def test_transaction_rollback_leaves_no_partial_data(pg):
    with pytest.raises(psycopg2.errors.UniqueViolation):
        with pg.transaction() as cur:
            cur.execute("INSERT INTO subject (subject_code, subject_name, subject_type) VALUES ('Z1','Z','Theory')")
            cur.execute("INSERT INTO subject (subject_code, subject_name, subject_type) VALUES ('Z1','Z again','Theory')")
    assert count(pg, "subject") == 0


def test_kpis_come_from_database(populated):
    d, ids = populated
    k = d.dashboard_kpis()
    assert k["students"] == 2 and k["semesters"] == 3 and k["gap_years"] == 1 and k["avg_latest_cgpa"] == 8.1


def test_sql_injection_text_is_just_data(pg):
    sid = pg.insert("student", v.student("Robert'); DROP TABLE student;--", None, "Studying"))
    assert pg.get_student(sid)["full_name"].startswith("Robert'")
    assert count(pg, "student") == 1


@pytest.mark.parametrize("sql_text,expected", [
    ("UPDATE student SET current_status='Retired'", "Status must be Studying, Graduated or Discontinued."),
    ("UPDATE student SET email='nope'", "Email format is invalid."),
    ("INSERT INTO student(full_name,email) VALUES ('Dup','ASHA@example.com')", "Duplicate record: that email is already used"),
    ("INSERT INTO semester(student_id,semester_no,academic_year) SELECT student_id,1,'2024-25' FROM student WHERE full_name='Asha Verma'", "already has that semester number"),
    ("INSERT INTO gap_year(student_id,start_year,end_year) SELECT student_id,2022,2021 FROM student WHERE full_name='Asha Verma'", "End year cannot be before start year."),
    ("UPDATE semester_marks SET theory_marks=101", "Theory marks must be between 0 and 100."),
    ("DELETE FROM subject", "other records still depend"),
    ("INSERT INTO student(full_name) VALUES (NULL)", "required field"),
])
def test_every_violation_gives_a_specific_readable_message(populated, sql_text, expected):
    d, _ = populated
    e = _catch(lambda: _run_sql(d, sql_text))
    assert expected in d.friendly_error(e)


def _run_sql(d, sql_text):
    with d.transaction() as cur:
        cur.execute(sql_text)
