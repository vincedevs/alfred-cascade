"""What Alfred says when you wake him, and how you send him to sleep.

Waking: "Hey Alfred" on its own gets a short greeting ("Yes, sir?"). "Hey Alfred, what
time is it?" doesn't: he just answers. Speech after the wake word is held back until
Deepgram is connected, then sent; if Deepgram hears you start speaking soon after, it was a
request. Its speech detection is used rather than our own microphone check, which the wake
chime's echo and the Pi's noisier microphone both fooled.

Sleeping: "Alfred, go to sleep" is recognised here, from what you said, not by a model:
a model may say "Goodnight, sir." without acting on it. It counts when the
whole sentence is the command ("sleep right now", "I said go to sleep"), so "remind me to
go to sleep at 11" is left alone; or when you mentioned sleep and Alfred answered with
nothing but a goodnight. Alfred says goodnight and the session closes as soon as he's done
(idle.py). Jobs keep running, timers and alarms still ring, waiting questions are asked
next time, and "Hey Alfred" wakes him.
"""

import asyncio
import re

from loguru import logger

from alfred.announcer import FILLER, quick_answer

LISTEN_SECONDS = 0.6  # after Deepgram connects: speech it hears by then is a request
# Spoken straight by the voice, not written by the model: that took up to 2.7 s.
GREETING = "Yes, sir?"

# The only words a sleep command may have besides "sleep": "go to sleep", "sleep right
# now", "Alfred, you may go back to sleep", "I told you to sleep", "time to sleep, sir".
# Any other word ("remind me…", "are you going…", "don't…", "couldn't") means it's not one.
SLEEP_COMMAND_WORDS = {
    "sleep", "go", "back", "to", "now", "right", "please", "just", "alfred", "sir", "you",
    "can", "could", "may", "should", "i", "said", "told", "time", "it's", "its", "is",
    "then", "already", "alright", "all", "hey", "mode",
}  # fmt: skip
OFFER = re.compile(r"\bgo to sleep\b.*\?")  # Alfred asked "Shall I go to sleep?"
ONLY_GOODNIGHT = re.compile(r"^\W*good ?night\W*(sir\W*)?$")  # "Goodnight, sir."


async def greet_unless_asked(user_spoke: asyncio.Event, say_aloud) -> None:
    """Once Deepgram is connected: greet, unless it hears the user speaking.
    say_aloud(text) speaks the words as they are."""
    try:
        await asyncio.wait_for(user_spoke.wait(), LISTEN_SECONDS)
        logger.info("A request followed the wake word: no greeting")
    except TimeoutError:
        logger.info("Only the wake word: greeting")
        await say_aloud(GREETING)


def words_of(said: str) -> set[str]:
    return set(re.findall(r"[a-z']+", said.lower().replace("’", "'"))) - FILLER


def asks_to_sleep(said: str) -> bool:
    """An explicit request to sleep, and nothing else. Not "thanks", "goodnight", "don't
    go to sleep", "are you going to sleep?" or "remind me to go to sleep at 11"."""
    words = words_of(said)
    return "sleep" in words and words <= SLEEP_COMMAND_WORDS


class SleepCommand:
    """Watches the conversation for "go to sleep" and calls go_to_sleep(said_goodnight).
    Also: a yes after Alfred offers to sleep, and a bare goodnight from Alfred after you
    mentioned sleep (the model understood a wording this module doesn't)."""

    def __init__(self, go_to_sleep):
        self._go_to_sleep = go_to_sleep
        self._offered = False
        self._mentioned_sleep = False
        self._asked = False  # already going to sleep

    def user_said(self, text: str) -> None:
        offered, self._offered = self._offered, False
        self._mentioned_sleep = "sleep" in words_of(text)
        if asks_to_sleep(text) or (offered and quick_answer(text) == "yes"):
            self._sleep(f"Told to go to sleep: {text!r}", said_goodnight=False)

    def alfred_said(self, text: str) -> None:
        self._offered = bool(OFFER.search(text.lower()))
        if self._mentioned_sleep and ONLY_GOODNIGHT.match(text.lower()):
            self._sleep(f"Alfred said goodnight to a sleep request: {text!r}", True)
        self._mentioned_sleep = False

    def _sleep(self, why: str, said_goodnight: bool) -> None:
        if not self._asked:
            self._asked = True
            logger.info(why)
            self._go_to_sleep(said_goodnight)
