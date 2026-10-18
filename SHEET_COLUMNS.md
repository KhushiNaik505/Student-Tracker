# Google Sheet column specification (row 1 = header; rows ordered by primary key; empty cell = NULL)
| Worksheet | Columns |
|---|---|
| Students | student_id, full_name, email, current_status |
| Education | education_id, student_id, level, board, completion_year, percentage |
| Semesters | semester_id, student_id, semester_no, academic_year, sgpa, cgpa |
| Subjects | subject_id, subject_code, subject_name, subject_type |
| Subject_Marks | mark_id, semester_id, subject_id, theory_marks, lab_marks |
| Gap_Years | gap_id, student_id, start_year, end_year, reason |
| Seminars | seminar_id, student_id, title, seminar_type, event_date (YYYY-MM-DD) |
| Activity_Categories | category_id, category_name |
| Activities | activity_id, student_id, category_id, title, activity_date (YYYY-MM-DD) |
| Sync_Log (read-only) | log_id, logged_at_utc, worksheet, sheet_row, record_id, outcome (applied/rejected), message — latest 50, newest first |
Relationships are kept through the same ID columns as the database (e.g. Semesters.student_id → Students.student_id).

## Editing rules (two-way sync)
- Edit existing cells, or type a **new row with an empty ID cell** (the database assigns the ID).
- ID columns cannot be edited. Deleting a row does not delete the record (it reappears). Activity_Categories and Sync_Log are read-only. Do not rename the header row or insert columns.
- Dates: `YYYY-MM-DD`. Academic year: `2023-24`. Blank numeric cell = NULL.
- Each edit is validated like the GUI (SGPA/CGPA 0-10, marks 0-100, statuses, e-mail format, unique values, theory/lab vs subject type). A rejected edit is reverted and explained in Sync_Log.
