import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(root / "provider.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, secret BLOB NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'wan-video',
                    credential_kind TEXT NOT NULL DEFAULT 'access_token',
                    credit_profile TEXT NOT NULL DEFAULT 'shared',
                    enabled INTEGER NOT NULL DEFAULT 1, balance REAL,
                    video_balance REAL, image_balance REAL,
                    checked_at TEXT, video_checked_at TEXT, image_checked_at TEXT,
                    status TEXT NOT NULL DEFAULT 'unchecked',
                    auth_state TEXT NOT NULL DEFAULT 'unchecked',
                    auth_error TEXT NOT NULL DEFAULT '',
                    check_error TEXT NOT NULL DEFAULT '',
                    q4_trials_json TEXT NOT NULL DEFAULT '{}',
                    image_trials_json TEXT NOT NULL DEFAULT '{}',
                    claw_state_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, account_id TEXT, provider TEXT NOT NULL DEFAULT 'wan-video',
                    task_type TEXT NOT NULL DEFAULT 'video', model TEXT NOT NULL,
                    prompt TEXT NOT NULL, input_json TEXT NOT NULL,
                    status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                    upstream_id TEXT, result_url TEXT, result_path TEXT,
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS claims (
                    account_id TEXT NOT NULL, task_key TEXT NOT NULL,
                    period_key TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, task_key, period_key)
                );
                CREATE TABLE IF NOT EXISTS wan_experiences (
                    account_id TEXT NOT NULL, experience_id TEXT NOT NULL,
                    status TEXT NOT NULL, task_id TEXT, receipt_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, experience_id)
                );
            """)
            self._ensure_column(db, "accounts", "provider", "TEXT NOT NULL DEFAULT 'wan-video'")
            self._ensure_column(db, "accounts", "credential_kind", "TEXT NOT NULL DEFAULT 'access_token'")
            self._ensure_column(db, "accounts", "credit_profile", "TEXT NOT NULL DEFAULT 'shared'")
            self._ensure_column(db, "accounts", "video_balance", "REAL")
            self._ensure_column(db, "accounts", "image_balance", "REAL")
            self._ensure_column(db, "accounts", "video_checked_at", "TEXT")
            self._ensure_column(db, "accounts", "image_checked_at", "TEXT")
            self._ensure_column(db, "accounts", "auth_state", "TEXT NOT NULL DEFAULT 'unchecked'")
            self._ensure_column(db, "accounts", "auth_error", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(db, "accounts", "check_error", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(db, "accounts", "q4_trials_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "accounts", "image_trials_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "accounts", "claw_state_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "accounts", "wan_login_email", "BLOB")
            self._ensure_column(db, "accounts", "wan_state_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "tasks", "provider", "TEXT NOT NULL DEFAULT 'wan-video'")
            self._ensure_column(db, "tasks", "task_type", "TEXT NOT NULL DEFAULT 'video'")
            db.execute(
                "UPDATE accounts SET provider = 'vidu-cookie', credit_profile = 'video' "
                "WHERE provider IN ('q4-video', 'claw-video')"
            )
            db.execute(
                "UPDATE accounts SET provider = 'vidu-cookie', credit_profile = 'image' "
                "WHERE provider = 'image'"
            )
            db.execute(
                "UPDATE accounts SET video_balance = balance "
                "WHERE provider = 'vidu-cookie' AND credit_profile IN ('video', 'shared') "
                "AND video_balance IS NULL AND balance IS NOT NULL"
            )
            db.execute(
                "UPDATE accounts SET image_balance = balance "
                "WHERE provider = 'vidu-cookie' AND credit_profile IN ('image', 'shared') "
                "AND image_balance IS NULL AND balance IS NOT NULL"
            )

    @staticmethod
    def _ensure_column(db, table, column, definition):
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                yield self.db
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        with self.lock:
            row = self.db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def setting(self, key, fallback=None):
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else fallback

    def set_setting(self, key, value):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))

    def task(self, task_id):
        return self.one("SELECT * FROM tasks WHERE id = ?", (task_id,))

    def update_task(self, task_id, **values):
        allowed = {
            "account_id", "provider", "task_type", "model", "prompt", "input_json", "status", "progress",
            "upstream_id", "result_url", "result_path", "error",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"不允许更新任务字段: {', '.join(sorted(unknown))}")
        values["updated_at"] = now()
        with self.transaction() as db:
            db.execute(
                f"UPDATE tasks SET {', '.join(key + ' = ?' for key in values)} WHERE id = ?",
                (*values.values(), task_id),
            )
