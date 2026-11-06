"""`uv run alfred-reset`: wipe what Alfred keeps, for a fresh start (a demo, a new owner).

    uv run alfred-reset           everything: files, database, profile, audit log, logs
    uv run alfred-reset files     only files jobs made (notes, code, web pages)
    uv run alfred-reset web       only the shared web pages
    uv run alfred-reset profile   only what he knows about you (profile.md)
    uv run alfred-reset logs      only the logs in logs/
    add --yes to skip the question

Your settings (.env) and the code are never touched. Alfred must be stopped first.
"""

import argparse
import re
import shutil
import sqlite3
import sys
from pathlib import Path

from alfred.config import Config, ConfigError, load_config
from alfred.db import Database
from alfred.lock import only_one_alfred
from alfred.logs import LOG_DIR
from alfred.saved import mark_gone
from alfred.web import WWW

WHAT = {
    "all": "everything Alfred keeps",
    "files": "files jobs made",
    "web": "shared web pages",
    "profile": "what Alfred knows about you",
    "logs": "the logs",
}


def targets(what: str, data_dir: Path) -> list[Path]:
    """The files and folders to delete; ones that don't exist are left out."""
    workspace = data_dir / "workspace"
    logs = sorted(LOG_DIR.glob("*.log"))  # not subfolders (e.g. logs copied from the Pi)
    paths = {
        "files": [workspace],
        "web": [workspace / WWW],
        "profile": [data_dir / "profile.md"],
        "logs": logs,
        # everything in ~/.alfred, Claude's notes on Alfred's jobs, and the logs
        "all": [*sorted(data_dir.iterdir()), claude_notes(workspace), *logs]
        if data_dir.exists()
        else [claude_notes(workspace), *logs],
    }[what]
    return [path for path in paths if path.exists() and path.name != "alfred.lock"]


def claude_notes(workspace: Path) -> Path:
    """Where Claude keeps its notes on Alfred's jobs (named after the workspace path). Only
    this folder: the others in ~/.claude/projects may be your own Claude Code sessions."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(workspace))


def delete(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def size(path: Path) -> str:
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files if not p.is_symlink())
    return f"{total / 1_000_000:.1f} MB" if total >= 100_000 else f"{total / 1000:.0f} KB"


def main() -> None:
    parser = argparse.ArgumentParser(prog="alfred-reset", description=__doc__.split("\n")[0])
    parser.add_argument("what", nargs="?", default="all", choices=WHAT)
    parser.add_argument("--yes", action="store_true", help="don't ask, just delete")
    args = parser.parse_args()
    try:
        config = load_config()
    except ConfigError as error:
        sys.exit(f"Config error: {error}")

    lock = only_one_alfred(config.data_dir)
    if lock is None:
        sys.exit("Alfred is running. Stop him first (Ctrl+C, or: systemctl --user stop alfred).")
    found = targets(args.what, config.data_dir)
    if not found:
        print(f"Nothing to delete: {WHAT[args.what]} is already empty.")
        return
    print(f"This deletes {WHAT[args.what]}:")
    for path in found:
        print(f"  {path}  ({size(path)})")
    print("Your settings (.env) and the code stay.")
    if not args.yes and input("Delete? Type yes: ").strip().lower() != "yes":
        print("Nothing deleted.")
        return
    for path in found:
        delete(path)
    if args.what in ("files", "web"):
        mark_deleted_in_history(config)
    print("Done. Alfred starts fresh next time.")


def mark_deleted_in_history(config: Config) -> None:
    """Past job results keep saying where they saved things: mark the ones just deleted
    "(no longer there)", so Alfred never gives a dead link (saved.py)."""
    if not config.database_path.exists():
        return
    try:
        db = Database(config.database_path)
        changed = {}
        for rowid, text in db.job_history():
            marked = mark_gone(text, config.workspace_dir)
            if marked != text:
                changed[rowid] = marked
        db.rewrite_history(changed)
        db.close()
    except sqlite3.DatabaseError as error:
        print(f"Couldn't update the history ({error}); Alfred still checks when he looks.")
        return
    if changed:
        print(f"Marked {len(changed)} past job result(s) as no longer there.")


if __name__ == "__main__":
    main()
