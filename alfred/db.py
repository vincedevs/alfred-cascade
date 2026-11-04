"""Alfred's one SQLite database file: background jobs, the questions they ask you,
reminders, and a searchable history of past conversations and job results.

A job is written here when it starts and updated as it runs, so after a restart
Alfred knows which jobs to pick up again and which results it hasn't told you yet.
"""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY,
    request     TEXT    NOT NULL,           -- what you asked for, in your words
    status      TEXT    NOT NULL,           -- running | done | failed | stopped
    result      TEXT,                       -- the answer, or what went wrong
    session_id  TEXT,                       -- Claude session, used to resume after a restart
    created_at  REAL    NOT NULL,
    finished_at REAL,
    told_user   INTEGER NOT NULL DEFAULT 0  -- 1 once Alfred has said the result
);
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER NOT NULL,           -- the job asking
    kind        TEXT    NOT NULL,           -- approve (yes or no) | clarify (any answer)
    text        TEXT    NOT NULL,           -- what Alfred asks you, word for word
    status      TEXT    NOT NULL,           -- waiting | answered | expired
    answer      TEXT,
    created_at  REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY,
    text        TEXT    NOT NULL,           -- what to say, or the job to run
    due_at      REAL    NOT NULL,           -- next time it goes off (Unix time)
    repeat      TEXT    NOT NULL,           -- none | daily | weekdays | weekly
    run_job     INTEGER NOT NULL,           -- 1: start a background job instead of speaking
    status      TEXT    NOT NULL,           -- waiting | done | missed | cancelled
    unsaid      INTEGER NOT NULL DEFAULT 0, -- 1: went off, but Alfred hasn't said it yet
    created_at  REAL    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'reminder'  -- reminder | timer | alarm
);
-- Full-text search over what happened: job results and short conversation summaries.
CREATE VIRTUAL TABLE IF NOT EXISTS history USING fts5(kind, text, created_at UNINDEXED);
"""


@dataclass
class Task:
    id: int
    request: str
    status: str
    result: str | None
    session_id: str | None
    created_at: float
    finished_at: float | None
    told_user: bool


@dataclass
class Reminder:
    id: int
    text: str
    due_at: float
    repeat: str
    run_job: bool
    status: str
    unsaid: bool
    created_at: float
    kind: str = "reminder"  # reminder | timer | alarm


@dataclass
class Question:
    id: int
    task_id: int
    kind: str
    text: str
    status: str
    answer: str | None
    created_at: float


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._add_missing_columns()
        self._conn.commit()

    def _add_missing_columns(self) -> None:
        """Databases made by an older Alfred lack columns added since."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(reminders)")}
        if "kind" not in columns:
            self._conn.execute(
                "ALTER TABLE reminders ADD COLUMN kind TEXT NOT NULL DEFAULT 'reminder'"
            )

    def add_task(self, request: str) -> int:
        cursor = self._conn.execute(
            "INSERT INTO tasks (request, status, created_at) VALUES (?, 'running', ?)",
            (request, time.time()),
        )
        self._conn.commit()
        return cursor.lastrowid

    def set_session(self, task_id: int, session_id: str) -> None:
        self._conn.execute("UPDATE tasks SET session_id = ? WHERE id = ?", (session_id, task_id))
        self._conn.commit()

    def finish_task(self, task_id: int, status: str, result: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = ?, result = ?, finished_at = ? WHERE id = ?",
            (status, result, time.time(), task_id),
        )
        if status == "done":  # searchable later: "what did you find about ramen?"
            request = self.get_task(task_id).request
            self.add_history("job", f"You asked: {request}\nResult: {result}", commit=False)
        self._conn.commit()

    def mark_told(self, task_ids: list[int]) -> None:
        self._conn.executemany(
            "UPDATE tasks SET told_user = 1 WHERE id = ?", [(i,) for i in task_ids]
        )
        self._conn.commit()

    def get_task(self, task_id: int) -> Task | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _to_task(row) if row else None

    def recent_tasks(self, limit: int = 5) -> list[Task]:
        rows = self._conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        return [_to_task(row) for row in rows]

    def running_tasks(self) -> list[Task]:
        rows = self._conn.execute("SELECT * FROM tasks WHERE status = 'running' ORDER BY id")
        return [_to_task(row) for row in rows]

    def untold_results(self) -> list[Task]:
        """Finished jobs whose result Alfred hasn't said yet."""
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE status != 'running' AND told_user = 0 ORDER BY id"
        )
        return [_to_task(row) for row in rows]

    def add_question(self, task_id: int, kind: str, text: str) -> Question:
        cursor = self._conn.execute(
            "INSERT INTO questions (task_id, kind, text, status, created_at) "
            "VALUES (?, ?, ?, 'waiting', ?)",
            (task_id, kind, text, time.time()),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM questions WHERE id = ?", (cursor.lastrowid,))
        return Question(**dict(row.fetchone()))

    def close_question(self, question_id: int, status: str, answer: str | None = None) -> None:
        self._conn.execute(
            "UPDATE questions SET status = ?, answer = ? WHERE id = ?",
            (status, answer, question_id),
        )
        self._conn.commit()

    def waiting_questions(self) -> list[Question]:
        rows = self._conn.execute("SELECT * FROM questions WHERE status = 'waiting' ORDER BY id")
        return [Question(**dict(row)) for row in rows]

    def expire_waiting_questions(self) -> None:
        self._conn.execute("UPDATE questions SET status = 'expired' WHERE status = 'waiting'")
        self._conn.commit()

    # Reminders

    def add_reminder(
        self, text: str, due_at: float, repeat: str, run_job: bool, kind: str = "reminder"
    ) -> Reminder:
        cursor = self._conn.execute(
            "INSERT INTO reminders (text, due_at, repeat, run_job, status, created_at, kind) "
            "VALUES (?, ?, ?, ?, 'waiting', ?, ?)",
            (text, due_at, repeat, int(run_job), time.time(), kind),
        )
        self._conn.commit()
        return self.get_reminder(cursor.lastrowid)

    def get_reminder(self, reminder_id: int) -> Reminder | None:
        row = self._conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        row = row.fetchone()
        return _to_reminder(row) if row else None

    def reminders_with_status(self, *statuses: str) -> list[Reminder]:
        marks = ", ".join("?" * len(statuses))
        rows = self._conn.execute(
            f"SELECT * FROM reminders WHERE status IN ({marks}) ORDER BY due_at", statuses
        )
        return [_to_reminder(row) for row in rows]

    def update_reminder(self, reminder_id: int, status: str, due_at: float | None = None) -> None:
        self._conn.execute(
            "UPDATE reminders SET status = ?, due_at = COALESCE(?, due_at) WHERE id = ?",
            (status, due_at, reminder_id),
        )
        self._conn.commit()

    def set_unsaid(self, reminder_id: int, unsaid: bool) -> None:
        self._conn.execute(
            "UPDATE reminders SET unsaid = ? WHERE id = ?", (int(unsaid), reminder_id)
        )
        self._conn.commit()

    def unsaid_reminders(self) -> list[Reminder]:
        rows = self._conn.execute("SELECT * FROM reminders WHERE unsaid = 1 ORDER BY due_at")
        return [_to_reminder(row) for row in rows]

    # History: full-text search with SQLite's built-in FTS5

    def add_history(self, kind: str, text: str, commit: bool = True) -> None:
        self._conn.execute(
            "INSERT INTO history (kind, text, created_at) VALUES (?, ?, ?)",
            (kind, text, time.time()),
        )
        if commit:
            self._conn.commit()

    def search_history(self, words: list[str], limit: int = 5) -> list[tuple[str, float]]:
        """Entries matching any of the words, best matches first: (text, created_at)."""
        if not words:
            return []
        rows = self._conn.execute(
            "SELECT text, created_at FROM history WHERE history MATCH ? ORDER BY rank LIMIT ?",
            (_any_of(words), limit),
        )
        return [(row["text"], row["created_at"]) for row in rows]

    def delete_history(self, words: list[str]) -> int:
        """Delete entries containing all of the words. Returns how many."""
        if not words:
            return 0
        query = " AND ".join(f'"{word}"' for word in words)
        cursor = self._conn.execute("DELETE FROM history WHERE history MATCH ?", (query,))
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        self._conn.close()


def _to_reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(**{**dict(row), "run_job": bool(row["run_job"]), "unsaid": bool(row["unsaid"])})


def _any_of(words: list[str]) -> str:
    """An FTS5 query matching any of the words (each quoted, so it's never parsed as syntax)."""
    return " OR ".join(f'"{word}"' for word in words)


def _to_task(row: sqlite3.Row) -> Task:
    return Task(**{**dict(row), "told_user": bool(row["told_user"])})
