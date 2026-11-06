"""Alfred's reply, made fit to be spoken, on its way from GPT-6 Luna to Cartesia.

Web search answers come with their sources as links ("20 degrees ([bbc.com](https://...))"),
and the odd reply has markdown in it. Cartesia would read all of that out, and pay for
every character. This drops sources and web addresses, keeps a link's words ("[the
Guardian](https://...)" -> "the Guardian") and leaves out the marks nobody says.

The reply streams in word by word, so this only holds back what might still turn out to be
a link: text inside brackets, and a word that starts like "http" or "www.". Everything
else goes straight through.

Now and then GPT-6 Luna writes its whole answer twice in one reply ("Shall I send it?
Shall I send it?"). NoRepeats drops a sentence that starts like one already said in the
same reply: from the second sentence on, it holds back each sentence's first few words
(while the earlier ones are still being spoken) to compare them.
"""

import re

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from alfred.echo import EchoGuard

UNSPOKEN = str.maketrans("", "", "*`#[]")  # markdown marks nobody says
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")  # [label](link): keep the label
ADDRESS = re.compile(r"(https?://|www\.)\S*", re.IGNORECASE)
ADDRESS_STARTS = ("http", "www.")
HOLD_AT_MOST = 400  # an unclosed bracket is said after all once it's this long
SAME_START_WORDS = 4  # a sentence starting with the same words as an earlier one: a repeat
SENTENCE_ENDS = ".?!"


class SpeakableText(FrameProcessor):
    """Sits between the LLM and the TTS and cleans the reply as it streams through."""

    def __init__(self, echo: EchoGuard | None = None):
        super().__init__()
        self._text = Speakable()
        self._repeats = NoRepeats()
        self._echo = echo  # told what Alfred says, so his echo isn't taken for you

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame) and direction == FrameDirection.DOWNSTREAM:
            frame.text = self._repeats.feed(self._text.feed(frame.text))
            if frame.text:
                if self._echo:
                    self._echo.alfred_said(frame.text)
                await self.push_frame(frame, direction)
            return
        if isinstance(frame, LLMFullResponseEndFrame):
            rest = self._repeats.feed(self._text.flush()) + self._repeats.flush()
            if rest:
                if self._echo:
                    self._echo.alfred_said(rest)
                await self.push_frame(LLMTextFrame(rest), direction)
        elif isinstance(frame, (InterruptionFrame, LLMFullResponseStartFrame)):
            self._text.reset()  # whatever was held back belongs to a reply that's over
            self._repeats.reset()
        await self.push_frame(frame, direction)


class NoRepeats:
    """Drops a sentence that starts like one already said in this reply (see the module
    docstring). The first sentence goes straight through: nothing to compare it with."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.said: list[list[str]] = []  # the sentences of this reply so far, as words
        self.sentence = ""  # the current sentence, as written
        self.held = ""  # its start, held back until it's known not to be a repeat
        self.passing = False  # it isn't a repeat: let it through
        self.dropping = False  # it is: leave it out

    def feed(self, text: str) -> str:
        return "".join(self._char(ch) for ch in text)

    def flush(self) -> str:
        """The end of the reply: a held-back start is said, unless it's a repeat."""
        rest = "" if self.dropping or self._repeats(self.held) else self.held
        self.reset()
        return rest

    def _char(self, ch: str) -> str:
        self.sentence += ch
        if self.dropping or self.passing or not self.said:
            out = "" if self.dropping else ch
        else:
            self.held += ch
            out = ""
            enough = len(words_of(self.held)) >= SAME_START_WORDS and ch.isspace()
            if enough or ch in SENTENCE_ENDS:
                if self._repeats(self.held):
                    self.dropping = True
                else:
                    out, self.passing = self.held, True
                self.held = ""
        if ch in SENTENCE_ENDS:
            if not self.dropping and words_of(self.sentence):
                self.said.append(words_of(self.sentence))
            self.sentence, self.held, self.passing, self.dropping = "", "", False, False
        return out

    def _repeats(self, start: str) -> bool:
        words = words_of(start)[:SAME_START_WORDS]
        return bool(words) and any(earlier[: len(words)] == words for earlier in self.said)


def words_of(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


class Speakable:
    """The cleaning itself, one character at a time (see the module docstring)."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.held = ""  # an unfinished (...) or [...], or a word that may be a web address
        self.depth = 0  # how many brackets are open in it
        self.after_label = False  # it's a [label]: a (link) may follow
        self.space = ""  # spaces not passed on yet: dropped with a source right after them
        self.mid_word = False  # in a word that's already been let through

    def feed(self, text: str) -> str:
        return "".join(self._char(ch) for ch in text).translate(UNSPOKEN)

    def flush(self) -> str:
        """The end of the reply: whatever is still held back, cleaned."""
        rest = self._release()
        self.reset()
        return rest.translate(UNSPOKEN)

    def _char(self, ch: str) -> str:
        if self.after_label:
            self.after_label = False
            if ch == "(":
                self.held, self.depth = self.held + ch, 1
                return ""
            return self._release() + self._char(ch)
        if self.depth:
            return self._inside(ch)
        if ch in "([":
            out = self._release()
            self.held, self.depth, self.space = self.space + ch, 1, ""
            return out
        if ch.isspace():
            out = self._release()
            self.space, self.mid_word = self.space + ch, False
            return out
        return self._in_word(ch)

    def _inside(self, ch: str) -> str:
        self.held += ch
        self.depth += (ch in "([") - (ch in ")]")
        if self.depth == 0:
            if ch == "]":
                self.after_label = True
                return ""
            return self._release()
        if len(self.held) > HOLD_AT_MOST:
            return self._release()
        return ""

    def _in_word(self, ch: str) -> str:
        out, self.space = self.space, ""
        if self.mid_word:
            return out + ch
        word = self.held + ch
        if could_be_address(word):
            self.held = word
            return out
        self.held, self.mid_word = "", True
        return out + word

    def _release(self) -> str:
        held, self.held, self.depth = self.held, "", 0
        text = LINK.sub(r"\1", held)
        if text.lstrip().startswith("(") and (text != held or ADDRESS.search(text)):
            return ""  # a source in brackets, with the space before it
        return ADDRESS.sub("", text)


def could_be_address(word: str) -> bool:
    word = word.lower()
    return any(start.startswith(word) or word.startswith(start) for start in ADDRESS_STARTS)
