"""Alfred's own voice coming back through the microphone, told apart from you.

The echo canceller (LiveKit's, in transport.py) removes most of what the speaker plays
from what the microphone hears, but not all of it in a loud room or with the volume up:
on the Pi, Deepgram once wrote down Alfred's own sentence as if you had said it, and that
stopped him mid-answer. While he speaks, it takes a few of your words to stop him
(voice.INTERRUPT_WORDS); EchoGuard also checks that those words aren't mostly his own,
and that a word or two heard just after he stops isn't the end of his own sentence.

The reply arrives in pieces as it streams (a word can be split across two), so it's kept
as text and split into words only when checked.
"""

import re

KEEP_CHARACTERS = 600  # about the last 100 words Alfred said: what can still echo
ECHO_SHARE = 0.7  # this share of what was heard is Alfred's own words: it's his echo
CHECK_FROM_WORDS = 3  # fewer words than this are never taken for an echo...
# ...except just after he stops: the tail of "Yes, sir?" came back as you saying "Yes."
# and was taken as a yes. A word or two heard this soon, all from his last words, is his.
TAIL_SECONDS = 2.0
TAIL_CHARACTERS = 80  # his last words: what can still be in the room
TAIL_WORDS = 2


class EchoGuard:
    def __init__(self):
        self._said = ""

    def alfred_said(self, text: str) -> None:
        """A piece of what Alfred is saying, as it streams to his voice."""
        self._said = (self._said + text)[-KEEP_CHARACTERS:]

    def is_echo(self, heard: str) -> bool:
        """Is this mostly Alfred's own recent words, heard back?"""
        words = words_in(heard)
        if len(words) < CHECK_FROM_WORDS:
            return False
        his = set(words_in(self._said))
        return sum(word in his for word in words) / len(words) >= ECHO_SHARE

    def is_tail_echo(self, heard: str) -> bool:
        """A word or two that are all from what Alfred said last (call this only just
        after he stopped speaking: TAIL_SECONDS)."""
        words = words_in(heard)
        if not words or len(words) > TAIL_WORDS:
            return False
        last = set(words_in(self._said[-TAIL_CHARACTERS:]))
        return all(word in last for word in words)


def words_in(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())
