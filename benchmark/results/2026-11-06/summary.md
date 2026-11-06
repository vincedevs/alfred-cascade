# Full-Duplex Voice Agent on a Raspberry Pi

Student 201511198 · run 2026-11-06_19-44-33 · 2026-11-06

## 5 · Evaluation & Benchmark

| Metric | Definition | Target | Mean | p95 | n |
|---|---|---|---|---|---|
| E2E latency | end of speech → audio out | < 4.00 s | 2.26 s | 5.14 s | 28 |
| ASR final | end of speech → transcript | < 0.80 s | 0.03 s | 0.00 s | 30 |
| LLM TTFT | transcript → first token | < 1.50 s | 2.13 s | 6.03 s | 29 |
| TTS TTFB | text → first audio chunk | < 0.60 s | 0.28 s | 0.36 s | 28 |
| Barge-in | speech → playback stopped | < 0.80 s | 1.16 s | 1.83 s | 9 |
| WER | ASR accuracy on test set | < 15 % | 5.9 % | — | 30 |

- One clock: the assistant machine's monotonic clock; every stage timestamped (VAD end → ASR final → LLM 1st token → TTS 1st audio → speaker)
- E2E = speaker − VAD end; barge-in = stop − speech start
- 10 phrases × 3 runs; mean + p95; JSON log per run: stage_latency.json (per turn: trials/<id>/stages.jsonl)

## Results (deck 2)

Latency chart: latency_chart.svg

- RPi model / RAM: Raspberry Pi 5 Model B Rev 1.1 · 8 GB
- Cooling: official Raspberry Pi 5 case (built-in fan) · peak 51 °C · throttled never
- Network: cloud: Wi-Fi · benchmark link: Wi-Fi, 7 ms round trip
- Date: 2026-11-06
- Model versions: harness pipecat 1.15.0, ASR Deepgram Flux (flux-general-en), LLM gpt-6-luna, reasoning none, TTS Cartesia sonic-3.6 (voice Archie), VAD Deepgram Flux turn detection

## 3 · Context engineering: context size per run

| Context size | Median | Max | First → last call |
|---|---|---|---|
| Prompt tokens per LLM call (estimate, usage) | 5606 | 16728 | 5599 → 11906 |
| Messages in context | 130 | 256 | 1 → 256 |
