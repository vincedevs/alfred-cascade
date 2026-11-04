"""What Alfred remembers about you.

- The profile (~/.alfred/profile.md): lasting facts, one per line ("Vegetarian."). Every
  conversation and every job sees it. It's a plain file: read it, edit it, or ask Alfred
  to forget something.
- The history (in the database): job results and a short summary of each conversation,
  searchable by words ("what did you find about ramen?"). Not word-for-word transcripts.

After each conversation, learn() asks Claude Haiku for any new lasting facts and a
one-or-two-line summary. Throwaway chatter is left out.
"""

import json
import re

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from loguru import logger

from alfred.config import Config, load_prompt
from alfred.db import Database

TOO_SHORT_TO_LEARN = 8  # words you said: "thanks, Alfred" isn't worth a Claude call
COMMON_WORDS = set(
    "the a an and or of to in on at for is are was were it my me i you your what did do "
    "about with that this from have has had be been find found tell".split()
)


def words_in(text: str) -> list[str]:
    """The words worth searching for: no common words, no punctuation."""
    found = re.findall(r"[a-z0-9]+", text.lower())
    return [word for word in found if word not in COMMON_WORDS and len(word) > 1]


class Memory:
    def __init__(self, db: Database, config: Config):
        self._db = db
        self._config = config
        self._path = config.profile_path

    # The profile

    def facts(self) -> list[str]:
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").splitlines()
        return [line.removeprefix("- ").strip() for line in lines if line.startswith("- ")]

    def remember(self, fact: str) -> bool:
        """Add a fact unless it's already there. True if it was new."""
        fact = fact.strip().rstrip(".") + "."
        if fact.lower() in (known.lower() for known in self.facts()):
            return False
        self._write(self.facts() + [fact])
        logger.info(f"Remembered: {fact}")
        return True

    def forget(self, about: str) -> list[str]:
        """Remove the facts (and history entries) about something. Returns the facts removed."""
        wanted = set(words_in(about))
        if not wanted:
            return []
        kept, removed = [], []
        for fact in self.facts():
            overlap = len(wanted & set(words_in(fact))) / len(wanted)
            (removed if overlap >= 0.5 else kept).append(fact)
        if removed:
            self._write(kept)
        entries = self._db.delete_history(sorted(wanted))
        logger.info(f"Forgot {len(removed)} fact(s) and {entries} history entries about {about!r}")
        return removed

    def profile_text(self) -> str:
        """For prompts: the facts as a short section, or nothing."""
        facts = self.facts()
        if not facts:
            return ""
        lines = "\n".join(f"- {fact}" for fact in facts)
        return f"\n\n## What you know about the user (from memory)\n{lines}"

    def _write(self, facts: list[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = "".join(f"- {fact}\n" for fact in facts)
        header = "# What Alfred knows about you\n\nEdit freely: one fact per line.\n\n"
        self._path.write_text(header + lines, encoding="utf-8")

    # The history

    def search(self, about: str, limit: int = 5) -> list[str]:
        found = self._db.search_history(words_in(about), limit)
        return [f"({self._config.local(when):%a %d %b %Y}) {text}" for text, when in found]

    # Learning after a conversation

    async def learn(self, transcript: list[str]) -> None:
        """Ask Claude Haiku what's worth keeping from a conversation. Never raises."""
        yours = [line for line in transcript if line.startswith("You:")]
        if sum(len(line.split()) - 1 for line in yours) < TOO_SHORT_TO_LEARN:
            return
        try:
            notes = await self._ask_claude("\n".join(transcript)[-12000:])
        except Exception as error:  # memory is a nice-to-have: never break Alfred over it
            logger.warning(f"Couldn't learn from the conversation: {error}")
            return
        for fact in notes.get("new_facts", []):
            self.remember(str(fact))
        if summary := str(notes.get("summary", "")).strip():
            self._db.add_history("conversation", summary)
            logger.info(f"Conversation summary: {summary}")

    async def _ask_claude(self, transcript: str) -> dict:
        options = ClaudeAgentOptions(
            model=self._config.agent_model,
            system_prompt=load_prompt("memory"),
            tools=[],  # it only reads and answers
            max_turns=1,
            setting_sources=[],
            cwd=self._config.workspace_dir,
        )
        known = "\n".join(f"- {fact}" for fact in self.facts()) or "(nothing yet)"
        prompt = f"Already known:\n{known}\n\nConversation:\n{transcript}"
        answer = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                answer = message.result or ""
                logger.info(f"Memory notes: about ${message.total_cost_usd or 0:.4f}")
        return json.loads(answer[answer.find("{") : answer.rfind("}") + 1] or "{}")
