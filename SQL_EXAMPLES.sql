-- =====================================================================
-- StudentTrack: modifying records directly in PostgreSQL (pgAdmin Query Tool or psql)
-- Run the sections one at a time (select the text, press F5 in pgAdmin).
-- The whole file is also safe to run top to bottom: it creates a 'Demo Student' and deletes it again at the end.
-- Direct changes reach Google Sheets automatically IF `python sync_worker.py` is running.
-- Rules to remember
--   * Never type an ID when inserting: IDs are GENERATED ALWAYS (the database numbers the rows).
--   * Always use WHERE on UPDATE/DELETE, otherwise EVERY row changes.
--   * NULL means "missing / not applicable"; 0 is a real mark. Use NULL, not 0, for a component that doesn't exist.
--   * pgAdmin auto-commits each statement. To be able to undo, wrap statements in BEGIN; ... ROLLBACK; (or COMMIT;)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. FIND THE IDs YOU NEED
-- ---------------------------------------------------------------------
SELECT student_id, full_name, email, current_status FROM student WHERE full_name ILIKE '%priya%';   -- case-insensitive partial name
SELECT subject_id, subject_code, subject_name, subject_type FROM subject ORDER BY subject_code;
SELECT category_id, category_name FROM activity_category;

-- ---------------------------------------------------------------------
-- 1. INSERT (create)  -- parents first, then children that point to them
-- ---------------------------------------------------------------------
-- 1a. a student
INSERT INTO student (full_name, email, current_status)
VALUES ('Demo Student', 'demo.student@example.com', 'Studying');

-- 1b. Secondary / Higher Secondary record (the student is found by e-mail instead of typing an ID)
INSERT INTO education_record (student_id, level, board, completion_year, percentage)
SELECT student_id, 'Secondary', 'CBSE', 2020, 88.5 FROM student WHERE email = 'demo.student@example.com';

-- 1c. a semester (SGPA/CGPA may be NULL if not yet declared)
INSERT INTO semester (student_id, semester_no, academic_year, sgpa, cgpa)
SELECT student_id, 1, '2023-24', 8.2, 8.2 FROM student WHERE email = 'demo.student@example.com';

-- 1d. a subject (shared by all students) and marks for it in that semester
INSERT INTO subject (subject_code, subject_name, subject_type) VALUES ('EC999', 'Demo Subject', 'Both');

INSERT INTO semester_marks (semester_id, subject_id, theory_marks, lab_marks)
SELECT sm.semester_id, sb.subject_id, 72, 85
FROM semester sm
JOIN student st ON st.student_id = sm.student_id
JOIN subject sb ON sb.subject_code = 'EC999'
WHERE st.email = 'demo.student@example.com' AND sm.semester_no = 1;

-- 1e. a recorded gap year, a seminar and an activity
INSERT INTO gap_year (student_id, start_year, end_year, reason)
SELECT student_id, 2024, 2024, 'Demo gap year' FROM student WHERE email = 'demo.student@example.com';

INSERT INTO seminar (student_id, title, seminar_type, event_date)
SELECT student_id, 'Demo Seminar', 'Internal', DATE '2024-02-01' FROM student WHERE email = 'demo.student@example.com';

INSERT INTO activity (student_id, category_id, title, activity_date)
SELECT st.student_id, c.category_id, 'Demo Hackathon', DATE '2024-02-10'
FROM student st, activity_category c
WHERE st.email = 'demo.student@example.com' AND c.category_name = 'Technical';

-- ---------------------------------------------------------------------
-- 2. UPDATE (change)
-- ---------------------------------------------------------------------
UPDATE student SET current_status = 'Graduated' WHERE email = 'demo.student@example.com';

UPDATE semester SET sgpa = 8.6, cgpa = 8.4
WHERE semester_no = 1 AND student_id = (SELECT student_id FROM student WHERE email = 'demo.student@example.com');

-- set a lab mark to "not applicable" (NULL, not 0)
UPDATE semester_marks SET lab_marks = NULL
WHERE subject_id = (SELECT subject_id FROM subject WHERE subject_code = 'EC999');

UPDATE seminar SET seminar_type = 'External' WHERE title = 'Demo Seminar';
UPDATE gap_year SET end_year = 2025 WHERE reason = 'Demo gap year';
UPDATE subject SET subject_name = 'Demo Subject (renamed)' WHERE subject_code = 'EC999';

-- ---------------------------------------------------------------------
-- 3. UNDOING A MISTAKE: transactions   (run these lines together, then choose ROLLBACK or COMMIT)
-- ---------------------------------------------------------------------
--   BEGIN;
--   UPDATE student SET full_name = 'Oops' WHERE email = 'demo.student@example.com';
--   SELECT full_name FROM student WHERE email = 'demo.student@example.com';   -- check the result
--   ROLLBACK;     -- undo everything since BEGIN   (or COMMIT; to keep it)
-- A rolled-back change never reaches Google Sheets: notifications are only delivered on COMMIT.

-- ---------------------------------------------------------------------
-- 4. DELETE (remove)
-- ---------------------------------------------------------------------
DELETE FROM seminar WHERE title = 'Demo Seminar';          -- one record
DELETE FROM activity WHERE title = 'Demo Hackathon';
-- Deleting a STUDENT also deletes their education, semesters, marks, gap years, seminars and activities (ON DELETE CASCADE):
DELETE FROM student WHERE email = 'demo.student@example.com';
-- A subject that still has marks, or a category that still has activities, CANNOT be deleted (ON DELETE RESTRICT).
DELETE FROM subject WHERE subject_code = 'EC999';          -- allowed now: its marks were removed by the cascade above

-- ---------------------------------------------------------------------
-- 5. CHECK THE RESULT
-- ---------------------------------------------------------------------
SELECT count(*) AS students FROM student;
SELECT s.full_name, sm.semester_no, sm.sgpa, sm.cgpa
FROM student s JOIN semester sm ON sm.student_id = s.student_id ORDER BY s.full_name, sm.semester_no;

-- ---------------------------------------------------------------------
-- 6. STATEMENTS THAT SHOULD FAIL (try them one at a time; each is rejected and nothing changes)
--    See USER_TECHNICAL_REPORT.md section 8 for the exact messages.
-- ---------------------------------------------------------------------
--   UPDATE semester SET sgpa = 11 WHERE semester_id = 1;                                  -- CHECK: 0..10
--   UPDATE semester_marks SET theory_marks = 101 WHERE mark_id = 1;                       -- CHECK: 0..100
--   INSERT INTO semester (student_id, semester_no, academic_year) VALUES (9999, 1, '2023-24');   -- FOREIGN KEY
--   INSERT INTO student (full_name, email) VALUES ('Dup', 'priya.nair@example.com');      -- UNIQUE e-mail
--   INSERT INTO gap_year (student_id, start_year, end_year) VALUES (1, 2022, 2021);       -- CHECK: end >= start
--   DELETE FROM subject WHERE subject_code = 'MA101';                                     -- RESTRICT (has marks)
