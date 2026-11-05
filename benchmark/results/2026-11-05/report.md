# Alfred cascade: Full-duplex benchmark 1, compared with alfred-sts benchmark 3

**Date:** 5 November 2026 · **Run:** `runs/201511198/2026-11-05_21-46-19` · **Student:** 201511198

The same benchmark as alfred-sts's final run (its `benchmark/results/2026-11-02-benchmark-3/report.md`, tag
`benchmark-3`), with the same clips, so the two compare directly. Only the voice pipeline differs:

| | alfred-sts (benchmark 3) | **alfred-cascade (this run)** |
|---|---|---|
| Voice pipeline | GPT-Live 1, speech to speech | Deepgram Flux → GPT-6 Luna (low reasoning) → Cartesia Sonic 3.6 |
| Turn detection | GPT-Live | Deepgram Flux |
| Commit | `5e0579c` | `e0f1088` (tag `benchmark-1`) |
| Seed (clips) | 874034 | 874034, the same clips |
| Link round trip | 8.8 ms | 4.3 ms |
| Pi temperature before → after | 47.7 → 48.8 °C | 45.5 → 48.8 °C |
| Throttling | none (`0x0`) | none (`0x0`) |

Both ran the same way: Alfred on a Raspberry Pi 5, fd-benchmark 0.1.0 (`106ac8e`) on a MacBook, both on Wi-Fi,
direct feed (`--where remote`), 5 rounds (50 trials, all completed), faster-whisper small, judge GPT-4o.

Commands, from `fd-benchmark` on the Mac, with the Pi running `launch_assistant.py`:

```bash
.venv/bin/python -m fdbench run --student 201511198 --rounds 5 --seed 874034 --connect direct --where remote --judge-provider none --yes --recordings k
.venv/bin/python -m fdbench score runs/201511198/2026-11-05_21-46-19 --judge-provider openai --judge-model gpt-4o
```

## Summary

alfred-sts is the better voice assistant on this benchmark. The cascade wins two timing measures and loses almost
everything about how the conversation flows.

- **Cascade better:**
  - stops sooner when talked over: 0.7–0.8 s against 1.6 s;
  - synthetic pauses: took the turn in 0% against 80% (alfred-sts's failures were mostly spill-over);
  - false turn-taking 50% → 40% (within noise; both answer TV/radio every time).
- **Cascade worse:**
  - reply latency 1.92 s → 3.58 s, and 1.44 s → 5.03 s after an interruption;
  - interruption reply quality 4.8 → 3.3 out of 5;
  - missed turns 20% → 40%; interruptions yielded to 100% → 75%;
  - backchannels handled (Resume) 100% → 60%;
  - many v1.5 trials with no reply at all (Unknown 40–80%).
- **Same:** natural pauses (0% took the turn in both), TV/radio (100% answered in both).

## Results

Brackets are 95% intervals for the cascade.

### Full-Duplex-Bench v1 and turn-taking rates

| Metric | alfred-sts | **cascade** | Better |
|---|---|---|---|
| TOR, pause handling (synthetic) | 80% (4/5) | **0%** (0/5) [0–43] | lower |
| TOR, pause handling (Candor) | 0% (0/5) | **0%** (0/5) [0–43] | lower |
| TOR, turn-taking (Candor) | 80% (4/5) | **60%** (3/5) [23–88] | higher |
| Missed turn-taking rate | 20% (1/5) | **40%** (2/5) [12–77] | lower |
| False turn-taking rate | 50% (5/10) | **40%** (4/10) [17–69] | lower |
| · side talk | 0% (0/3) | **0%** (0/3) | lower |
| · TV / radio | 100% (3/3) | **100%** (3/3) | lower |
| · background speech | 100% (2/2) | **50%** (1/2) | lower |
| · noise | 0% (0/2) | **0%** (0/2) | lower |
| Latency, turn-taking (median) | 1.92 s | **3.58 s** (n=3; 2.54–4.67 s) | lower, < 1 s |
| TOR, interruption | 100% (5/5) | **75%** (3/4) [30–95] | higher |
| Judge score, interruption (0–5) | 4.8 | **3.3** (n=3) | higher |
| Latency, interruption (median) | 1.44 s | **5.03 s** (n=3; 1.78–6.29 s) | lower |
| Stop time after an interruption (median) | 1.56 s | **0.81 s** (n=2) | lower |

One v1 interruption clip was not played, because Alfred never answered its first question.

### Full-Duplex-Bench v1.5 (judge: GPT-4o)

| Scenario (expected) | alfred-sts | **cascade** |
|---|---|---|
| User interruption (**Respond**) | 40% (Resume 60%) | **20%** (Unknown 80%) |
| User backchannel (**Resume**) | 100% | **60%** (Unknown 40%) |
| Talking to others (**Resume**) | 60% (Unknown 20%) | **40%** (Respond 20%, Unknown 40%) |
| Background speech (**Resume**) | 40% (Unknown 20%) | **60%** (Respond 20%, Unknown 20%) |

"Unknown" means the judge had no reply to label: Alfred stopped and did not come back, or never answered.
Stop time during a v1.5 interruption: 0.67 s (median, n=3; 0.60–0.76 s), against alfred-sts's 1.56 s.

## What the logs show

**Cut off, then silent (the main weakness).** When someone speaks over Alfred, Flux stops him at once, whoever is
speaking. If the words weren't meant for him ("Your coffee's getting cold", "Ralph, pizza's here, grab plates",
"Laura, lights flickered again"), GPT-6 Luna rightly decides not to answer and returns an empty reply. But the
answer he was giving is already cut off, so he falls silent. This happened 9 times, and it drives the low Resume and
high Unknown rates. GPT-Live hears the audio itself, so it simply keeps talking through such lines.

**Speech recognition dropped out four times.** Deepgram's connection closed with "keepalive ping timeout" and came
back within about 2 s each time. Two round-2 trials that got no answer (`v1_interrupt_15`, `v15_background_83`)
fall in these gaps.

**Slow look-ups spill over.** "Find vegetarian restaurants open now" took 19 s with web search, so the answer came
after its clip had ended and played over the next one. 19 of the 70 trials began with Alfred still speaking from
the clip before. alfred-sts had the same spill-over (one session per round), so the comparison stays fair.

**Latency is the cascade's built-in cost.** Speech to text, the model and text to speech run one after another:
3.6 s median, against GPT-Live's 1.9 s. After an interruption, the new request starts from scratch: 5.0 s.

**Fast stops.** Flux notices overlapping speech in about 0.7 s and Pipecat flushes the speaker, about twice as fast
as GPT-Live, which handles interruptions on OpenAI's side.

**Credits held out.** Misses are spread over all five rounds and round 5 answered normally, so Cartesia and
Deepgram credits never ran out mid-run.

## Assessment

On identical clips, the cascade beats alfred-sts on two measures (stop time, synthetic pauses) and is roughly equal
on false turn-taking. It loses on latency, interruption quality, backchannels and missed replies, which are what
make a voice assistant feel interactive. This matches how it felt in use on the Pi ("robotic", less interactive).
alfred-sts remains the version to present.

## Validity and limitations

- **Sample size.** About 5 clips per test (10 for false turn-taking); intervals span 40–60 points for most rates.
  Only large differences are meaningful.
- **Speech-to-text.** faster-whisper small (repository default) instead of the papers' Parakeet-TDT.
- **Wi-Fi.** Round trip 4.3 ms; Wi-Fi jitter can still widen latencies.
- **Benchmark design.** No wake word, so false turn-taking is stricter than real use; one session per round, so
  answers spill into the next clip (the same for both versions).
- **A pause mid-run.** fdbench paused for 22 minutes on the Mac (21:54–22:16, between two trials in round 2),
  most likely the Mac sleeping. Alfred sat idle and the run then continued normally.
- **Run validity.** All 50 trials completed, the Pi was never throttled; the only errors in Alfred's log are the 9
  empty replies and the 4 Deepgram reconnects above.

## Possible future work

1. Stop only when the overlap is a few words long, so "mm-hmm" and short asides don't cut Alfred off.
2. When an interruption gets an empty reply, resume the answer that was cut off.
3. Eager end-of-turn detection and minimal reasoning, to bring latency down.
4. Add a reset hook so the benchmark can use `--reset-cmd`, removing spill-over between clips (both versions).
