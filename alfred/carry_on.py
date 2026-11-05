"""Alfred carries on when words that weren't meant for him cut him off.

Someone speaks over Alfred ("Your coffee's getting cold") and Flux stops him at once. GPT-6
Luna then rightly says nothing, since it wasn't meant for him, but the answer he was giving
is already cut off, so he would fall silent. The prompt asks him to carry on in that case;
when he still says nothing, this asks him once more, as a note in the conversation.

The conversation keeps only what Alfred actually said before he was cut off, so he knows
where to pick up.
"""

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

CARRY_ON = (
    "You were cut off mid-answer by speech that wasn't meant for you. Carry on from where "
    "you stopped, without repeating what you already said. If you were asked to stop, say "
    "nothing."
)


class CarryOnAfterAside(FrameProcessor):
    """Sits right after the LLM, and asks it to carry on when a cut-off gets no reply."""

    def __init__(self):
        super().__init__()
        self._cut_off = CutOff()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self._notice(frame)
        await self.push_frame(frame, direction)
        if isinstance(frame, LLMFullResponseEndFrame) and self._cut_off.reply_ended():
            logger.info("Cut off by words not meant for him: asking Alfred to carry on")
            note = LLMMessagesAppendFrame(
                messages=[{"role": "developer", "content": CARRY_ON}], run_llm=True
            )
            await self.push_frame(note, FrameDirection.UPSTREAM)  # to the conversation

    def _notice(self, frame: Frame) -> None:
        cut_off = self._cut_off
        if isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
            cut_off.speaking = isinstance(frame, BotStartedSpeakingFrame)
        elif isinstance(frame, InterruptionFrame):
            cut_off.interrupted()
        elif isinstance(frame, LLMFullResponseStartFrame):
            cut_off.reply_started()
        elif isinstance(frame, (FunctionCallsStartedFrame, FunctionCallInProgressFrame)) or (
            isinstance(frame, LLMTextFrame) and frame.text.strip()
        ):
            cut_off.answered = True


class CutOff:
    """Whether Alfred was cut off mid-answer and hasn't said anything since."""

    def __init__(self):
        self.speaking = False  # Alfred is speaking
        self.cut_off = False  # ...and someone stopped him, with no answer yet
        self.in_reply = False  # a reply started after that (not the one cut off)
        self.answered = False  # ...and it said something, or called a tool

    def interrupted(self) -> None:
        self.cut_off = self.cut_off or self.speaking
        self.in_reply = self.answered = False

    def reply_started(self) -> None:
        self.in_reply, self.answered = True, False

    def reply_ended(self) -> bool:
        """A reply ended: whether to ask Alfred to carry on (once per cut-off)."""
        if not self.in_reply:
            return False  # the end of the reply that was cut off
        self.in_reply = False
        carry_on = self.cut_off and not self.answered
        if self.answered or carry_on:
            self.cut_off = False
        return carry_on
