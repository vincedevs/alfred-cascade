"""Greeting after "Hey Alfred" alone, and recognising "go to sleep"."""

import asyncio

from alfred import greeting
from alfred.greeting import GREETING, SleepCommand, asks_to_sleep, greet_unless_asked


def test_greets_only_when_the_user_says_nothing(monkeypatch):
    monkeypatch.setattr(greeting, "LISTEN_SECONDS", 0.05)
    said = []

    async def say_aloud(text):
        said.append(text)

    async def main(user_speaks):
        spoke = asyncio.Event()
        if user_speaks:  # "Hey Alfred, what time is it?"
            asyncio.get_running_loop().call_later(0.01, spoke.set)
        await greet_unless_asked(spoke, say_aloud)

    asyncio.run(main(user_speaks=True))
    assert said == []
    asyncio.run(main(user_speaks=False))
    assert said == [GREETING]


def test_explicit_requests_to_sleep():
    for said in (
        "Go to sleep, please",
        "Alfred, sleep.",
        "You can sleep now",
        "Time to sleep, Alfred",
        "Go back to sleep",
        "I told you to sleep",
        "Sleep right now",  # seen in testing: the model said goodnight, Alfred stayed awake
        "I said go to sleep",
        "Uh, you may go to sleep now",  # seen on the Pi
        "Okay Alfred, you can go to sleep now, sir",
    ):
        assert asks_to_sleep(said), said


def test_not_requests_to_sleep():
    for said in (
        "Thanks",
        "That's all",
        "Goodnight",
        "Bye",
        "Never mind",
        "Don't go to sleep yet",
        "Are you going to sleep?",
        "I couldn't sleep last night",
        "Remind me to go to sleep at 11",  # seen in testing: Alfred went to sleep
        "What time should I go to sleep?",
        "Set an alarm so I go to sleep on time",
    ):
        assert not asks_to_sleep(said), said


def test_sleep_command_and_a_yes_to_the_offer():
    calls = []
    sleep = SleepCommand(calls.append)
    sleep.user_said("I think I'm done for today")
    assert calls == []
    sleep.alfred_said("Certainly. Shall I go to sleep?")
    sleep.user_said("Yes, please")
    assert calls == [False]  # his goodnight is still to come
    sleep.user_said("Go to sleep")
    assert calls == [False]  # once is enough


def test_a_bare_goodnight_after_a_sleep_wording_we_missed():
    calls = []
    sleep = SleepCommand(calls.append)
    sleep.user_said("Off to sleep with you, Alfred")  # not in our word list
    assert calls == []
    sleep.alfred_said("Goodnight, sir.")
    assert calls == [True]  # he has just said it


def test_a_goodnight_without_sleep_mentioned_changes_nothing():
    calls = []
    sleep = SleepCommand(calls.append)
    sleep.user_said("Goodnight")
    sleep.alfred_said("Goodnight, sir.")
    sleep.user_said("Remind me to go to sleep at one a.m.")
    sleep.alfred_said("I'll remind you at one a.m. to go to sleep.")
    assert calls == []
