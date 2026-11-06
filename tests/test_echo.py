"""Alfred's own voice heard back through the mic isn't taken for you interrupting him."""

from alfred.echo import EchoGuard


def guard_saying(text: str) -> EchoGuard:
    guard = EchoGuard()
    for piece in [text[i : i + 4] for i in range(0, len(text), 4)]:  # streams in pieces
        guard.alfred_said(piece)
    return guard


SAID = "The Raspberry Pi first came out in 2012, to teach children programming."


def test_his_own_words_heard_back_are_an_echo():
    guard = guard_saying(SAID)
    assert guard.is_echo("came out in 2012 to teach")
    assert guard.is_echo("the raspberry pi first came out")


def test_you_interrupting_is_not():
    guard = guard_saying(SAID)
    assert not guard.is_echo("Actually, stop. How far is the Moon?")
    assert not guard.is_echo("wait, tell me more about the children")


def test_a_word_or_two_is_never_taken_for_an_echo():
    guard = guard_saying(SAID)
    assert not guard.is_echo("the Pi")  # too short to tell: the word rule decides


def test_only_his_recent_words_count():
    guard = guard_saying(SAID)
    guard.alfred_said(" " + "Very good, sir. " * 60)  # much later
    assert not guard.is_echo("came out in 2012 to teach")
