"""Pure (database-free) logic that prepares the SGPA/CGPA chart and gap-year indicators.
Kept separate so it can be unit-tested offline."""


def _start_year(academic_year):
    return int(academic_year[:4])


def build_progression(sem_rows, gap_rows):
    """
    sem_rows: dicts with semester_no, academic_year, sgpa, cgpa (any order, grades may be None)
    gap_rows: dicts with start_year, end_year, reason

    Returns:
      series  - one entry per semester number 1..max; unrecorded semesters have recorded=False and
                sgpa/cgpa = None (we NEVER invent values, so the line breaks there)
      gaps    - explicitly recorded gap years with chart position (x0..x1) when it can be placed
      missing - semester numbers with no record that are NOT explained by a recorded gap year
    """
    sems = sorted(sem_rows, key=lambda r: r["semester_no"])
    by_no = {r["semester_no"]: r for r in sems}
    top = max(by_no) if by_no else 0
    series = []
    for n in range(1, top + 1):
        r = by_no.get(n)
        series.append({"semester_no": n, "recorded": r is not None,
                       "academic_year": r["academic_year"] if r else None,
                       "sgpa": r["sgpa"] if r else None, "cgpa": r["cgpa"] if r else None})
    gaps = []
    for g in sorted(gap_rows, key=lambda r: r["start_year"]):
        before = [r["semester_no"] for r in sems if _start_year(r["academic_year"]) < g["start_year"]]
        after = [r["semester_no"] for r in sems if _start_year(r["academic_year"]) > g["end_year"]]
        b, a = (max(before) if before else None), (min(after) if after else None)
        if b is None and a is None:
            x0 = x1 = None
        else:
            x0 = b if b is not None else a - 1
            x1 = a if a is not None else b + 1
        gaps.append({"start_year": g["start_year"], "end_year": g["end_year"], "reason": g.get("reason"),
                     "x0": x0, "x1": x1})
    explained = set()
    for g in gaps:
        if g["x0"] is not None:
            explained |= {n for n in range(1, top + 1) if g["x0"] < n < g["x1"]}
    missing = [s["semester_no"] for s in series if not s["recorded"] and s["semester_no"] not in explained]
    return {"series": series, "gaps": gaps, "missing": missing}
