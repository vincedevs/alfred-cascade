"""The benchmark's prompt: Alfred's real prompt (prompts/voice.md), minus the sections about
features switched off for benchmarking (background jobs, memory, reminders, the house).

They are trimmed when loaded, never copied, so any later edit to Alfred's prompts reaches
the benchmark too. tests/test_benchmark.py checks that only these sections differ.
"""

from alfred.config import load_prompt

# Sections ("## Heading" to the next "## ") left out in benchmark mode.
LEFT_OUT = {
    "voice": [
        "Things you are told to say",  # announcements from jobs and reminders
        # tools not there
        "Background jobs",
        "The house",
        "Messages",
        "Memory",
        "Reminders",
        "Timers and alarms",
    ],
}
THIS_SESSION = (
    "\n\n## In this session\n"
    "You have web search only: background jobs, memory, reminders, timers, alarms, "
    "messages and the house are switched off. "
    "If the user asks for one, say briefly that it isn't available right now."
)


def benchmark_prompt(name: str) -> str:
    return without_sections(load_prompt(name), LEFT_OUT.get(name, [])) + THIS_SESSION


def without_sections(text: str, headings: list[str]) -> str:
    kept, skipping = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            skipping = line[3:].strip() in headings
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip()
