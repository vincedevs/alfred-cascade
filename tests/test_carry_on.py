"""When words that weren't meant for him cut Alfred off and he says nothing, he's asked once
to carry on; never for a reply he gave, nor for one that was simply quiet."""

from alfred.carry_on import CutOff


def cut_off_while_speaking() -> CutOff:
    state = CutOff()
    state.speaking = True
    state.reply_started()  # the answer that gets cut off
    state.answered = True
    state.interrupted()  # "Your coffee's getting cold"
    assert not state.reply_ended()  # the end of the cut-off answer doesn't count
    return state


def test_a_silent_reply_after_being_cut_off_asks_him_to_carry_on():
    state = cut_off_while_speaking()
    state.reply_started()
    assert state.reply_ended()


def test_he_is_asked_only_once():
    state = cut_off_while_speaking()
    state.reply_started()
    assert state.reply_ended()
    state.reply_started()  # the carry-on reply, silent too
    assert not state.reply_ended()


def test_no_carry_on_when_he_answered_the_interruption():
    state = cut_off_while_speaking()
    state.reply_started()
    state.answered = True  # "Of course, let's talk about yoga."
    assert not state.reply_ended()


def test_no_carry_on_when_he_wasnt_speaking():
    state = CutOff()
    state.interrupted()  # you started talking while he was quiet
    state.reply_started()
    assert not state.reply_ended()  # an empty reply here is him ignoring side talk
