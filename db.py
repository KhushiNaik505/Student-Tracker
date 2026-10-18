"""Database layer: connections, transactions, generic CRUD and all queries.
Every query is parameterised; table/column names come from a fixed whitelist."""
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import sql

import config

# table -> (primary key, writable columns).  Whitelist used by generic CRUD.
TABLES = {
    "student": ("student_id", ["full_name", "email", "current_status"]),
    "education_record": ("education_id", ["student_id", "level", "board", "completion_year", "percentage"]),
    "semester": ("semester_id", ["student_id", "semester_no", "academic_year", "sgpa", "cgpa"]),
    "subject": ("subject_id", ["subject_code", "subject_name", "subject_type"]),
    "semester_marks": ("mark_id", ["semester_id", "subject_id", "theory_marks", "lab_marks"]),
    "gap_year": ("gap_id", ["student_id", "start_year", "end_year", "reason"]),
    "seminar": ("seminar_id", ["student_id", "title", "seminar_type", "event_date"]),
    "activity": ("activity_id", ["student_id", "category_id", "title", "activity_date"]),
}


def connect(dbname=None):
    return psycopg2.connect(**config.db_params(dbname))


@contextmanager
def transaction(dbname=None):
    """One transaction: COMMIT on success, ROLLBACK on any exception."""
    conn = connect(dbname)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql_text, params=None, dbname=None):
    """Run a SELECT, return list of dicts."""
    with transaction(dbname) as cur:
        cur.execute(sql_text, params or ())
        return [dict(r) for r in cur.fetchall()]


# constraint name -> message shown to the user (anything not listed falls back to a generic message)
CONSTRAINT_MESSAGES = {
    "semester_sgpa_check": "SGPA must be between 0 and 10.",
    "semester_cgpa_check": "CGPA must be between 0 and 10.",
    "semester_semester_no_check": "Semester number must be between 1 and 12.",
    "semester_academic_year_check": "Academic year must look like 2023-24.",
    "semester_marks_theory_marks_check": "Theory marks must be between 0 and 100.",
    "semester_marks_lab_marks_check": "Lab marks must be between 0 and 100.",
    "student_current_status_check": "Status must be Studying, Graduated or Discontinued.",
    "student_email_check": "Email format is invalid.",
    "student_full_name_check": "Full name cannot be blank.",
    "education_record_level_check": "Level must be Secondary or Higher Secondary.",
    "education_record_percentage_check": "Percentage must be between 0 and 100.",
    "ck_gap_order": "End year cannot be before start year.",
    "seminar_seminar_type_check": "Seminar type must be Internal or External.",
    "subject_subject_type_check": "Subject type must be Theory, Lab or Both.",
    "activity_category_category_name_check": "Category must be Technical, Co-curricular or Extra-curricular.",
    "uq_student_email": "Duplicate record: that email is already used by another student.",
    "uq_semester_student_no": "Duplicate record: this student already has that semester number.",
    "uq_marks_semester_subject": "Duplicate record: marks for this subject already exist in that semester.",
    "uq_education_student_level": "Duplicate record: this student already has a record for that level.",
    "subject_subject_code_key": "Duplicate record: that subject code already exists.",
    "uq_gap_student_period": "Duplicate record: this gap year is already recorded.",
    "uq_seminar": "Duplicate record: this seminar is already recorded.",
    "uq_activity": "Duplicate record: this activity is already recorded.",
}


def friendly_error(exc):
    """Translate a psycopg2 error into a message safe to show users (no secrets)."""
    if isinstance(exc, psycopg2.OperationalError):
        return "Cannot reach the database. Check that PostgreSQL is running and your .env settings are correct."
    code = getattr(exc, "pgcode", None)
    detail = (getattr(getattr(exc, "diag", None), "message_detail", None) or "")
    cname = getattr(getattr(exc, "diag", None), "constraint_name", None) or ""
    if code in ("23505", "23514") and cname in CONSTRAINT_MESSAGES:
        return CONSTRAINT_MESSAGES[cname]
    if code == "23505":
        return f"Duplicate record: that value already exists ({cname or 'unique rule'})."
    if code == "23503":
        return "Invalid reference: the related record does not exist, or other records still depend on this one."
    if code == "23514":
        return f"Value rejected by a database rule ({cname or 'check constraint'})."
    if code == "23502":
        return "A required field was left empty."
    if code == "P0001":
        return (getattr(getattr(exc, "diag", None), "message_primary", None) or "Rejected by a database rule.")
    if code and code.startswith("22"):
        return "Invalid value format."
    return "Database error. The change was not saved."


# ---------- generic CRUD (transactional) ----------
def insert(table, data):
    pk, cols = TABLES[table]
    data = {k: v for k, v in data.items() if k in cols}
    q = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, data)),
        sql.SQL(", ").join(sql.Placeholder() * len(data)), sql.Identifier(pk))
    with transaction() as cur:
        cur.execute(q, list(data.values()))
        return cur.fetchone()[pk]


def update(table, pk_value, data):
    pk, cols = TABLES[table]
    data = {k: v for k, v in data.items() if k in cols}
    q = sql.SQL("UPDATE {} SET {} WHERE {} = %s").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in data),
        sql.Identifier(pk))
    with transaction() as cur:
        cur.execute(q, list(data.values()) + [pk_value])
        return cur.rowcount


def delete(table, pk_value):
    pk, _ = TABLES[table]
    with transaction() as cur:
        cur.execute(sql.SQL("DELETE FROM {} WHERE {} = %s").format(sql.Identifier(table), sql.Identifier(pk)), (pk_value,))
        return cur.rowcount


# ---------- students ----------
def _like(term):
    """Escape LIKE wildcards so user text is matched literally."""
    return "%" + term.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def search_students(term=None):
    if term and term.strip():
        return query("SELECT * FROM student WHERE full_name ILIKE %s ORDER BY full_name", (_like(term),))
    return query("SELECT * FROM student ORDER BY full_name")


def get_student(student_id):
    r = query("SELECT * FROM student WHERE student_id = %s", (student_id,))
    return r[0] if r else None


# ---------- lookups / lists ----------
def list_education(student_id):
    return query("SELECT * FROM education_record WHERE student_id=%s ORDER BY completion_year", (student_id,))


def list_semesters(student_id):
    return query("SELECT * FROM semester WHERE student_id=%s ORDER BY semester_no", (student_id,))


def list_subjects():
    return query("SELECT * FROM subject ORDER BY subject_code")


def list_gaps(student_id):
    return query("SELECT * FROM gap_year WHERE student_id=%s ORDER BY start_year", (student_id,))


def list_categories():
    return query("SELECT * FROM activity_category ORDER BY category_id")


def marks_report(student_id, semester_id=None):
    """Semester-wise theory & lab marks (JOIN of semester_marks, semester, subject)."""
    sqlt = """SELECT m.mark_id, sm.semester_id, sm.semester_no, sm.academic_year, s.subject_id, s.subject_code,
                     s.subject_name, s.subject_type, m.theory_marks, m.lab_marks
              FROM semester_marks m
              JOIN semester sm ON sm.semester_id = m.semester_id
              JOIN subject  s  ON s.subject_id  = m.subject_id
              WHERE sm.student_id = %s"""
    params = [student_id]
    if semester_id:
        sqlt += " AND sm.semester_id = %s"
        params.append(semester_id)
    return query(sqlt + " ORDER BY sm.semester_no, s.subject_code", params)


def list_seminars(student_id, seminar_type=None):
    sqlt, p = "SELECT * FROM seminar WHERE student_id=%s", [student_id]
    if seminar_type:
        sqlt += " AND seminar_type=%s"
        p.append(seminar_type)
    return query(sqlt + " ORDER BY event_date", p)


def list_activities(student_id, category_name=None):
    sqlt = """SELECT a.activity_id, a.student_id, a.category_id, c.category_name, a.title, a.activity_date
              FROM activity a JOIN activity_category c ON c.category_id = a.category_id
              WHERE a.student_id=%s"""
    p = [student_id]
    if category_name:
        sqlt += " AND c.category_name=%s"
        p.append(category_name)
    return query(sqlt + " ORDER BY a.activity_date", p)


def progression_rows(student_id):
    """Chart data straight from the semester table, ordered by semester number."""
    return query("""SELECT semester_no, academic_year, sgpa::float AS sgpa, cgpa::float AS cgpa
                    FROM semester WHERE student_id=%s ORDER BY semester_no""", (student_id,))


# ---------- dashboard ----------
def dashboard_kpis():
    k = query("""SELECT (SELECT count(*) FROM student) AS students,
                        (SELECT count(*) FROM semester) AS semesters,
                        (SELECT count(*) FROM seminar) AS seminars,
                        (SELECT count(*) FROM activity) AS activities,
                        (SELECT count(*) FROM gap_year) AS gap_years,
                        (SELECT round(avg(cgpa),2)::float FROM
                           (SELECT DISTINCT ON (student_id) cgpa FROM semester WHERE cgpa IS NOT NULL
                            ORDER BY student_id, semester_no DESC) latest) AS avg_latest_cgpa""")[0]
    k["by_status"] = query("SELECT current_status, count(*)::int AS n FROM student GROUP BY 1 ORDER BY 1")
    k["by_category"] = query("""SELECT c.category_name, count(a.activity_id)::int AS n FROM activity_category c
                                LEFT JOIN activity a ON a.category_id=c.category_id GROUP BY 1, c.category_id ORDER BY c.category_id""")
    k["avg_sgpa_by_sem"] = query("""SELECT semester_no, round(avg(sgpa),2)::float AS avg_sgpa FROM semester
                                    WHERE sgpa IS NOT NULL GROUP BY 1 ORDER BY 1""")
    return k


# ---------- sync status (bookkeeping) ----------
def worker_status():
    r = query("""SELECT last_heartbeat, last_sync_at, last_result, last_error,
                        (last_heartbeat > now() - interval '30 seconds') AS alive
                 FROM sync_status WHERE component='worker'""")
    return r[0] if r else None
