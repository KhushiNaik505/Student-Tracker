"""In-memory stand-in for a gspread Spreadsheet (same method names we use). It lets us test OUR sync logic
offline. It is NOT a test of Google itself -- see tests/test_google_live.py for that."""
import uuid

import gspread


class FakeWorksheet:
    def __init__(self, sheet, title):
        self.sheet, self.title, self.values, self.formats = sheet, title, [], []

    def get_all_values(self):
        self.sheet.reads += 1
        return [[str(c) for c in r] for r in self.values]

    def clear(self):
        self.sheet.writes += 1
        self.values = []

    def update(self, values=None, range_name="A1", raw=True):
        self.sheet.writes += 1
        self.values = [list(r) for r in values]


    def format(self, range_name, fmt):   # cosmetic call: recorded, not counted as a data write
        self.formats.append((range_name, fmt))


class FakeSpreadsheet:
    def __init__(self, fail_with=None):
        self.sheets, self.reads, self.writes, self.fail_with = {}, 0, 0, fail_with
        self.id = uuid.uuid4().hex

    def worksheet(self, title):
        if self.fail_with:
            raise self.fail_with
        if title not in self.sheets:
            raise gspread.exceptions.WorksheetNotFound(title)
        return self.sheets[title]

    def add_worksheet(self, title, rows, cols):
        self.sheets[title] = FakeWorksheet(self, title)
        return self.sheets[title]

    def rows(self, title):
        return self.sheets[title].values[1:]


class FakeApiError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status})()
