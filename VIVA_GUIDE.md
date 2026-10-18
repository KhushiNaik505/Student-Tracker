# Viva Preparation Guide

**What problem does this project solve?** It stores each student's whole academic history in one consistent database (instead of scattered files), shows progression charts, and mirrors the data to Google Sheets automatically.

**What is a relational database?** Data stored in tables (rows and columns) linked by keys; PostgreSQL enforces the rules and links. Here: `student`, `semester`, `subject`, etc.

**What is normalization?** Organising tables so each fact is stored once, which prevents update, insert and delete anomalies.

**1NF / 2NF / 3NF in this schema?**
- 1NF: atomic values, a PK in every table, no repeating columns (marks are rows in `semester_marks`, not `subject1, subject2`).
- 2NF: no attribute depends on only part of a composite key. `subject_name` depends only on the subject, so it lives in `subject`; marks depend on the pair (semester, subject).
- 3NF: no non-key depends on another non-key. Category name is in `activity_category`, not copied into each activity; the student's name is not copied into `semester`.

**Primary key?** A column that uniquely identifies each row, never NULL (`student_id`, generated automatically).

**Foreign key?** A column that must match a primary key in another table, e.g. `semester.student_id → student.student_id`.

**Referential integrity?** The database guarantees every foreign key points to an existing row. Test: inserting a semester for student 99999 is rejected (tested).

**ON DELETE CASCADE?** Deleting the parent deletes its children: deleting a student removes their semesters, marks, seminars… (tested). We use **RESTRICT** for `subject` and `activity_category` so you can't delete something that is in use.

**Purpose of CHECK constraints?** Reject impossible values inside the database itself, e.g. SGPA between 0 and 10, marks 0–100, `end_year >= start_year`, status in a fixed list. Even a direct SQL insert cannot bypass them.

**Why are subjects and semester marks separate tables?** A subject (code, name, type) is one fact reused in many semesters and students. `semester_marks` links a semester to a subject with the marks, so no subject name is repeated and any subject can be used in any semester without changing the schema.

**What is an SQL JOIN?** Combining rows from tables using related keys. `marks_report` joins `semester_marks`, `semester` and `subject` to show subject name next to the marks.

**Database indexes?** Extra lookup structures (like a book index) that speed up searches. We index `lower(full_name)` for name search, `lower(email)` for uniqueness, and foreign-key columns.

**How does Python talk to PostgreSQL?** Through `psycopg2`: open a connection, run SQL with `%s` placeholders (user values passed separately, preventing SQL injection), then commit or roll back (`db.py`).

**How does the GUI do CRUD?** A form button calls `validation.py` to check/clean input, then `db.insert/update/delete` inside a transaction, then shows a message and reloads. Data is in PostgreSQL, so refresh doesn't lose it.

**How is the progression chart generated?** `db.progression_rows` selects semesters ordered by `semester_no`; `progression.build_progression` fills missing semester numbers with None (so Plotly breaks the line, nothing is invented) and places recorded gap years; `app.py` draws SGPA/CGPA lines, y-axis 0–10, and an orange band for gap years.

**How does Google Sheets synchronization work?** After each GUI commit, `sync.sync_all()` reads all 9 tables in one consistent snapshot and, via gspread with a service account, rewrites each worksheet only if it differs from the DB. It's a full refresh, so no duplicates and deletions propagate.

**How are direct database changes detected?** Triggers on every exported table run `pg_notify`. A notification is only delivered on COMMIT. `sync_worker.py` listens, waits 2 s to batch bursts, then re-syncs from the current DB state. No Google calls happen inside the database.

**Is the synchronisation one-way or two-way?** Both. Database → sheet is always on. Sheet → database is implemented too: a person's edit in the sheet is detected, validated with the same rules as the GUI plus the database constraints, and only then saved. The database stays the source of truth.

**How do you know which cells the user edited?** After every sync the program stores a *baseline* (a copy of what it wrote) in `sync_baseline`. A sheet row that differs from its baseline row was edited by a person. A database change updates the baseline too, so it is never mistaken for a sheet edit.

**What if someone types an invalid value, e.g. SGPA 11, in the sheet?** `validation.py` rejects it (and the CHECK constraint would too). Nothing is saved, the next sync restores the real value, and the reason is written to `sync_log`, shown in the Sync_Log worksheet and on the GUI sync page.

**What if the sheet and the database are both changed?** Conflict: the database value wins and the rejection is logged.

**Why isn't deleting a sheet row propagated?** A stray key press in a spreadsheet would cascade-delete a student's whole history. Deletions must be done in the app or in SQL; the row reappears on the sheet. (A deliberate safety decision.)

**Why does the worker poll the sheet?** Google Sheets cannot push events to a local program (that would need a cloud-side Apps Script and a public URL). Polling every 30 s uses about 20 of Google's 60 reads/min.

**Why can't a user type an ID for a new sheet row?** IDs are generated by the database (`GENERATED ALWAYS AS IDENTITY`), so duplicates are impossible; a new row must have an empty ID cell and the database fills it in.

**What if Google Sheets is unavailable?** The database save still succeeds. Temporary errors are retried with back-off; permanent ones (no permission, wrong ID) show a clear warning. The GUI never claims success on failure, and **Sync Now** retries. The worker retries every 15 s.

**How are duplicates prevented?** UNIQUE constraints: e-mail (case-insensitive), (student, semester_no), (student, level), (semester, subject), subject_code; and in Sheets by refreshing instead of appending.

**Limitations?** Sheet edits are picked up with up to 30 s delay; an edit typed during the few seconds a sync is writing can be overwritten; the worker must be running for direct-SQL changes and for polling; full refresh isn't for huge data; CGPA entered manually; Google quotas; no login system; the real-Google path was not tested by me before submission day (state this honestly if you haven't tested it).

### Likely follow-ups
- *Why NULL instead of 0 for lab marks?* 0 is a real score; NULL means "not applicable / not entered".
- *Why a trigger for the sheet and not Python polling?* Event-driven, no repeated polling, and only committed data is notified.
- *Is the sync two-way?* Yes, with validation: valid edits are saved; invalid ones are reverted and logged; the DB is always the source of truth.
- *Show a violated constraint live:* Semester Records → SGPA 11 → rejected.
