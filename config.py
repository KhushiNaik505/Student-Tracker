"""Central configuration. Secrets come from environment variables (.env) or
.streamlit/secrets.toml -- never from source code."""
import os

try:  # optional: load a local .env file if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def get(key, default=None):
    """Environment variable first, then Streamlit secrets (if running in Streamlit)."""
    val = os.environ.get(key)
    if val not in (None, ""):
        return val
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return default


def db_params(dbname=None):
    """psycopg2.connect() keyword arguments."""
    return dict(
        host=get("DB_HOST", "localhost"),
        port=int(get("DB_PORT", "5432")),
        dbname=dbname or get("DB_NAME", "studenttrack"),
        user=get("DB_USER", "postgres"),
        password=get("DB_PASSWORD", ""),
        connect_timeout=20,
        sslmode=get("DB_SSLMODE", "prefer"),   # use "require" for hosted databases (Neon, Supabase)
    )


def sheets_credentials_file():
    return get("GOOGLE_SHEETS_CREDENTIALS_FILE")


def sheets_credentials_info():
    """Service-account key as a dict, taken from GOOGLE_SERVICE_ACCOUNT_JSON (for hosting without a key file)."""
    import json
    raw = get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def sheets_target():
    """(spreadsheet_id, spreadsheet_name) -- ID is preferred."""
    return get("GOOGLE_SHEET_ID"), get("GOOGLE_SHEET_NAME")


def sheets_configured():
    f = sheets_credentials_file()
    sid, name = sheets_target()
    have_key = bool((f and os.path.isfile(f)) or sheets_credentials_info())
    return bool(have_key and (sid or name))


def two_way_enabled():
    """Two-way sync (validated edits in Google Sheets -> database). Set TWO_WAY_SYNC=0 to disable."""
    return str(get("TWO_WAY_SYNC", "1")).strip().lower() not in ("0", "false", "no", "off")


NOTIFY_CHANNEL = "studenttrack_changes"
SHEET_POLL_SECONDS = float(get("SHEET_POLL_SECONDS", "30"))   # how often the worker looks for sheet edits (0 = never)
DEBOUNCE_SECONDS = float(get("SYNC_DEBOUNCE_SECONDS", "2"))
HEARTBEAT_SECONDS = 10
