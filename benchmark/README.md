# Benchmarking Alfred with fd-benchmark

[fd-benchmark](https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking) plays test
clips straight into Alfred and scores turn-taking, pauses, interruptions, false turn-taking and
latency. No microphone or speaker is used.

## What benchmark mode is

The files here run Alfred's own pipeline, models, voice and settings (imported from `alfred/`,
not copied), adapted for the benchmark:

| | Alfred | Benchmark mode |
|---|---|---|
| Deepgram Flux, GPT-6 Luna, Cartesia voice, web search, settings from `.env` | ✓ | same |
| Alfred's prompt | full | same text, minus the jobs, memory and reminders sections |
| Wake word, sounds, closing after 25 s of quiet | ✓ | off: one conversation per round |
| Background jobs, email, memory, reminders | ✓ | off: nothing is written to `~/.alfred` |
| Microphone, speaker, echo cancellation | ✓ | bypassed: the benchmark sends audio directly |

- `voice.py`: builds the conversation with the benchmark's transport.
- `prompts.py`: Alfred's prompts, trimmed as they load, so later edits carry over.
- `assistant_entry.py`: the file fd-benchmark's launcher loads.
- `tests/test_benchmark.py` checks the prompts differ only by the trimmed sections.

## Set up (once)

From the `alfred-cascade` folder:

```bash
git clone https://github.com/trueshotjames/ai231-me3-full-duplex-benchmarking.git fd-benchmark
cd fd-benchmark
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-score.txt
.venv/bin/python tools/fetch_datasets.py      # prints "datasets ready: 430 audio files in 9 groups"
cp .env.example .env
```

Then add this line to `fd-benchmark/.env`, with your own path:

```
FDBENCH_ENTRY=/Users/you/path/to/alfred-cascade/benchmark/assistant_entry.py
```

The same file takes an optional key for the benchmark's judge model (see its `.env.example`).
Alfred's own keys stay in `alfred-cascade/.env`.

Check (from `fd-benchmark`); the last line must say `READY`:

```bash
../.venv/bin/python launch_assistant.py --check
```

## Run

From `fd-benchmark`, one round (about 7 minutes):

```bash
.venv/bin/python -m fdbench run --student YOUR_ID --rounds 1 --connect direct --where local \
  --judge-provider none --yes --recordings k
```

Or interactively: `.venv/bin/python -m fdbench`. Results land in `fd-benchmark/runs/<student>/<date-time>/`
(`results.html` opens in a browser). Alfred's own log for the run is
`fd-benchmark/runs/_assistant/alfred-bench-<date-time>.log`.

## Good to know

- **Interruptions:** Deepgram Flux detects you talking over Alfred and Pipecat sends its usual
  "interrupted" signal, so the benchmark stops counting audio already sent.
- **Comparing with alfred-sts:** its GPT-Live results are in alfred-sts's `benchmark/results/`
  (tags `benchmark-1` to `benchmark-3`). Run this one with the same settings (5 rounds,
  seed 874034, GPT-4o judge, faster-whisper small) so the two compare directly. This
  version's own runs go in `results/` here; the first, compared with alfred-sts, is
  `results/2026-11-05/report.md` (tag `benchmark-1`).
- **Idle noise:** in normal use the wake word filters background noise; here every clip reaches
  Alfred, so "false turn-taking" scores are stricter than real life.
