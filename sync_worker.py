"""Background worker: LISTENs for PostgreSQL change notifications and re-syncs Google Sheets.
Run in its OWN terminal:   python sync_worker.py     (Ctrl+C to stop)

Flow: trigger -> NOTIFY (delivered only after COMMIT) -> worker wakes -> waits DEBOUNCE seconds for
further notifications (coalescing) -> re-reads the committed DB state -> refreshes the worksheets."""
import logging
import select
import threading
import time

import psycopg2

import config
import db
import sync

log = logging.getLogger("sync_worker")


class Worker:
    def __init__(self, spreadsheet_provider=None, debounce=None, heartbeat=None, retry_delay=15, poll_interval=None):
        self.provider = spreadsheet_provider
        self.debounce = config.DEBOUNCE_SECONDS if debounce is None else debounce
        self.heartbeat = config.HEARTBEAT_SECONDS if heartbeat is None else heartbeat
        self.retry_delay = retry_delay
        # how often to look for edits typed into the Google Sheet (Google cannot push to us, so we poll). 0 = never
        self.poll_interval = config.SHEET_POLL_SECONDS if poll_interval is None else poll_interval
        if not config.two_way_enabled():
            self.poll_interval = 0
        self.syncs = 0           # completed sync attempts (used by tests)
        self.reconnects = 0      # times the DB connection was lost and re-established
        self.last_result = None
        self.listening = threading.Event()

    def _sync(self, poll=False):
        r = self.last_result = sync.sync_all(self.provider)
        self.syncs += 1
        quiet = poll and r.ok and r.api_writes == 0 and not r.pulled_applied and not r.pulled_rejected
        if not quiet:
            sync.record_worker_state(result=r)
        if not r.ok:
            log.warning("sync failed: %s", r.error)
        elif quiet:
            log.debug("poll: sheet unchanged")
        else:
            log.info("sync ok: %s", r.tables)
            if r.pulled_applied:
                log.info("saved %d edit(s) made in Google Sheets", r.pulled_applied)
            for e in r.pulled_rejected:
                log.warning("rejected sheet edit in %s row %s: %s", e["worksheet"], e["row"], e["message"])
        return r.ok

    def run(self, stop: threading.Event):
        backoff = 1
        while not stop.is_set():
            conn = None
            try:
                conn = db.connect()
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(f"LISTEN {config.NOTIFY_CHANNEL}")
                self.listening.set()
                log.info("listening on '%s'", config.NOTIFY_CHANNEL)
                backoff = 1
                dirty, last_event, next_retry = True, time.monotonic() - self.debounce, 0  # initial catch-up sync
                last_beat = 0
                next_poll = time.monotonic() + self.poll_interval
                while not stop.is_set():
                    now = time.monotonic()
                    if now - last_beat >= self.heartbeat:
                        sync.record_worker_state()
                        last_beat = now
                    timeout = 0.5 if dirty else min(1.0, self.heartbeat)
                    if select.select([conn], [], [], timeout)[0]:
                        conn.poll()
                        if conn.notifies:
                            conn.notifies.clear()
                            dirty, last_event = True, time.monotonic()  # coalesce: just remember "something changed"
                    if self.poll_interval and not dirty and time.monotonic() >= next_poll:
                        next_poll = time.monotonic() + self.poll_interval
                        self._sync(poll=True)   # pulls validated sheet edits (if any), then pushes
                    if dirty and time.monotonic() - last_event >= self.debounce and time.monotonic() >= next_retry:
                        dirty = False  # clear BEFORE syncing so changes during the sync trigger another run
                        if not self._sync():
                            dirty, next_retry = True, time.monotonic() + self.retry_delay
                            last_event = time.monotonic() - self.debounce
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
                self.listening.clear()
                self.reconnects += 1
                log.warning("database connection lost (%s); reconnecting in %ss", type(exc).__name__, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not config.sheets_configured():
        log.warning("Google Sheets is NOT configured: the worker will run but every sync will fail until .env is set.")
    stop = threading.Event()
    print("StudentTrack sync worker running. Press Ctrl+C to stop.")
    try:
        Worker().run(stop)
    except KeyboardInterrupt:
        stop.set()
        print("stopped")


if __name__ == "__main__":
    main()
