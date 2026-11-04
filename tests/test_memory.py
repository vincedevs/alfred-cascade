"""What Alfred remembers: the profile file, the searchable history, and forgetting."""

import asyncio

from alfred.config import Config
from alfred.db import Database
from alfred.memory import Memory, words_in


def make(tmp_path):
    db = Database(tmp_path / "alfred.db")
    return db, Memory(db, Config(openai_api_key="sk-test", data_dir=tmp_path))


def test_remember_writes_a_readable_profile(tmp_path):
    _, memory = make(tmp_path)
    assert memory.remember("Is vegetarian")
    assert not memory.remember("is vegetarian.")  # already known
    memory.remember("Works from home on Fridays.")
    assert memory.facts() == ["Is vegetarian.", "Works from home on Fridays."]
    text = (tmp_path / "profile.md").read_text()
    assert "- Is vegetarian.\n" in text and "Edit freely" in text
    assert "Is vegetarian." in memory.profile_text()


def test_your_own_edits_to_the_profile_count(tmp_path):
    _, memory = make(tmp_path)
    (tmp_path / "profile.md").write_text("# Notes\n\n- Has a cat called Mochi.\n")
    assert memory.facts() == ["Has a cat called Mochi."]


def test_forget_removes_the_fact_and_related_history(tmp_path):
    db, memory = make(tmp_path)
    memory.remember("Lives at 12 Mango Street, Makati.")
    memory.remember("Is vegetarian.")
    db.add_history("conversation", "Gave the delivery address 12 Mango Street.")
    assert memory.forget("my address on Mango Street") == ["Lives at 12 Mango Street, Makati."]
    assert memory.facts() == ["Is vegetarian."]
    assert memory.search("Mango Street") == []


def test_finished_jobs_are_searchable(tmp_path):
    db, memory = make(tmp_path)
    task_id = db.add_task("find good ramen in Makati")
    db.finish_task(task_id, "done", "Ramen Nagi and Mendokoro are the favourites.")
    found = memory.search("what did you find about ramen yesterday?")
    assert len(found) == 1 and "Mendokoro" in found[0]
    assert memory.search("sushi") == []


def test_search_words_skip_the_filler():
    assert words_in("What did you find about the ramen?") == ["ramen"]


def test_learning_keeps_new_facts_and_a_summary(tmp_path):
    async def main():
        db, memory = make(tmp_path)
        memory.remember("Is vegetarian.")

        async def fake_claude(_transcript):
            return {"new_facts": ["Is vegetarian.", "Has a sister, Ana."], "summary": "Ramen."}

        memory._ask_claude = fake_claude
        await memory.learn(["You: I'm vegetarian, and my sister Ana loves ramen.", "Alfred: ..."])
        assert memory.facts() == ["Is vegetarian.", "Has a sister, Ana."]
        assert memory.search("ramen") and "Ramen." in memory.search("ramen")[0]

    asyncio.run(main())


def test_learning_failures_never_break_alfred(tmp_path):
    async def main():
        _, memory = make(tmp_path)

        async def broken(_transcript):
            raise RuntimeError("offline")

        memory._ask_claude = broken
        await memory.learn(["You: I'm vegetarian and my sister Ana lives in Cebu."])
        # ...logs a warning, no exception
        assert memory.facts() == []

    asyncio.run(main())


def test_tiny_conversations_are_not_worth_a_claude_call(tmp_path):
    async def main():
        _, memory = make(tmp_path)
        calls = []

        async def counting(_transcript):
            calls.append(1)
            return {}

        memory._ask_claude = counting
        await memory.learn(["You: thanks, Alfred", "Alfred: My pleasure."])
        assert calls == []

    asyncio.run(main())
