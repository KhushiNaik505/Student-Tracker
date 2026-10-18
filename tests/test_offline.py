"""Tests that need NEITHER PostgreSQL NOR Google."""
from datetime import date
from decimal import Decimal

import pytest

import progression
import sync
import validation as v
from tests.fake_sheets import FakeApiError


# ---- validation ----
def test_student_validation_ok_and_trims():
    assert v.student("  Ann  ", " ", "Studying") == {"full_name": "Ann", "email": None, "current_status": "Studying"}


@pytest.mark.parametrize("args", [("", None, "Studying"), ("A", "bad-email", "Studying"), ("A", None, "Unknown")])
def test_student_validation_rejects(args):
    with pytest.raises(v.ValidationError):
        v.student(*args)


def test_semester_validation():
    assert v.semester(1, 2, "2023-24", 8.5, None)["cgpa"] is None
    for bad in [dict(sgpa=10.5), dict(sgpa=-1), dict(academic_year="2023-25"), dict(academic_year="23-24"), dict(no=13)]:
        kw = dict(student_id=1, no=1, academic_year="2023-24", sgpa=8, cgpa=8); kw.update(bad)
        with pytest.raises(v.ValidationError):
            v.semester(kw["student_id"], kw["no"], kw["academic_year"], kw["sgpa"], kw["cgpa"])


def test_marks_validation_range_and_type():
    with pytest.raises(v.ValidationError):
        v.marks(1, 1, 101, None)
    with pytest.raises(v.ValidationError):
        v.marks(1, 1, 50, 50, subject_type="Theory")
    assert v.marks(1, 1, None, 0)["lab_marks"] == 0.0  # zero is a real mark, not NULL


def test_gap_validation_end_before_start():
    with pytest.raises(v.ValidationError):
        v.gap(1, 2022, 2021, "x")


# ---- progression logic (tests 13/14 pure part) ----
def rows(*t):
    return [dict(semester_no=n, academic_year=ay, sgpa=s, cgpa=c) for n, ay, s, c in t]


def test_progression_sorted_and_never_invents_values():
    p = progression.build_progression(rows((4, "2021-22", 8, 8), (1, "2020-21", 7, 7), (2, "2020-21", None, None)), [])
    assert [s["semester_no"] for s in p["series"]] == [1, 2, 3, 4]
    s3 = p["series"][2]
    assert s3["recorded"] is False and s3["sgpa"] is None and s3["cgpa"] is None
    assert p["series"][1]["recorded"] is True and p["series"][1]["sgpa"] is None  # recorded but grade missing
    assert p["missing"] == [3]


def test_gap_year_distinct_from_missing_semester():
    # sems 1-2 in 2020-21, gap in 2021, sem 3 in 2022-23  -> gap explicitly shown, no 'missing'
    p = progression.build_progression(rows((1, "2020-21", 7, 7), (2, "2020-21", 7, 7), (3, "2022-23", 8, 8)),
                                      [dict(start_year=2021, end_year=2021, reason="Health")])
    assert p["gaps"][0]["x0"] == 2 and p["gaps"][0]["x1"] == 3 and p["missing"] == []
    # a recorded gap that explains an unrecorded semester number
    p = progression.build_progression(rows((1, "2020-21", 7, 7), (3, "2022-23", 8, 8)),
                                      [dict(start_year=2021, end_year=2021, reason="x")])
    assert p["missing"] == []
    # same data, NO gap record -> semester 2 is merely missing
    p = progression.build_progression(rows((1, "2020-21", 7, 7), (3, "2022-23", 8, 8)), [])
    assert p["missing"] == [2] and p["gaps"] == []


def test_progression_empty():
    assert progression.build_progression([], [])["series"] == []


# ---- sync helpers ----
def test_cell_conversion_and_normalisation():
    assert sync.to_cell(None) == "" and sync.to_cell(Decimal("8.50")) == 8.5
    assert sync.to_cell(date(2023, 1, 2)) == "2023-01-02"
    assert sync.norm("8.50") == sync.norm(8.5) == sync.norm("8.5") == "8.5"
    assert sync.norm("7") == sync.norm(7.0) and sync.norm(None) == ""


def test_retry_retries_transient_only():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise FakeApiError(503)
        return "ok"
    assert sync.with_retry(flaky, sleep=sleeps.append) == "ok" and len(calls) == 3 and sleeps == [1.5, 3.0]
    calls.clear()

    def forbidden():
        calls.append(1)
        raise FakeApiError(403)
    with pytest.raises(FakeApiError):
        sync.with_retry(forbidden, sleep=sleeps.append)
    assert len(calls) == 1  # non-transient: no retry


def test_sync_without_credentials_or_database_is_graceful(monkeypatch):
    """Test 18 (part 1): no credentials / unreachable DB -> clear message, no exception, ok=False."""
    monkeypatch.delenv("GOOGLE_SHEETS_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    monkeypatch.setenv("DB_PORT", "1")  # nothing listens here
    res = sync.sync_all()
    assert res.ok is False and res.error
    assert "password" not in res.error.lower()


def test_get_spreadsheet_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_CREDENTIALS_FILE", raising=False)
    with pytest.raises(sync.SyncNotConfigured):
        sync.get_spreadsheet()


def test_error_descriptions_hide_internals():
    assert "Share the spreadsheet" in sync._describe(FakeApiError(403))
    assert "quota" in sync._describe(FakeApiError(429)).lower()
