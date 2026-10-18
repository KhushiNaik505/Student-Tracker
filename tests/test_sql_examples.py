"""The SQL tutorial shipped with the project must actually run (it is quoted in the report)."""
import os

import pytest

pytestmark = pytest.mark.live_pg
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_sql_examples_file_runs_and_cleans_up_after_itself(pg):
    with pg.transaction() as cur:
        cur.execute(open(os.path.join(ROOT, "seed.sql"), encoding="utf-8").read())
    before = {t: pg.query(f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in
              ["student", "education_record", "semester", "semester_marks", "subject", "gap_year", "seminar", "activity"]}
    with pg.transaction() as cur:                                  # the whole file, top to bottom, one transaction
        cur.execute(open(os.path.join(ROOT, "SQL_EXAMPLES.sql"), encoding="utf-8").read())
    after = {t: pg.query(f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in before}
    assert after == before


def test_transaction_rollback_example_really_undoes_change(pg):
    with pg.transaction() as cur:
        cur.execute("INSERT INTO student (full_name, email) VALUES ('Demo Student','demo.student@example.com')")
    conn = pg.connect()
    cur = conn.cursor()
    cur.execute("BEGIN")
    cur.execute("UPDATE student SET full_name='Oops' WHERE email='demo.student@example.com'")
    conn.rollback(); conn.close()
    assert pg.query("SELECT full_name FROM student WHERE email='demo.student@example.com'")[0]["full_name"] == "Demo Student"
