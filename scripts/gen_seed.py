"""Developer helper that generates seed.sql (seed.sql itself is plain, readable SQL)."""
import os

subjects = [
    ("MA101", "Engineering Mathematics I", "Theory"), ("PH101", "Engineering Physics", "Both"),
    ("CS101", "Programming in C", "Both"), ("EC101", "Basic Electronics", "Both"),
    ("EC102", "Network Theory", "Theory"), ("MA201", "Engineering Mathematics II", "Theory"),
    ("EC201", "Digital Logic Design", "Both"), ("EC202", "Signals and Systems", "Theory"),
    ("EC203", "Analog Circuits", "Both"), ("CS201", "Data Structures", "Both"),
    ("EC301", "Microprocessors and Microcontrollers", "Both"), ("EC302", "Communication Systems", "Both"),
    ("EC303", "Control Systems", "Theory"), ("EC304", "Electromagnetic Theory", "Theory"),
    ("EC305", "VLSI Design", "Both"), ("LB101", "Workshop Practice", "Lab"),
]
S = {s[0]: s[2] for s in subjects}
plan = {1: ["MA101", "PH101", "CS101", "LB101"], 2: ["MA201", "EC101", "EC102", "CS101"],
        3: ["EC201", "EC202", "EC203", "CS201"], 4: ["EC301", "EC302", "EC303", "EC304"],
        5: ["EC305", "EC302", "EC301", "EC303"]}
# (name, email, status, education, semesters{no:(year,sgpa,cgpa,seed)}, gaps, seminars, activities)
students = [
    ("Aarav Sharma", "aarav.sharma@example.com", "Studying",
     [("Secondary", "CBSE", 2019, 91.40), ("Higher Secondary", "CBSE", 2021, 88.60)],
     {1: ("2021-22", 8.40, 8.40, 7), 2: ("2021-22", 8.72, 8.56, 3), 3: ("2022-23", 8.95, 8.69, 5),
      4: ("2022-23", 9.10, 8.79, 2), 5: ("2023-24", 9.20, 8.87, 9)},
     [], [("Embedded Systems Trends", "Internal", "2023-02-14"), ("IoT in Smart Agriculture", "External", "2023-09-21")],
     [("Technical", "Arduino Weather Station Project", "2022-11-05"), ("Technical", "Smart India Hackathon Finalist", "2023-08-18"),
      ("Co-curricular", "IEEE Student Chapter Volunteer", "2022-09-01"), ("Extra-curricular", "Inter-college Cricket Tournament", "2023-01-20")]),
    ("Priya Nair", "priya.nair@example.com", "Studying",
     [("Secondary", "Kerala State Board", 2018, 94.20), ("Higher Secondary", "Kerala State Board", 2020, 90.10)],
     {1: ("2020-21", 8.10, 8.10, 4), 2: ("2020-21", 8.35, 8.22, 6), 3: ("2022-23", 8.60, 8.35, 8), 4: ("2022-23", 8.80, 8.47, 1)},
     [(2021, 2021, "Medical leave - recovery from surgery")],
     [("Wireless Sensor Networks", "Internal", "2023-03-10"), ("5G Network Architecture", "External", "2023-10-05")],
     [("Technical", "PCB Design Workshop", "2023-01-12"), ("Co-curricular", "Technical Paper Presentation", "2023-04-02"),
      ("Extra-curricular", "Classical Dance Competition", "2022-12-15")]),
    ("Rohan Patel", "rohan.patel@example.com", "Studying",
     [("Secondary", "GSEB", 2018, 82.50), ("Higher Secondary", "GSEB", 2020, 79.80)],
     {1: ("2020-21", 7.20, 7.20, 10), 2: ("2020-21", 7.45, 7.33, 11), 4: ("2021-22", 7.90, 7.52, 12), 5: ("2022-23", None, None, 13)},
     [], [("Basics of Robotics", "Internal", "2022-02-18")],
     [("Technical", "Line-follower Robot Contest", "2021-10-09"), ("Extra-curricular", "NSS Blood Donation Camp", "2022-01-26")]),
    ("Meera Iyer", "meera.iyer@example.com", "Graduated",
     [("Secondary", "ICSE", 2014, 93.00), ("Higher Secondary", "ISC", 2016, 91.50)],
     {1: ("2016-17", 8.80, 8.80, 14), 2: ("2016-17", 9.00, 8.90, 15), 3: ("2017-18", 9.15, 8.98, 16)},
     [], [("Machine Learning for Signal Processing", "External", "2018-03-22"), ("Digital Image Processing", "Internal", "2017-09-14")],
     [("Technical", "Winner - National Robotics Challenge", "2018-02-11"), ("Co-curricular", "Technical Magazine Editor", "2017-08-01"),
      ("Extra-curricular", "University Choir Member", "2017-12-10")]),
    ("Karthik Reddy", "karthik.reddy@example.com", "Discontinued",
     [("Secondary", "State Board Telangana", 2017, 76.00), ("Higher Secondary", "Telangana Intermediate Board", 2019, 71.20)],
     {1: ("2019-20", 6.50, 6.50, 17), 2: ("2019-20", 6.20, 6.35, 18)},
     [(2020, 2021, "Family business responsibilities")], [], [("Extra-curricular", "College Football Team", "2019-12-03")]),
    ("Sneha Kulkarni", "sneha.kulkarni@example.com", "Studying",
     [("Secondary", "Maharashtra State Board", 2019, 89.20)],
     {1: ("2022-23", 8.00, 8.00, 19), 2: ("2022-23", 8.25, 8.13, 20), 3: ("2023-24", None, None, 21)},
     [], [("Cyber Security Awareness", "Internal", "2023-08-30")],
     [("Technical", "Python Automation Workshop", "2023-02-25"), ("Co-curricular", "Quiz Competition Runner-up", "2023-03-18")]),
]


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


def n(v):
    return "NULL" if v is None else str(v)


def marks(code, sem, seed):
    base = 55 + (seed * 7 + sem * 5 + len(code) * 3) % 38
    t = None if S[code] == "Lab" else base
    l = None if S[code] == "Theory" else min(100, base + 6 - (seed % 5))
    return t, l


def block(head, rows, tail):
    return head + "\n" + ",\n".join(rows) + "\n" + tail + "\n"


E = "JOIN student st ON lower(st.email) = lower(v.email)"
o = ["-- StudentTrack demo data (all fictional). Safe to run repeatedly: every INSERT uses ON CONFLICT DO NOTHING.",
     "-- Run AFTER schema.sql.  (Generated by scripts/gen_seed.py)\n"]
o.append(block("INSERT INTO subject (subject_code, subject_name, subject_type) VALUES",
               [f"  ({q(c)}, {q(nm)}, {q(t)})" for c, nm, t in subjects], "ON CONFLICT (subject_code) DO NOTHING;"))
o.append(block("INSERT INTO student (full_name, email, current_status) VALUES",
               [f"  ({q(s[0])}, {q(s[1])}, {q(s[2])})" for s in students],
               "ON CONFLICT (lower(email)) WHERE email IS NOT NULL DO NOTHING;"))
rows = [(s[1],) + e for s in students for e in s[3]]
o.append(block("INSERT INTO education_record (student_id, level, board, completion_year, percentage)\n"
               "SELECT st.student_id, v.level, v.board, v.yr, v.pct FROM (VALUES",
               [f"  ({q(r[0])}, {q(r[1])}, {q(r[2])}, {r[3]}, {r[4]})" for r in rows],
               f") AS v(email, level, board, yr, pct) {E}\nON CONFLICT (student_id, level) DO NOTHING;"))
rows = [(s[1], no) + v[:3] for s in students for no, v in s[4].items()]
o.append(block("INSERT INTO semester (student_id, semester_no, academic_year, sgpa, cgpa)\n"
               "SELECT st.student_id, v.sem_no, v.ay, v.sgpa, v.cgpa FROM (VALUES",
               [f"  ({q(r[0])}, {r[1]}, {q(r[2])}, {n(r[3])}::numeric, {n(r[4])}::numeric)" for r in rows],
               f") AS v(email, sem_no, ay, sgpa, cgpa) {E}\nON CONFLICT (student_id, semester_no) DO NOTHING;"))
rows = []
for s in students:
    for no, v in s[4].items():
        if v[1] is None:
            continue  # result not yet declared -> no marks yet
        for code in plan[no]:
            t, l = marks(code, no, v[3])
            rows.append((s[1], no, code, t, l))
o.append(block("INSERT INTO semester_marks (semester_id, subject_id, theory_marks, lab_marks)\n"
               "SELECT sm.semester_id, sb.subject_id, v.t, v.l FROM (VALUES",
               [f"  ({q(r[0])}, {r[1]}, {q(r[2])}, {n(r[3])}::numeric, {n(r[4])}::numeric)" for r in rows],
               f") AS v(email, sem_no, code, t, l)\n{E}\n"
               "JOIN semester sm ON sm.student_id = st.student_id AND sm.semester_no = v.sem_no\n"
               "JOIN subject sb ON sb.subject_code = v.code\nON CONFLICT (semester_id, subject_id) DO NOTHING;"))
rows = [(s[1],) + g for s in students for g in s[5]]
o.append(block("INSERT INTO gap_year (student_id, start_year, end_year, reason)\n"
               "SELECT st.student_id, v.sy, v.ey, v.reason FROM (VALUES",
               [f"  ({q(r[0])}, {r[1]}, {r[2]}, {q(r[3])})" for r in rows],
               f") AS v(email, sy, ey, reason) {E}\nON CONFLICT (student_id, start_year, end_year) DO NOTHING;"))
rows = [(s[1],) + x for s in students for x in s[6]]
o.append(block("INSERT INTO seminar (student_id, title, seminar_type, event_date)\n"
               "SELECT st.student_id, v.title, v.typ, v.d::date FROM (VALUES",
               [f"  ({q(r[0])}, {q(r[1])}, {q(r[2])}, {q(r[3])})" for r in rows],
               f") AS v(email, title, typ, d) {E}\nON CONFLICT (student_id, title, event_date) DO NOTHING;"))
rows = [(s[1],) + x for s in students for x in s[7]]
o.append(block("INSERT INTO activity (student_id, category_id, title, activity_date)\n"
               "SELECT st.student_id, c.category_id, v.title, v.d::date FROM (VALUES",
               [f"  ({q(r[0])}, {q(r[1])}, {q(r[2])}, {q(r[3])})" for r in rows],
               f") AS v(email, cat, title, d) {E}\nJOIN activity_category c ON c.category_name = v.cat\n"
               "ON CONFLICT (student_id, category_id, title, activity_date) DO NOTHING;"))
out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seed.sql")
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(o))
print("wrote", out)
