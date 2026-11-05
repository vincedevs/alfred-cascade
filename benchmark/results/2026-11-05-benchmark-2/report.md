# Alfred cascade: Full-duplex benchmark 2, compared with benchmark 1 and alfred-sts benchmark 3

**Date:** 5 November 2026 · **Run:** `runs/201511198/2026-11-05_23-06-14` · **Student:** 201511198

Earlier report: `../2026-11-05/report.md` (cascade benchmark 1). alfred-sts's final run is in its
`benchmark/results/2026-11-02-benchmark-3/report.md` (tag `benchmark-3`).

## The runs

| | What changed | Commit | Seed (clips) |
|---|---|---|---|
| alfred-sts benchmark 3 | GPT-Live 1, speech to speech | `5e0579c` | 874034 |
| Cascade benchmark 1 | Deepgram Flux → GPT-6 Luna (low reasoning) → Cartesia Sonic 3.6 | `e0f1088` | 874034 |
| **Cascade benchmark 2** | carries on after asides; three words to stop him while he speaks; replies start on Flux's early end of turn; reasoning off; 150 ms TTS buffer | `4833d3d` (tag `benchmark-2`) | **874034** |

All three played the same clips, ran on a Raspberry Pi 5 with fd-benchmark 0.1.0 (`106ac8e`) on a MacBook, both on
Wi-Fi, direct feed, 5 rounds (50 trials, all completed), faster-whisper small, judge GPT-4o.

| | alfred-sts B3 | Cascade B1 | Cascade B2 |
|---|---|---|---|
| Link round trip | 8.8 ms | 4.3 ms | 4.2 ms |
| Pi temperature before → after | 47.7 → 48.8 °C | 45.5 → 48.8 °C | 45.0 → 45.5 °C |
| Throttling | none | none | none |

Benchmark 2's changes, in detail:
- **Carrying on after an aside:** a prompt rule, plus `alfred/carry_on.py`, which asks Alfred once to carry on when
  he was cut off mid-answer and his next reply is empty.
- **Backchannels:** while Alfred speaks, it takes three words to stop him (Pipecat's
  `MinWordsUserTurnStartStrategy`), so "mm-hmm" doesn't.
- **Speed:** GPT-6 Luna starts on Flux's predicted end of turn (eager end of turn); reasoning effort `none` instead
  of `low`; the TTS start buffer is 150 ms instead of 300 ms.

Commands, from `fd-benchmark` on the Mac, with the Pi running `launch_assistant.py`:

```bash
.venv/bin/python -m fdbench run --student 201511198 --rounds 5 --seed 874034 --connect direct --where remote --judge-provider none --yes --recordings k
.venv/bin/python -m fdbench score runs/201511198/2026-11-05_23-06-14 --judge-provider openai --judge-model gpt-4o
```

## Summary

The cascade is now competitive with alfred-sts. It beats it on turn-taking and on answering interruptions, and it
stops sooner when talked over; alfred-sts is still faster and better at staying quiet.

Compared with cascade benchmark 1 (same clips):

- **Better:**
  - turn-taking 60% → 100%, missed turns 40% → 0%;
  - latency after an interruption 5.03 s → 1.71 s; interruption reply quality 3.3 → 4.0 out of 5;
  - interruptions yielded to 75% → 100%;
  - v1.5 interruption Respond 20% → 80%, with no unanswered trials (Unknown 80% → 0%);
  - backchannels handled 60% → 100%;
  - turn-taking latency 3.58 s → 2.91 s.
- **Same:** talking to others (40% Resume), background speech (40–60% Resume, within noise), stop time (about 0.9 s).
- **Worse:**
  - false turn-taking 40% → 70% (side talk 0% → 100%, mostly spill-over, see below);
  - pauses: synthetic 0% → 40%, Candor 0% → 40%.

Against alfred-sts benchmark 3:
- **Cascade better:** missed turns (0% against 20%), turn-taking (100% against 80%), v1.5 interruption Respond
  (80% against 40%), stop time (0.86 s against 1.56 s), synthetic pauses (40% against 80%).
- **Same:** yielding to interruptions (100%), backchannels (100%), background speech (40%).
- **alfred-sts better:** latency (1.92 s against 2.91 s; 1.44 s against 1.71 s after an interruption), interruption
  reply quality (4.8 against 4.0), false turn-taking (50% against 70%), Candor pauses (0% against 40%), talking to
  others (60% against 40%).

## Results

Brackets are 95% intervals for cascade benchmark 2.

### Full-Duplex-Bench v1 and turn-taking rates

| Metric | alfred-sts B3 | Cascade B1 | **Cascade B2** | Better |
|---|---|---|---|---|
| TOR, pause handling (synthetic) | 80% (4/5) | 0% (0/5) | **40%** (2/5) [12–77] | lower |
| TOR, pause handling (Candor) | 0% (0/5) | 0% (0/5) | **40%** (2/5) [12–77] | lower |
| TOR, turn-taking (Candor) | 80% (4/5) | 60% (3/5) | **100%** (5/5) [57–100] | higher |
| Missed turn-taking rate | 20% (1/5) | 40% (2/5) | **0%** (0/5) [0–43] | lower |
| False turn-taking rate | 50% (5/10) | 40% (4/10) | **70%** (7/10) [40–89] | lower |
| · side talk | 0% (0/3) | 0% (0/3) | **100%** (3/3) | lower |
| · TV / radio | 100% (3/3) | 100% (3/3) | **67%** (2/3) | lower |
| · background speech | 100% (2/2) | 50% (1/2) | **100%** (2/2) | lower |
| · noise | 0% (0/2) | 0% (0/2) | **0%** (0/2) | lower |
| Latency, turn-taking (median) | 1.92 s | 3.58 s | **2.91 s** (n=5; 2.22–4.57 s) | lower, < 1 s |
| TOR, interruption | 100% (5/5) | 75% (3/4) | **100%** (5/5) [57–100] | higher |
| Judge score, interruption (0–5) | 4.8 | 3.3 | **4.0** | higher |
| Latency, interruption (median) | 1.44 s | 5.03 s | **1.71 s** (n=5; 0.00–3.40 s) | lower |
| Stop time after an interruption (median) | 1.56 s | 0.81 s | **0.86 s** (n=4; 0.48–1.18 s) | lower |

### Full-Duplex-Bench v1.5 (judge: GPT-4o)

| Scenario (expected) | alfred-sts B3 | Cascade B1 | **Cascade B2** |
|---|---|---|---|
| User interruption (**Respond**) | 40% (Resume 60%) | 20% (Unknown 80%) | **80%** (Unknown 0%) |
| User backchannel (**Resume**) | 100% | 60% (Unknown 40%) | **100%** |
| Talking to others (**Resume**) | 60% (Unknown 20%) | 40% (Unknown 40%) | **40%** (Respond 20%, Unknown 20%) |
| Background speech (**Resume**) | 40% (Unknown 20%) | 60% (Unknown 20%) | **40%** (Respond 40%, Unknown 20%) |

Timing in benchmark 2: during a user interruption, Alfred stopped after 1.05 s (median, n=3) and answered 3.67 s
(median, n=5) after it.

**Voice change after an interruption** (3 Respond-labelled clips, too few to interpret): pitch mean Δ −22.9
(p = 0.16), loudness mean Δ −0.7 (p = 0.52), neither significant. UTMOSv2 was not scored.

## What the logs show

**No more falling silent.** Benchmark 1's main weakness is gone: no unanswered v1.5 interruptions, no missed turns.
The carry-on note fired 10 times, and Alfred resumed his answer where he had been cut off. Only 5 empty replies
remain (9 in benchmark 1).

**Backchannels pass.** "Mm-hmm" and "alright" no longer stop Alfred (100% Resume). Waiting for three words barely
slowed stopping: 0.86 s, still about twice as fast as GPT-Live.

**Side talk: mostly spill-over, now counted against him.** Two of the three side-talk failures began while Alfred
was still finishing the previous clip's answer. These clips are about 2 s long, so the short aside no longer stops
him, and his carrying on counts as taking the turn. In `side_talk_005`, "Dad, give me a hand with this math real
quick" cut him off and the carry-on note had him continue his dinner suggestions: right in real use, a failure here.
The third (`side_talk_006`, "Zing, a dog's chewing your shoe") is genuine: Alfred asked "do you mean a dog is chewing
your shoe?". Reasoning `none` may weaken his judgement of whom speech is meant for.

**Pauses: the early start, and the carry-on note misfiring.** Alfred now starts on Flux's prediction that the turn
has ended, and the prediction sometimes falls in a mid-sentence pause. In `pause_candor_215`, he began "It sounds
like you're noticing…" while the speaker was still mid-thought. The speaker went on talking, which cut Alfred off;
his reply was empty, and the carry-on note had him finish his sentence over the speaker. The note can't yet tell the
user continuing their own turn from an aside meant for someone else. `pause_candor_43` was spill-over.

**Spill-over:** 16 of 70 trials began with Alfred still speaking from the clip before (19 in benchmark 1). The 0.00 s
lower bound on interruption latency is one such trial.

**Run health:** Deepgram did not drop out once (4 reconnects in benchmark 1). The run took 26 minutes without pauses.

## Assessment

Benchmark 2 fixes what benchmark 1 found worst: falling silent after being cut off, stopping for backchannels and
slow answers after an interruption. On identical clips the cascade now beats alfred-sts on five measures and ties on
three. alfred-sts keeps its lead on latency (about 1 s), interruption reply quality, and staying quiet through pauses,
side talk and noise. The losses come from two of benchmark 2's own changes (the early start and the carry-on note)
and from spill-over between clips, so they have clear causes.

## Validity and limitations

- **Sample size.** About 5 clips per test (10 for false turn-taking); intervals span 40–60 points for most rates.
  Only large differences are meaningful.
- **Speech-to-text.** faster-whisper small (repository default) instead of the papers' Parakeet-TDT.
- **Wi-Fi.** Round trip 4.2 ms; Wi-Fi jitter can still widen latencies.
- **Benchmark design.** No wake word, so false turn-taking is stricter than real use; one session per round, so
  answers spill into the next clip. Carrying on after an aside makes that spill-over count more often.
- **Run validity.** All 50 trials completed, the Pi was never throttled, and no errors in Alfred's log other than
  the 5 empty replies.

## Possible future work

1. Carry on only after speech that isn't the user continuing their own turn, for example only when the interruption
   was short or named someone else.
2. Make the early start stricter (a higher eager end-of-turn threshold), so it doesn't answer into pauses.
3. Try reasoning `low` again, for better judgement of whom speech is meant for, and measure what it costs in latency.
4. Add a reset hook so the benchmark can use `--reset-cmd`, removing spill-over between clips (both versions).
