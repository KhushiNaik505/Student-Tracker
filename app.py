"""StudentTrack - Student Academic Progression and Portfolio Management System (Streamlit GUI).
Run with:  streamlit run app.py
"""
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg2
import streamlit as st

import config
import db
import progression
import sync
import validation as v

st.set_page_config(page_title="StudentTrack", page_icon="🎓", layout="wide")

PAGES = ["Dashboard", "Student Management", "Education Records", "Semester Records", "Subject and Lab Marks",
         "Academic Progression", "Gap-Year Timeline", "Seminars", "Activities and Portfolio",
         "Google Sheets Synchronization"]
DATE_MIN, DATE_MAX = date(1990, 1, 1), date(2100, 12, 31)


# ------------------------------------------------------------------ helpers
def flash(kind, msg):
    st.session_state.setdefault("flash", []).append((kind, msg))


def show_flash():
    for kind, msg in st.session_state.pop("flash", []):
        {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}[kind](msg)


def run_sync():
    """Called ONLY after a database transaction has committed. Stores + reports the sync outcome."""
    if not config.sheets_configured():
        st.session_state["last_sync"] = {"ok": False, "when": datetime.now(), "error": "Google Sheets is not configured.", "tables": {}}
        flash("info", "ℹ️ Google Sheets sync skipped: not configured yet (see the Google Sheets Synchronization page).")
        return
    with st.spinner("Syncing to Google Sheets..."):
        res = sync.sync_all()
    st.session_state["last_sync"] = {"ok": res.ok, "when": res.finished, "error": res.error, "tables": res.tables,
                                     "applied": res.pulled_applied, "rejected": res.pulled_rejected}
    if res.pulled_applied:
        flash("info", f"📥 {res.pulled_applied} edit(s) typed in Google Sheets passed validation and were saved to the database.")
    if res.pulled_rejected:
        flash("warning", f"⚠️ {len(res.pulled_rejected)} edit(s) in Google Sheets were rejected and reverted. "
                         f"First reason: {res.pulled_rejected[0]['message']} (details: Sync page / Sync_Log worksheet)")
    if res.ok:
        flash("success", "📊 Google Sheets synchronised.")
    else:
        flash("warning", f"⚠️ The database change IS saved, but Google Sheets sync failed: {res.error} Use 'Sync Now' to retry.")


def save(action, ok_msg):
    """Run one DB write. Validation/DB problems are shown in place; on success: flash, sync, rerun."""
    try:
        action()
    except v.ValidationError as e:
        for m in e.errors:
            st.warning(m)
        return False
    except psycopg2.Error as e:
        st.error("❌ Not saved. " + db.friendly_error(e))
        return False
    flash("success", f"✅ Saved in the database: {ok_msg}")
    run_sync()
    st.rerun()


def confirm_delete(label, key, action, ok_msg):
    st.markdown("**Delete**")
    sure = st.checkbox(f"Yes, permanently delete {label}", key=f"{key}_sure")
    if st.button("🗑️ Delete", key=f"{key}_btn", disabled=not sure, type="primary"):
        save(action, ok_msg)


def blank(text):
    text = (text or "").strip()
    return text or None


def table(rows, columns=None, empty="Nothing recorded yet."):
    if not rows:
        st.info(empty)
        return
    df = pd.DataFrame(rows)
    if columns:
        df = df[columns]
    st.dataframe(df, width="stretch", hide_index=True)


def current_student():
    sid = st.session_state.get("sel_student")
    return db.get_student(sid) if sid else None


def need_student():
    s = current_student()
    if not s:
        st.info("👈 No student selected. Add a student in **Student Management**, or pick one in the sidebar.")
        st.stop()
    st.caption(f"Student: **{s['full_name']}** · Status: **{s['current_status']}**")
    return s


# ------------------------------------------------------------------ pages
def page_dashboard():
    st.header("📊 Dashboard")
    k = db.dashboard_kpis()
    if k["students"] == 0:
        st.info("The database is empty. Run `python scripts/init_db.py` for demo data, or add students in Student Management.")
        return
    c = st.columns(6)
    c[0].metric("Students", k["students"])
    c[1].metric("Semesters recorded", k["semesters"])
    c[2].metric("Avg latest CGPA", k["avg_latest_cgpa"] if k["avg_latest_cgpa"] is not None else "n/a")
    c[3].metric("Seminars", k["seminars"])
    c[4].metric("Activities", k["activities"])
    c[5].metric("Gap years", k["gap_years"])
    a, b, d = st.columns(3)
    with a:
        st.subheader("Students by status")
        st.plotly_chart(px.bar(pd.DataFrame(k["by_status"]), x="current_status", y="n", labels={"current_status": "Status", "n": "Students"}),
                        width="stretch")
    with b:
        st.subheader("Activities by category")
        st.plotly_chart(px.bar(pd.DataFrame(k["by_category"]), x="category_name", y="n", labels={"category_name": "Category", "n": "Activities"}),
                        width="stretch")
    with d:
        st.subheader("Average SGPA by semester")
        if k["avg_sgpa_by_sem"]:
            fig = px.line(pd.DataFrame(k["avg_sgpa_by_sem"]), x="semester_no", y="avg_sgpa", markers=True,
                          labels={"semester_no": "Semester", "avg_sgpa": "Average SGPA"})
            fig.update_yaxes(range=[0, 10])
            fig.update_xaxes(dtick=1)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No SGPA values recorded yet.")


def page_students():
    st.header("👤 Student Management")
    term = st.text_input("🔍 Search by full or partial name (not case-sensitive)", key="stu_search")
    rows = db.search_students(term)
    table(rows, ["student_id", "full_name", "email", "current_status"],
          "No students match your search." if term else "No students yet. Use the Add tab.")
    t_add, t_edit = st.tabs(["➕ Add student", "✏️ Edit / delete"])
    with t_add:
        with st.form("add_student", clear_on_submit=True):
            name = st.text_input("Full name *")
            email = st.text_input("Email (optional, must be unique)")
            status = st.selectbox("Current status", v.STATUSES)
            if st.form_submit_button("Add student"):
                save(lambda: db.insert("student", v.student(name, email, status)), f"student '{name.strip()}' added.")
    with t_edit:
        if not rows:
            st.info("Nothing to edit.")
            return
        labels = {r["student_id"]: f"{r['full_name']} (ID {r['student_id']})" for r in rows}
        sid = st.selectbox("Choose student", list(labels), format_func=labels.get, key="stu_edit_pick")
        r = next(x for x in rows if x["student_id"] == sid)
        with st.form(f"edit_student_{sid}"):
            name = st.text_input("Full name *", r["full_name"])
            email = st.text_input("Email", r["email"] or "")
            status = st.selectbox("Current status", v.STATUSES, index=v.STATUSES.index(r["current_status"]))
            if st.form_submit_button("Save changes"):
                save(lambda: db.update("student", sid, v.student(name, email, status)), "student updated.")
        st.warning("Deleting a student also deletes their education, semesters, marks, gap years, seminars and activities.")
        confirm_delete(f"'{r['full_name']}' and ALL their records", f"del_stu_{sid}", lambda: db.delete("student", sid), "student deleted.")


def page_education():
    st.header("🎓 Education Records")
    s = need_student()
    rows = db.list_education(s["student_id"])
    table(rows, ["education_id", "level", "board", "completion_year", "percentage"], "No Secondary / Higher Secondary records yet.")
    t_add, t_edit = st.tabs(["➕ Add", "✏️ Edit / delete"])
    with t_add:
        have = {r["level"] for r in rows}
        free = [x for x in v.LEVELS if x not in have]
        if not free:
            st.success("Both levels are already recorded. Use the Edit tab to change them.")
        else:
            with st.form("add_edu", clear_on_submit=True):
                level = st.selectbox("Level", free)
                board = st.text_input("Board *")
                year = st.number_input("Completion year", 1950, 2100, 2020, step=1)
                pct = st.number_input("Percentage", 0.0, 100.0, 75.0, step=0.1)
                if st.form_submit_button("Add record"):
                    save(lambda: db.insert("education_record", v.education(s["student_id"], level, board, year, pct)), f"{level} record added.")
    with t_edit:
        if not rows:
            st.info("Nothing to edit.")
            return
        eid = st.selectbox("Record", [r["education_id"] for r in rows],
                           format_func=lambda i: next(r["level"] for r in rows if r["education_id"] == i), key="edu_pick")
        r = next(x for x in rows if x["education_id"] == eid)
        with st.form(f"edit_edu_{eid}"):
            st.text_input("Level (fixed)", r["level"], disabled=True)
            board = st.text_input("Board *", r["board"])
            year = st.number_input("Completion year", 1950, 2100, int(r["completion_year"]), step=1)
            pct = st.number_input("Percentage", 0.0, 100.0, float(r["percentage"]), step=0.1)
            if st.form_submit_button("Save changes"):
                save(lambda: db.update("education_record", eid, v.education(s["student_id"], r["level"], board, year, pct)), "education record updated.")
        confirm_delete(f"the {r['level']} record", f"del_edu_{eid}", lambda: db.delete("education_record", eid), "education record deleted.")


def page_semesters():
    st.header("📚 Semester Records")
    s = need_student()
    rows = db.list_semesters(s["student_id"])
    table(rows, ["semester_id", "semester_no", "academic_year", "sgpa", "cgpa"], "No semesters recorded yet.")
    st.caption("Leave SGPA/CGPA blank if the result has not been declared. Blank is stored as NULL, never as 0.")
    t_add, t_edit = st.tabs(["➕ Add semester", "✏️ Edit / delete"])
    with t_add:
        used = {r["semester_no"] for r in rows}
        with st.form("add_sem", clear_on_submit=True):
            c = st.columns(4)
            no = c[0].number_input("Semester no.", 1, 12, min([n for n in range(1, 13) if n not in used] or [1]), step=1)
            ay = c[1].text_input("Academic year (e.g. 2023-24)")
            sg = c[2].text_input("SGPA (0-10)")
            cg = c[3].text_input("CGPA (0-10)")
            if st.form_submit_button("Add semester"):
                save(lambda: db.insert("semester", v.semester(s["student_id"], no, ay.strip(), blank(sg), blank(cg))), f"semester {no} added.")
    with t_edit:
        if not rows:
            st.info("Nothing to edit.")
            return
        sid = st.selectbox("Semester", [r["semester_id"] for r in rows],
                           format_func=lambda i: "Semester " + str(next(r["semester_no"] for r in rows if r["semester_id"] == i)), key="sem_pick")
        r = next(x for x in rows if x["semester_id"] == sid)
        with st.form(f"edit_sem_{sid}"):
            c = st.columns(4)
            no = c[0].number_input("Semester no.", 1, 12, int(r["semester_no"]), step=1)
            ay = c[1].text_input("Academic year", r["academic_year"])
            sg = c[2].text_input("SGPA", "" if r["sgpa"] is None else str(r["sgpa"]))
            cg = c[3].text_input("CGPA", "" if r["cgpa"] is None else str(r["cgpa"]))
            if st.form_submit_button("Save changes"):
                save(lambda: db.update("semester", sid, v.semester(s["student_id"], no, ay.strip(), blank(sg), blank(cg))), "semester updated.")
        st.warning("Deleting a semester also deletes its subject marks.")
        confirm_delete(f"semester {r['semester_no']} and its marks", f"del_sem_{sid}", lambda: db.delete("semester", sid), "semester deleted.")


def page_marks():
    st.header("🧪 Subject and Lab Marks")
    t_marks, t_sub = st.tabs(["Marks for selected student", "Subject catalogue"])
    subjects = db.list_subjects()
    with t_sub:
        table(subjects, ["subject_id", "subject_code", "subject_name", "subject_type"], "No subjects yet. Add one below.")
        with st.form("add_subject", clear_on_submit=True):
            c = st.columns(3)
            code, name = c[0].text_input("Code *"), c[1].text_input("Name *")
            typ = c[2].selectbox("Type", v.SUBJECT_TYPES)
            if st.form_submit_button("Add subject"):
                save(lambda: db.insert("subject", v.subject(code, name, typ)), "subject added.")
        if subjects:
            sid = st.selectbox("Edit / delete subject", [x["subject_id"] for x in subjects],
                               format_func=lambda i: next(f"{x['subject_code']} - {x['subject_name']}" for x in subjects if x["subject_id"] == i), key="subj_pick")
            r = next(x for x in subjects if x["subject_id"] == sid)
            with st.form(f"edit_subject_{sid}"):
                c = st.columns(3)
                code, name = c[0].text_input("Code", r["subject_code"]), c[1].text_input("Name", r["subject_name"])
                typ = c[2].selectbox("Type", v.SUBJECT_TYPES, index=v.SUBJECT_TYPES.index(r["subject_type"]))
                if st.form_submit_button("Save subject"):
                    save(lambda: db.update("subject", sid, v.subject(code, name, typ)), "subject updated.")
            st.caption("A subject that already has marks cannot be deleted (ON DELETE RESTRICT).")
            confirm_delete(f"subject {r['subject_code']}", f"del_subj_{sid}", lambda: db.delete("subject", sid), "subject deleted.")
    with t_marks:
        s = need_student()
        sems = db.list_semesters(s["student_id"])
        if not sems or not subjects:
            st.info("Marks need at least one semester (for this student) and one subject.")
            return
        semlabel = {x["semester_id"]: f"Semester {x['semester_no']} ({x['academic_year']})" for x in sems}
        pick = st.selectbox("Show semester", [0] + list(semlabel), format_func=lambda i: "All semesters" if i == 0 else semlabel[i], key="marks_sem_filter")
        report = db.marks_report(s["student_id"], pick or None)
        table(report, ["mark_id", "semester_no", "subject_code", "subject_name", "subject_type", "theory_marks", "lab_marks"],
              "No marks recorded for this selection.")
        a, e = st.tabs(["➕ Add marks", "✏️ Edit / delete"])
        with a:
            with st.form("add_marks", clear_on_submit=True):
                c = st.columns(4)
                sem_id = c[0].selectbox("Semester", list(semlabel), format_func=semlabel.get)
                sub = c[1].selectbox("Subject", [x["subject_id"] for x in subjects],
                                     format_func=lambda i: next(f"{x['subject_code']} ({x['subject_type']})" for x in subjects if x["subject_id"] == i))
                th, lab = c[2].text_input("Theory marks (0-100)"), c[3].text_input("Lab marks (0-100)")
                st.caption("Leave a box empty when it does not apply. Empty = NULL, not 0.")
                if st.form_submit_button("Add marks"):
                    stype = next(x["subject_type"] for x in subjects if x["subject_id"] == sub)
                    save(lambda: db.insert("semester_marks", v.marks(sem_id, sub, blank(th), blank(lab), stype)), "marks added.")
        with e:
            if not report:
                st.info("Nothing to edit.")
            else:
                mid = st.selectbox("Record", [x["mark_id"] for x in report],
                                   format_func=lambda i: next(f"Sem {x['semester_no']} - {x['subject_code']}" for x in report if x["mark_id"] == i), key="marks_pick")
                r = next(x for x in report if x["mark_id"] == mid)
                with st.form(f"edit_marks_{mid}"):
                    c = st.columns(2)
                    th = c[0].text_input("Theory marks", "" if r["theory_marks"] is None else str(r["theory_marks"]))
                    lab = c[1].text_input("Lab marks", "" if r["lab_marks"] is None else str(r["lab_marks"]))
                    if st.form_submit_button("Save marks"):
                        save(lambda: db.update("semester_marks", mid, v.marks(r["semester_id"], r["subject_id"], blank(th), blank(lab), r["subject_type"])), "marks updated.")
                confirm_delete("this marks record", f"del_marks_{mid}", lambda: db.delete("semester_marks", mid), "marks deleted.")


def progression_figure(prog):
    ser = prog["series"]
    xs = [p["semester_no"] for p in ser]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=[p["sgpa"] for p in ser], mode="lines+markers", name="SGPA",
                             line=dict(color="#1f77b4", width=3), connectgaps=False,
                             hovertemplate="Semester %{x}<br>SGPA %{y}<extra></extra>"))
    fig.add_trace(go.Scatter(x=xs, y=[p["cgpa"] for p in ser], mode="lines+markers", name="CGPA",
                             line=dict(color="#ff7f0e", width=3, dash="dash"), connectgaps=False,
                             hovertemplate="Semester %{x}<br>CGPA %{y}<extra></extra>"))
    for g in prog["gaps"]:
        if g["x0"] is not None:
            label = f"Gap year {g['start_year']}" + (f"–{g['end_year']}" if g["end_year"] != g["start_year"] else "")
            fig.add_vrect(x0=g["x0"], x1=g["x1"], fillcolor="orange", opacity=0.25, line_width=0,
                          annotation_text=label, annotation_position="top left")
    for n in prog["missing"]:
        fig.add_vline(x=n, line_dash="dot", line_color="grey", annotation_text="No record", annotation_position="bottom")
    fig.update_layout(xaxis=dict(title="Semester", tickmode="linear", dtick=1), yaxis=dict(title="Grade points", range=[0, 10]),
                      legend=dict(orientation="h", y=1.1), margin=dict(t=40), height=430)
    return fig


def page_progression():
    st.header("📈 Academic Progression")
    s = need_student()
    sems, gaps = db.progression_rows(s["student_id"]), db.list_gaps(s["student_id"])
    prog = progression.build_progression(sems, gaps)
    if not prog["series"]:
        st.info("No semesters recorded for this student, so there is nothing to plot.")
    else:
        st.plotly_chart(progression_figure(prog), width="stretch")
        st.caption("🟧 Orange band = a gap year explicitly recorded in the database.  ┆ Grey dotted line = a semester with no record (NOT a recorded gap).  "
                   "Lines break where a value is missing; nothing is estimated.")
        if prog["missing"]:
            st.warning("Semesters with no record and no recorded gap year: " + ", ".join(map(str, prog["missing"])))
        no_grade = [p["semester_no"] for p in prog["series"] if p["recorded"] and (p["sgpa"] is None or p["cgpa"] is None)]
        if no_grade:
            st.info("Semesters recorded but with no declared SGPA/CGPA yet: " + ", ".join(map(str, no_grade)))
    if gaps:
        st.subheader("Recorded gap years")
        table(gaps, ["start_year", "end_year", "reason"])
    st.subheader("Semester-wise theory and lab marks")
    report = db.marks_report(s["student_id"])
    if not report:
        st.info("No marks recorded.")
    for no in sorted({r["semester_no"] for r in report}):
        part = [r for r in report if r["semester_no"] == no]
        sem = next(x for x in sems if x["semester_no"] == no)
        with st.expander(f"Semester {no} ({sem['academic_year']}) — SGPA {sem['sgpa'] if sem['sgpa'] is not None else 'n/a'}, "
                         f"CGPA {sem['cgpa'] if sem['cgpa'] is not None else 'n/a'}", expanded=(no == max(r['semester_no'] for r in report))):
            table(part, ["subject_code", "subject_name", "subject_type", "theory_marks", "lab_marks"])


def page_gaps():
    st.header("⏳ Gap-Year Timeline")
    s = need_student()
    gaps, sems = db.list_gaps(s["student_id"]), db.list_semesters(s["student_id"])
    rows = [dict(Item=f"Semester {x['semester_no']}", Kind="Semester", Start=datetime(int(x["academic_year"][:4]), 7, 1),
                 End=datetime(int(x["academic_year"][:4]) + 1, 6, 30)) for x in sems]
    rows += [dict(Item=f"Gap {g['start_year']}–{g['end_year']}", Kind="Recorded gap year", Start=datetime(g["start_year"], 7, 1),
                  End=datetime(g["end_year"] + 1, 6, 30)) for g in gaps]
    if rows:
        fig = px.timeline(pd.DataFrame(rows), x_start="Start", x_end="End", y="Item", color="Kind",
                          color_discrete_map={"Semester": "#1f77b4", "Recorded gap year": "orange"})
        fig.update_yaxes(autorange="reversed", title="")
        fig.update_layout(height=max(250, 40 * len(rows) + 120))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No semesters or gap years to show.")
    table(gaps, ["gap_id", "start_year", "end_year", "reason"], "No gap years recorded for this student.")
    t_add, t_edit = st.tabs(["➕ Record gap year", "✏️ Edit / delete"])
    with t_add:
        with st.form("add_gap", clear_on_submit=True):
            c = st.columns(2)
            sy, ey = c[0].number_input("Start year", 1950, 2100, date.today().year, step=1), c[1].number_input("End year", 1950, 2100, date.today().year, step=1)
            reason = st.text_input("Reason")
            if st.form_submit_button("Record gap year"):
                save(lambda: db.insert("gap_year", v.gap(s["student_id"], sy, ey, reason)), "gap year recorded.")
    with t_edit:
        if not gaps:
            st.info("Nothing to edit.")
            return
        gid = st.selectbox("Gap year", [g["gap_id"] for g in gaps],
                           format_func=lambda i: next(f"{g['start_year']}–{g['end_year']}" for g in gaps if g["gap_id"] == i), key="gap_pick")
        r = next(g for g in gaps if g["gap_id"] == gid)
        with st.form(f"edit_gap_{gid}"):
            c = st.columns(2)
            sy, ey = c[0].number_input("Start year", 1950, 2100, int(r["start_year"]), step=1), c[1].number_input("End year", 1950, 2100, int(r["end_year"]), step=1)
            reason = st.text_input("Reason", r["reason"] or "")
            if st.form_submit_button("Save changes"):
                save(lambda: db.update("gap_year", gid, v.gap(s["student_id"], sy, ey, reason)), "gap year updated.")
        confirm_delete("this gap year", f"del_gap_{gid}", lambda: db.delete("gap_year", gid), "gap year deleted.")


def page_seminars():
    st.header("🎤 Seminars")
    s = need_student()
    tabs = st.tabs(["All", "Internal", "External", "➕ Add", "✏️ Edit / delete"])
    for tab, kind in zip(tabs[:3], [None, "Internal", "External"]):
        with tab:
            table(db.list_seminars(s["student_id"], kind), ["title", "seminar_type", "event_date"] if kind is None else ["title", "event_date"],
                  f"No {kind.lower() + ' ' if kind else ''}seminars recorded.")
    with tabs[3]:
        with st.form("add_sem_form", clear_on_submit=True):
            title = st.text_input("Title *")
            c = st.columns(2)
            typ, d = c[0].selectbox("Type", v.SEMINAR_TYPES), c[1].date_input("Event date", date.today(), min_value=DATE_MIN, max_value=DATE_MAX)
            if st.form_submit_button("Add seminar"):
                save(lambda: db.insert("seminar", v.seminar(s["student_id"], title, typ, d)), "seminar added.")
    with tabs[4]:
        rows = db.list_seminars(s["student_id"])
        if not rows:
            st.info("Nothing to edit.")
            return
        sid = st.selectbox("Seminar", [r["seminar_id"] for r in rows], format_func=lambda i: next(r["title"] for r in rows if r["seminar_id"] == i), key="seminar_pick")
        r = next(x for x in rows if x["seminar_id"] == sid)
        with st.form(f"edit_seminar_{sid}"):
            title = st.text_input("Title", r["title"])
            c = st.columns(2)
            typ = c[0].selectbox("Type", v.SEMINAR_TYPES, index=v.SEMINAR_TYPES.index(r["seminar_type"]))
            d = c[1].date_input("Event date", r["event_date"], min_value=DATE_MIN, max_value=DATE_MAX)
            if st.form_submit_button("Save changes"):
                save(lambda: db.update("seminar", sid, v.seminar(s["student_id"], title, typ, d)), "seminar updated.")
        confirm_delete("this seminar", f"del_seminar_{sid}", lambda: db.delete("seminar", sid), "seminar deleted.")


def page_activities():
    st.header("🏅 Activities and Portfolio")
    s = need_student()
    cats = db.list_categories()
    names = [c["category_name"] for c in cats]
    tabs = st.tabs(["All"] + names + ["➕ Add", "✏️ Edit / delete", "📁 Academic records"])
    with tabs[0]:
        table(db.list_activities(s["student_id"]), ["category_name", "title", "activity_date"], "No activities recorded.")
    for tab, name in zip(tabs[1:1 + len(names)], names):
        with tab:
            table(db.list_activities(s["student_id"], name), ["title", "activity_date"], f"No {name} activities recorded.")
    k = 1 + len(names)
    with tabs[k]:
        with st.form("add_act", clear_on_submit=True):
            c = st.columns(3)
            cat = c[0].selectbox("Category", [x["category_id"] for x in cats], format_func=lambda i: next(x["category_name"] for x in cats if x["category_id"] == i))
            title = c[1].text_input("Title *")
            d = c[2].date_input("Date", date.today(), min_value=DATE_MIN, max_value=DATE_MAX)
            if st.form_submit_button("Add activity"):
                save(lambda: db.insert("activity", v.activity(s["student_id"], cat, title, d)), "activity added.")
    with tabs[k + 1]:
        rows = db.list_activities(s["student_id"])
        if not rows:
            st.info("Nothing to edit.")
        else:
            aid = st.selectbox("Activity", [r["activity_id"] for r in rows], format_func=lambda i: next(r["title"] for r in rows if r["activity_id"] == i), key="act_pick")
            r = next(x for x in rows if x["activity_id"] == aid)
            with st.form(f"edit_act_{aid}"):
                c = st.columns(3)
                cat = c[0].selectbox("Category", [x["category_id"] for x in cats], index=[x["category_id"] for x in cats].index(r["category_id"]),
                                     format_func=lambda i: next(x["category_name"] for x in cats if x["category_id"] == i))
                title = c[1].text_input("Title", r["title"])
                d = c[2].date_input("Date", r["activity_date"], min_value=DATE_MIN, max_value=DATE_MAX)
                if st.form_submit_button("Save changes"):
                    save(lambda: db.update("activity", aid, v.activity(s["student_id"], cat, title, d)), "activity updated.")
            confirm_delete("this activity", f"del_act_{aid}", lambda: db.delete("activity", aid), "activity deleted.")
    with tabs[k + 2]:
        st.subheader(f"Current status: {s['current_status']}")
        edu = db.list_education(s["student_id"])
        a, b = st.columns(2)
        for col, level in zip((a, b), v.LEVELS):
            with col:
                st.markdown(f"**{level}**")
                table([r for r in edu if r["level"] == level], ["board", "completion_year", "percentage"], f"No {level} record.")
        st.markdown("**All semesters (SGPA / CGPA)**")
        table(db.list_semesters(s["student_id"]), ["semester_no", "academic_year", "sgpa", "cgpa"], "No semesters.")
        st.markdown("**All theory and lab marks**")
        table(db.marks_report(s["student_id"]), ["semester_no", "subject_code", "subject_name", "theory_marks", "lab_marks"], "No marks.")


def page_sync():
    st.header("🔄 Google Sheets Synchronization")
    st.write("PostgreSQL is the **source of truth**. The 9 managed worksheets "
             f"({', '.join(sync.MANAGED)}) are fully refreshed from the database; other worksheets are never touched.")
    ok = config.sheets_configured()
    st.markdown(f"**Google configuration:** {'✅ credentials file and spreadsheet ID found' if ok else '❌ not configured (see README step 6 and .env.example)'}")
    w = db.worker_status()
    if w and w["alive"]:
        st.success("🟢 Background worker is running: direct database changes are synced automatically.")
    else:
        st.error("🔴 Background worker is NOT running. Changes made directly in PostgreSQL will not reach Google Sheets "
                 "until you start it:  `python sync_worker.py`  (in a second terminal). Changes made in this GUI still sync.")
    if w and w["last_sync_at"]:
        st.caption(f"Worker's last sync: {w['last_sync_at']:%Y-%m-%d %H:%M:%S} - {w['last_result']}" + (f" - {w['last_error']}" if w["last_error"] else ""))
    two_way = config.two_way_enabled()
    st.markdown("**Two-way sync:** " + ("✅ ON - valid edits typed into the sheet are saved to the database."
                if two_way else "⛔ OFF (TWO_WAY_SYNC=0) - the sheet is overwritten by the database."))
    if two_way:
        st.caption("Rules: edit existing cells or type a NEW row with an EMPTY ID cell. Every edit is validated exactly like the GUI "
                   "(SGPA 0-10, marks 0-100, dates as YYYY-MM-DD, ...). Invalid edits are reverted and explained in the Sync_Log worksheet. "
                   "ID columns, Activity_Categories and Sync_Log are read-only; deleting a sheet row does not delete the record. "
                   f"The worker looks for sheet edits every {config.SHEET_POLL_SECONDS:g} s; Sync Now and every GUI save check immediately.")
    st.subheader("Last GUI-triggered sync")
    ls = st.session_state.get("last_sync")
    if not ls:
        st.info("No sync has been run from this session yet.")
    else:
        (st.success if ls["ok"] else st.warning)(f"{ls['when']:%H:%M:%S} - " + ("success" if ls["ok"] else f"failed: {ls['error']}"))
        if ls["tables"]:
            st.dataframe(pd.DataFrame(ls["tables"].items(), columns=["Worksheet", "Result"]), hide_index=True, width="stretch")
    if ls and (ls.get("applied") or ls.get("rejected")):
        st.write(f"Sheet edits in that sync: **{ls['applied']} saved**, **{len(ls['rejected'])} rejected**.")
    st.subheader("Recent edits made in Google Sheets")
    try:
        log_rows = sync.recent_log(15)
    except psycopg2.Error:
        log_rows = []
    if log_rows:
        st.dataframe(pd.DataFrame(log_rows)[["logged_at", "worksheet", "sheet_row", "record_id", "outcome", "message"]], hide_index=True, width="stretch")
    else:
        st.info("No edits from Google Sheets yet. Try changing a name in the Students worksheet, then click Sync Now.")
    if st.button("🔄 Sync Now", type="primary"):
        run_sync()
        st.rerun()
    st.caption("Database save and Sheets sync are separate steps: a green 'Saved in the database' message with a sync warning means your data is safe; only the spreadsheet copy is behind.")


# ------------------------------------------------------------------ main
def main():
    try:
        db.query("SELECT 1")
        db.query("SELECT 1 FROM student LIMIT 1")
    except psycopg2.OperationalError:
        st.error("Cannot connect to PostgreSQL. Start the PostgreSQL service and check DB_* values in your .env file.")
        st.stop()
    except psycopg2.Error:
        st.error("Database is reachable but the tables are missing. Run:  python scripts/init_db.py")
        st.stop()
    with st.sidebar:
        st.title("🎓 StudentTrack")
        page = st.radio("Navigate", PAGES, label_visibility="collapsed")
        st.divider()
        q = st.text_input("🔍 Find student", key="sidebar_search", placeholder="type part of a name")
        found = db.search_students(q)
        if found:
            ids = [r["student_id"] for r in found]
            if st.session_state.get("sel_student") not in ids:
                st.session_state["sel_student"] = ids[0]
            st.selectbox("Selected student", ids, key="sel_student", format_func=lambda i: next(r["full_name"] for r in found if r["student_id"] == i))
        else:
            st.session_state["sel_student"] = None
            st.caption("No student found.")
        st.divider()
        w = db.worker_status()
        st.caption("Sync worker: " + ("🟢 running" if w and w["alive"] else "🔴 not running"))
    show_flash()
    {"Dashboard": page_dashboard, "Student Management": page_students, "Education Records": page_education,
     "Semester Records": page_semesters, "Subject and Lab Marks": page_marks, "Academic Progression": page_progression,
     "Gap-Year Timeline": page_gaps, "Seminars": page_seminars, "Activities and Portfolio": page_activities,
     "Google Sheets Synchronization": page_sync}[page]()


main()
