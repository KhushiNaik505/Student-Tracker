-- =====================================================================
-- StudentTrack schema (PostgreSQL 13+)  --  safe to run repeatedly.
-- Tables are created with IF NOT EXISTS; triggers/functions are replaced.
-- =====================================================================

-- A. student ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS student (
    student_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name       VARCHAR(100) NOT NULL CHECK (btrim(full_name) <> ''),
    email           VARCHAR(150) CHECK (email IS NULL OR email ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    current_status  VARCHAR(12)  NOT NULL DEFAULT 'Studying'
                    CHECK (current_status IN ('Studying','Graduated','Discontinued'))
);
-- unique e-mail (case-insensitive) but only when an e-mail is given
CREATE UNIQUE INDEX IF NOT EXISTS uq_student_email ON student (lower(email)) WHERE email IS NOT NULL;
-- fast case-insensitive name lookups
CREATE INDEX IF NOT EXISTS idx_student_name_lower ON student (lower(full_name));

-- B. education_record -------------------------------------------------
CREATE TABLE IF NOT EXISTS education_record (
    education_id     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id       INTEGER NOT NULL REFERENCES student(student_id) ON DELETE CASCADE,
    level            VARCHAR(20) NOT NULL CHECK (level IN ('Secondary','Higher Secondary')),
    board            VARCHAR(100) NOT NULL CHECK (btrim(board) <> ''),
    completion_year  INTEGER NOT NULL CHECK (completion_year BETWEEN 1950 AND 2100),
    percentage       NUMERIC(5,2) NOT NULL CHECK (percentage BETWEEN 0 AND 100),
    CONSTRAINT uq_education_student_level UNIQUE (student_id, level)
);

-- C. semester ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS semester (
    semester_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id     INTEGER NOT NULL REFERENCES student(student_id) ON DELETE CASCADE,
    semester_no    SMALLINT NOT NULL CHECK (semester_no BETWEEN 1 AND 12),
    academic_year  VARCHAR(7) NOT NULL CHECK (academic_year ~ '^[0-9]{4}-[0-9]{2}$'),   -- e.g. 2023-24
    sgpa           NUMERIC(4,2) CHECK (sgpa BETWEEN 0 AND 10),   -- NULL = not yet declared
    cgpa           NUMERIC(4,2) CHECK (cgpa BETWEEN 0 AND 10),
    CONSTRAINT uq_semester_student_no UNIQUE (student_id, semester_no)
);

-- D. subject ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS subject (
    subject_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_code  VARCHAR(20)  NOT NULL UNIQUE CHECK (btrim(subject_code) <> ''),
    subject_name  VARCHAR(150) NOT NULL CHECK (btrim(subject_name) <> ''),
    subject_type  VARCHAR(6)   NOT NULL CHECK (subject_type IN ('Theory','Lab','Both'))
);

-- E. semester_marks ---------------------------------------------------
CREATE TABLE IF NOT EXISTS semester_marks (
    mark_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    semester_id   INTEGER NOT NULL REFERENCES semester(semester_id) ON DELETE CASCADE,
    subject_id    INTEGER NOT NULL REFERENCES subject(subject_id)   ON DELETE RESTRICT,
    theory_marks  NUMERIC(5,2) CHECK (theory_marks BETWEEN 0 AND 100),  -- NULL = not applicable / not entered
    lab_marks     NUMERIC(5,2) CHECK (lab_marks    BETWEEN 0 AND 100),
    CONSTRAINT uq_marks_semester_subject UNIQUE (semester_id, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_marks_subject ON semester_marks(subject_id);

-- F. gap_year ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS gap_year (
    gap_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id  INTEGER NOT NULL REFERENCES student(student_id) ON DELETE CASCADE,
    start_year  INTEGER NOT NULL CHECK (start_year BETWEEN 1950 AND 2100),
    end_year    INTEGER NOT NULL CHECK (end_year   BETWEEN 1950 AND 2100),
    reason      TEXT,
    CONSTRAINT ck_gap_order CHECK (end_year >= start_year),
    CONSTRAINT uq_gap_student_period UNIQUE (student_id, start_year, end_year)
);

-- G. seminar ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS seminar (
    seminar_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id    INTEGER NOT NULL REFERENCES student(student_id) ON DELETE CASCADE,
    title         VARCHAR(200) NOT NULL CHECK (btrim(title) <> ''),
    seminar_type  VARCHAR(8) NOT NULL CHECK (seminar_type IN ('Internal','External')),
    event_date    DATE NOT NULL,
    CONSTRAINT uq_seminar UNIQUE (student_id, title, event_date)
);

-- H. activity_category ------------------------------------------------
CREATE TABLE IF NOT EXISTS activity_category (
    category_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_name  VARCHAR(20) NOT NULL UNIQUE
                   CHECK (category_name IN ('Technical','Co-curricular','Extra-curricular'))
);
INSERT INTO activity_category (category_name)
VALUES ('Technical'),('Co-curricular'),('Extra-curricular')
ON CONFLICT (category_name) DO NOTHING;

-- I. activity ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity (
    activity_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id     INTEGER NOT NULL REFERENCES student(student_id) ON DELETE CASCADE,
    category_id    INTEGER NOT NULL REFERENCES activity_category(category_id) ON DELETE RESTRICT,
    title          VARCHAR(200) NOT NULL CHECK (btrim(title) <> ''),
    activity_date  DATE NOT NULL,
    CONSTRAINT uq_activity UNIQUE (student_id, category_id, title, activity_date)
);
CREATE INDEX IF NOT EXISTS idx_activity_category ON activity(category_id);
CREATE INDEX IF NOT EXISTS idx_activity_student  ON activity(student_id);
CREATE INDEX IF NOT EXISTS idx_gap_student       ON gap_year(student_id);

-- Bookkeeping table used by the sync worker / GUI (NOT exported to Sheets)
CREATE TABLE IF NOT EXISTS sync_status (
    component       VARCHAR(30) PRIMARY KEY,           -- 'worker'
    last_heartbeat  TIMESTAMPTZ,
    last_sync_at    TIMESTAMPTZ,
    last_result     VARCHAR(10),                       -- 'ok' / 'error'
    last_error      TEXT
);

-- Two-way sync bookkeeping (NOT exported as data worksheets, NOT change-notified)
-- sync_baseline: what each worksheet looked like right after the last sync. Comparing the live sheet with this
--                baseline is how we tell a USER's edit in Google Sheets from a change that came from the database.
-- sync_log:      every sheet edit that was applied or rejected (shown in the Sync_Log worksheet and the GUI).
CREATE TABLE IF NOT EXISTS sync_baseline (
    worksheet  VARCHAR(30) NOT NULL,
    row_key    VARCHAR(40) NOT NULL,          -- primary-key value as text, or '__header__'
    cells      JSONB NOT NULL,
    PRIMARY KEY (worksheet, row_key)
);
CREATE TABLE IF NOT EXISTS sync_log (
    log_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    logged_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    worksheet  VARCHAR(30) NOT NULL,
    sheet_row  INTEGER,
    record_id  TEXT,
    outcome    VARCHAR(10) NOT NULL CHECK (outcome IN ('applied','rejected')),
    message    TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Trigger 1: a Theory-only subject cannot carry lab marks (and vice-versa)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION check_marks_match_subject_type() RETURNS trigger AS $$
DECLARE st VARCHAR(6);
BEGIN
    SELECT subject_type INTO st FROM subject WHERE subject_id = NEW.subject_id;
    IF st = 'Theory' AND NEW.lab_marks IS NOT NULL THEN
        RAISE EXCEPTION 'Subject is Theory-only: lab marks must be empty (NULL).' USING ERRCODE = 'P0001';
    ELSIF st = 'Lab' AND NEW.theory_marks IS NOT NULL THEN
        RAISE EXCEPTION 'Subject is Lab-only: theory marks must be empty (NULL).' USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_marks_match_type ON semester_marks;
CREATE TRIGGER trg_marks_match_type BEFORE INSERT OR UPDATE ON semester_marks
FOR EACH ROW EXECUTE FUNCTION check_marks_match_subject_type();

-- ---------------------------------------------------------------------
-- Trigger 2: change notification for the Google-Sheets sync worker.
-- pg_notify is TRANSACTIONAL: the message is delivered only on COMMIT,
-- and is discarded on ROLLBACK. No Google API call happens in the DB.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION notify_db_change() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('studenttrack_changes', TG_TABLE_NAME);
    RETURN NULL;
END; $$ LANGUAGE plpgsql;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['student','education_record','semester','subject','semester_marks',
                             'gap_year','seminar','activity_category','activity']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_notify_%I ON %I', t, t);
        EXECUTE format('CREATE TRIGGER trg_notify_%I AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION notify_db_change()', t, t);
    END LOOP;
END $$;
