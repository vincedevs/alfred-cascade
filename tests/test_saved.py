"""A job's "Saved:" line is checked against what's still there, so Alfred never gives a
dead link: a page or folder that's gone gets "(no longer there)"."""

from alfred.saved import GONE, mark_gone


def workspace_with_snake(tmp_path):
    (tmp_path / "www" / "snake").mkdir(parents=True)
    (tmp_path / "budget-notes.md").write_text("notes")
    return tmp_path


SNAKE = "Saved: Snake game, link http://raspy.local:8080/snake/, folder www/snake"


def test_what_is_still_there_is_left_alone(tmp_path):
    workspace = workspace_with_snake(tmp_path)
    text = f"It's ready.\n{SNAKE}; budget notes, file budget-notes.md"
    assert mark_gone(text, workspace) == text


def test_a_deleted_page_is_marked(tmp_path):
    text = f"It's ready.\n{SNAKE}"
    assert mark_gone(text, tmp_path) == text + GONE
    assert mark_gone(mark_gone(text, tmp_path), tmp_path) == text + GONE  # marked once


def test_one_thing_gone_marks_the_line(tmp_path):
    workspace = workspace_with_snake(tmp_path)
    text = f"{SNAKE}; old chart, file chart.png"
    assert mark_gone(text, workspace).endswith(GONE)


def test_results_without_a_saved_line_are_unchanged(tmp_path):
    text = "Ramen Nagi and Mendokoro are the favourites."
    assert mark_gone(text, tmp_path) == text


def test_paths_outside_the_workspace_are_never_checked(tmp_path):
    text = "Saved: secrets, file ../../etc/passwd; key, file /etc/hosts"
    assert mark_gone(text, tmp_path) == text
