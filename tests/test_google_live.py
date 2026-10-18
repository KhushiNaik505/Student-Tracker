"""REAL Google test. Skipped unless you configure credentials. It only touches the 9 managed worksheets of the
spreadsheet in GOOGLE_SHEET_ID -- use a spare spreadsheet, not one with data you care about."""
import pytest

import config
import sync

pytestmark = pytest.mark.live_google


@pytest.mark.skipif(not config.sheets_configured(), reason="Google credentials not configured")
def test_real_google_sync_roundtrip(pg):
    r = sync.sync_all()
    assert r.ok, r.error
    again = sync.sync_all()
    assert again.ok and again.api_writes == 0
