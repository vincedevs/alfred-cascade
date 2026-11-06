"""The "Saved:" line a job ends its result with, and whether what it names is still there.

A job that saves something ends its result with one line naming each thing and where it
is (prompts/agent.md), for example:

    Saved: Snake game, link http://raspy.local:8080/snake/, folder www/snake

That line goes into the searchable history with the rest of the result, so Alfred can
find it again however long ago it was made. Things get deleted (alfred-reset, or by
hand), so before he passes a result on, mark_gone() adds "(no longer there)" to any line
whose folder, file or page has gone: he says so instead of giving a dead link.
"""

import re
from pathlib import Path, PurePosixPath

from alfred.web import WWW

SAVED_LINE = re.compile(r"\bSaved:.*$", re.MULTILINE)  # its own line, or a one-line result
PLACE = re.compile(r"\b(?:folder|file) ([^\s,;]+)")  # relative to the workspace
PAGE = re.compile(r"https?://[^/\s]+/([^/\s,;]+)")  # a page's folder in www/
GONE = " (no longer there)"


def mark_gone(text: str, workspace: Path) -> str:
    """The text, with "(no longer there)" after each Saved line naming something deleted."""
    return SAVED_LINE.sub(lambda line: _checked(line.group(0), workspace), text)


def _checked(line: str, workspace: Path) -> str:
    if line.endswith(GONE):
        return line
    places = [_inside(workspace, place) for place in PLACE.findall(line)]
    places += [_inside(workspace / WWW, page) for page in PAGE.findall(line)]
    places = [place for place in places if place is not None]
    if places and not all(place.exists() for place in places):
        return line + GONE
    return line


def _inside(folder: Path, relative: str) -> Path | None:
    """folder/relative, or None for anything that would point outside it."""
    path = PurePosixPath(relative.rstrip("./") or ".")
    if path.is_absolute() or ".." in path.parts:
        return None
    return folder / path
