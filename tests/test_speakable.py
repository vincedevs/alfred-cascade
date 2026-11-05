"""Alfred's reply is cleaned for speaking: no sources, web addresses or markdown marks,
however the reply is split into pieces as it streams in."""

import pytest

from alfred.speakable import Speakable


def spoken(reply: str, piece: int) -> str:
    """What Cartesia gets when the reply arrives `piece` characters at a time."""
    text = Speakable()
    pieces = [reply[i : i + piece] for i in range(0, len(reply), piece)]
    return "".join(text.feed(p) for p in pieces) + text.flush()


@pytest.mark.parametrize("piece", [1, 3, 7, 1000])
@pytest.mark.parametrize(
    "reply, said",
    [
        (
            "It's 20 degrees in London ([bbc.com](https://www.bbc.com/weather?utm_source=openai)).",
            "It's 20 degrees in London.",
        ),
        ("The [Guardian](https://theguardian.com/x) says rain.", "The Guardian says rain."),
        ("See https://example.com/a?b=1 for more.", "See  for more."),
        ("Try www.bbc.co.uk today.", "Try  today."),
        ("It's **very** good, `honestly`.", "It's very good, honestly."),
        ("## Weather\nSunny (about 20 minutes away).", " Weather\nSunny (about 20 minutes away)."),
        ("What a wonderful hat, sir.", "What a wonderful hat, sir."),
        ("Take the w and the h.", "Take the w and the h."),
        ("Open (bracket never closes", "Open (bracket never closes"),
    ],
)
def test_only_the_words_are_spoken(reply, said, piece):
    assert spoken(reply, piece) == said


def test_a_plain_reply_is_not_held_back():
    text = Speakable()  # (only a trailing space waits, in case a source follows it)
    assert text.feed("Good evening, sir. ") == "Good evening, sir."


def test_an_interruption_drops_what_was_held_back():
    text = Speakable()
    text.feed("See ([bbc")
    text.reset()
    assert text.feed("Right.") + text.flush() == "Right."
