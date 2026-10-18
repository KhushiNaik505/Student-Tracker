"""Drives the real Streamlit app headlessly (streamlit.testing AppTest) against the TEST database."""
import os

import pytest
from streamlit.testing.v1 import AppTest


pytestmark = pytest.mark.live_pg
APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
PAGES = ["Dashboard", "Student Management", "Education Records", "Semester Records", "Subject and Lab Marks",
         "Academic Progression", "Gap-Year Timeline", "Seminars", "Activities and Portfolio", "Google Sheets Synchronization"]


@pytest.fixture
def seeded(pg, monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_CREDENTIALS_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    with pg.transaction() as cur:
        cur.execute(open(os.path.join(os.path.dirname(APP), "seed.sql"), encoding="utf-8").read())
    return pg


def open_page(page, student=None):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    at.sidebar.radio[0].set_value(page)
    if student:
        at.sidebar.text_input[0].set_value(student)
    return at.run()


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_without_error(seeded, page):
    at = open_page(page, "priya")
    assert not at.exception, [e.value for e in at.exception]


def test_dashboard_kpis_match_database(seeded):
    at = open_page("Dashboard")
    vals = {m.label: m.value for m in at.metric}
    assert vals["Students"] == "6" and vals["Gap years"] == "2"


def test_empty_database_shows_empty_state(pg):
    at = open_page("Dashboard")
    assert not at.exception and any("empty" in i.value.lower() for i in at.info)


def test_add_student_through_gui_persists(seeded):
    at = open_page("Student Management")
    [t for t in at.text_input if t.label.startswith("Full name")][0].set_value("GUI Created Student")
    [b for b in at.button if b.label == "Add student"][0].click()
    at.run()
    assert not at.exception
    assert [r["full_name"] for r in seeded.search_students("gui created")] == ["GUI Created Student"]
    # DB save and Sheets sync are reported separately (Sheets deliberately not configured here)
    msgs = " ".join(x.value for x in list(at.success) + list(at.info))
    assert "Saved in the database" in msgs and "not configured" in msgs


def test_invalid_input_rejected_in_gui(seeded):
    at = open_page("Student Management")
    [t for t in at.text_input if t.label.startswith("Full name")][0].set_value("   ")
    [b for b in at.button if b.label == "Add student"][0].click()
    at.run()
    assert any("required" in w.value.lower() for w in at.warning)
    assert len(seeded.search_students("")) == 6


def test_search_is_case_insensitive_in_gui(seeded):
    at = open_page("Student Management")
    [t for t in at.text_input if t.label.startswith("🔍")][0].set_value("sHaRmA")
    at.run()
    names = [r for df in at.dataframe for r in df.value["full_name"].tolist()]
    assert names == ["Aarav Sharma"]


def test_sync_page_shows_two_way_status_and_log(seeded):
    seeded.insert("student", {"full_name": "Log Probe", "email": None, "current_status": "Studying"})
    import sync
    sync.write_log([dict(worksheet="Students", row=2, key="1", outcome="rejected", message="Rejected: probe message")])
    at = open_page("Google Sheets Synchronization")
    assert not at.exception, [e.value for e in at.exception]
    text = " ".join(m.value for m in at.markdown)
    assert "Two-way sync" in text
    assert any("probe message" in str(df.value) for df in at.dataframe)
