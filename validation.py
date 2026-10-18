"""Input validation done BEFORE any database write (the DB constraints are the second line of defence)."""
import re
from datetime import date

STATUSES = ["Studying", "Graduated", "Discontinued"]
LEVELS = ["Secondary", "Higher Secondary"]
SUBJECT_TYPES = ["Theory", "Lab", "Both"]
SEMINAR_TYPES = ["Internal", "External"]
CATEGORIES = ["Technical", "Co-curricular", "Extra-curricular"]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ValidationError(Exception):
    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [errors]
        super().__init__("; ".join(self.errors))


def _text(v):
    v = (v or "").strip()
    return v or None


def _range(name, v, lo, hi, errs, required=False):
    if v is None:
        if required:
            errs.append(f"{name} is required.")
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        errs.append(f"{name} must be a number.")
        return None
    if not lo <= v <= hi:
        errs.append(f"{name} must be between {lo} and {hi}.")
    return v


def student(full_name, email, status):
    errs = []
    full_name, email = _text(full_name), _text(email)
    if not full_name:
        errs.append("Full name is required.")
    elif len(full_name) > 100:
        errs.append("Full name is too long (max 100).")
    if email and not _EMAIL.match(email):
        errs.append("Email format is invalid.")
    if status not in STATUSES:
        errs.append("Status must be one of: " + ", ".join(STATUSES))
    if errs:
        raise ValidationError(errs)
    return {"full_name": full_name, "email": email, "current_status": status}


def education(student_id, level, board, year, pct):
    errs = []
    if not student_id:
        errs.append("Select a student.")
    if level not in LEVELS:
        errs.append("Level must be Secondary or Higher Secondary.")
    if not _text(board):
        errs.append("Board is required.")
    _range("Completion year", year, 1950, 2100, errs, True)
    pct = _range("Percentage", pct, 0, 100, errs, True)
    if errs:
        raise ValidationError(errs)
    return {"student_id": student_id, "level": level, "board": _text(board), "completion_year": int(year), "percentage": pct}


def semester(student_id, no, academic_year, sgpa, cgpa):
    errs = []
    if not student_id:
        errs.append("Select a student.")
    _range("Semester number", no, 1, 12, errs, True)
    if not re.fullmatch(r"\d{4}-\d{2}", academic_year or ""):
        errs.append("Academic year must look like 2023-24.")
    else:
        a, b = int(academic_year[:4]), int(academic_year[5:])
        if (a + 1) % 100 != b:
            errs.append("Academic year end must be start year + 1 (e.g. 2023-24).")
    sgpa = _range("SGPA", sgpa, 0, 10, errs)
    cgpa = _range("CGPA", cgpa, 0, 10, errs)
    if errs:
        raise ValidationError(errs)
    return {"student_id": student_id, "semester_no": int(no), "academic_year": academic_year, "sgpa": sgpa, "cgpa": cgpa}


def subject(code, name, stype):
    errs = []
    if not _text(code):
        errs.append("Subject code is required.")
    if not _text(name):
        errs.append("Subject name is required.")
    if stype not in SUBJECT_TYPES:
        errs.append("Subject type must be Theory, Lab or Both.")
    if errs:
        raise ValidationError(errs)
    return {"subject_code": _text(code).upper(), "subject_name": _text(name), "subject_type": stype}


def marks(semester_id, subject_id, theory, lab, subject_type=None):
    errs = []
    if not semester_id:
        errs.append("Select a semester.")
    if not subject_id:
        errs.append("Select a subject.")
    theory = _range("Theory marks", theory, 0, 100, errs)
    lab = _range("Lab marks", lab, 0, 100, errs)
    if subject_type == "Theory" and lab is not None:
        errs.append("This is a Theory-only subject: leave lab marks empty.")
    if subject_type == "Lab" and theory is not None:
        errs.append("This is a Lab-only subject: leave theory marks empty.")
    if errs:
        raise ValidationError(errs)
    return {"semester_id": semester_id, "subject_id": subject_id, "theory_marks": theory, "lab_marks": lab}


def gap(student_id, start, end, reason):
    errs = []
    if not student_id:
        errs.append("Select a student.")
    s = _range("Start year", start, 1950, 2100, errs, True)
    e = _range("End year", end, 1950, 2100, errs, True)
    if s is not None and e is not None and e < s:
        errs.append("End year cannot be before start year.")
    if errs:
        raise ValidationError(errs)
    return {"student_id": student_id, "start_year": int(s), "end_year": int(e), "reason": _text(reason)}


def seminar(student_id, title, stype, event_date):
    errs = []
    if not student_id:
        errs.append("Select a student.")
    if not _text(title):
        errs.append("Title is required.")
    if stype not in SEMINAR_TYPES:
        errs.append("Seminar type must be Internal or External.")
    if not isinstance(event_date, date):
        errs.append("Event date is required.")
    if errs:
        raise ValidationError(errs)
    return {"student_id": student_id, "title": _text(title), "seminar_type": stype, "event_date": event_date}


def activity(student_id, category_id, title, activity_date):
    errs = []
    if not student_id:
        errs.append("Select a student.")
    if not category_id:
        errs.append("Select a category.")
    if not _text(title):
        errs.append("Title is required.")
    if not isinstance(activity_date, date):
        errs.append("Activity date is required.")
    if errs:
        raise ValidationError(errs)
    return {"student_id": student_id, "category_id": category_id, "title": _text(title), "activity_date": activity_date}
