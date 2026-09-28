# Decisions


# Week 1: the stack, the first call, and what it costs

---

## Week 1

**Run conditions.** Everything below was produced on:

- machine: Apple M1, 8 GB RAM
- model: qwen3:4b-instruct (small tier), qwen2.5:7b not yet pulled (see Deferred)
- served by: Ollama, one request at a time, locally
- date: 2026-09-16

Every number in this file is meaningless without those four lines, so they
are stated once here and referred to rather than repeated.

### 1. Machine and model set

I am running the required model set minus `qwen2.5:7b`: I have
`qwen3:4b-instruct` and `nomic-embed-text` pulled, but not `qwen2.5:7b` yet
(4.7 GB), so `00_preflight.py` currently reports one `[FAIL]` line for that
reason. I will pull it before it is actually needed (week 4), and before
then if bandwidth allows. On 8 GB of RAM the two text models fit one at a
time, not together, per the syllabus note, which matters for any workflow
that would want both loaded at once. The optional vision model
(`qwen3-vl:4b`) is also deferred, to be pulled ahead of week 9.

### 2. The first call

|                   |                                                              |
| ----------------- | ------------------------------------------------------------ |
| finish reason     | stop                                                         |
| prompt tokens     | 24                                                           |
| completion tokens | 45                                                           |
| elapsed           | 21.58 s (this call doubled as a cold start — see section 4) |

One sentence on the finish reason: `stop` means the model ended the answer
deliberately; if it had come back as `length` instead, that would mean the
answer was cut off by `max_tokens`, and my program would need to treat the
output as incomplete — either re-run with a higher `max_tokens` or flag the
response as truncated rather than handing it to the user as final.

### 3. Variance

| cell                | distinct (recording) | distinct (mine) | median latency (mine) |
| ------------------- | -------------------- | --------------- | --------------------- |
| closed_short, t=0.0 | 1/12                 | 1/6             | 0.20 s                |
| closed_short, t=1.0 | 1/12                 | 1/6             | 0.19 s                |
| open_list, t=0.0    | 1/12                 | 1/6             | 2.42 s                |
| open_list, t=1.0    | 11/12                | 6/6             | 2.16 s                |

(Recording's own median latencies were 0.17 s / 0.17 s / 1.10 s / 1.14 s —
higher than mine on the open cells, consistent with different hardware.)

Which cell still returns a single answer at temperature 1.0, and why that
one: `closed_short` ("capital of Luxembourg, one word") stays at 1 distinct
answer even at t=1.0, in both the recording and on my machine. The prompt's
answer space is essentially a single short token sequence, so the model's
probability mass is so concentrated on it that sampling noise almost never
picks anything else. This is not "temperature didn't work" — temperature
behaves normally, the space it is sampling from is just nearly a single
point.

Which cells a test asserting exact string equality would pass on, and what
that tells me about testing this system: it would reliably pass on
`closed_short` at both temperatures, and would fail unpredictably on
`open_list` at t=1.0 (6/6 distinct). This tells me exact-match testing only
makes sense for narrow, closed-form answers; open-ended generations need a
different kind of check (e.g. semantic comparison), not string equality.

**The sentence that carries into week 10.** Repeatability is a property of
how narrow the prompt's answer space is, not of the model or the
temperature alone: closed-form prompts return the same answer even at
temperature 1.0, while open-ended prompts diverge, so evaluation has to
choose its method (exact match vs. something semantic) based on the shape
of the expected answer, not apply one test strategy everywhere.

### 4. The cold start

- cold call: 4.38 s
- warm call: 0.20 s
- ratio: ~22x

What this implies for a system that uses more than one model, and what I
will do about it: if a single user-facing request alternates between two
models (e.g. SMALL and LARGE), each switch likely forces an unload/reload
cycle costing multiple seconds — unacceptable mid-request latency. I will
avoid interleaving models within one request: route a given request to one
model for its whole lifetime, and if a switch is unavoidable, pre-warm the
next model ahead of time rather than paying the cold start inline.

### 5. Cost, estimated

A 200-case golden set, at the token cost of my long case (38 in / 200 out):

|            | one run    | nightly for the semester (98 nights) |
| ---------- | ---------- | ------------------------------------ |
| small tier | 0.0335 EUR | ~3.28 EUR                            |
| large tier | 2.4912 EUR | ~244.14 EUR                          |

Estimates against the price list dated 2026-08-10, not measurements.
Running locally, my actual monetary cost was zero.

Which tier I would run nightly, which I would run before a release, and why
not the same one for both: nightly on the **small** tier — cheap enough
(~3 EUR for the whole semester) to run unconditionally every night for
regression-catching. The **large** tier only before a release, since its
~75x cost premium is only justified for a less frequent, higher-fidelity
check, not for routine nightly runs.

### Deferred

- `qwen2.5:7b` not pulled yet — preflight's one `[FAIL]` line is known and
  accepted; will pull before week 4.
- Optional vision model `qwen3-vl:4b` not pulled — deferred until closer to
  week 9.
- Repository not yet pushed to a remote (GitHub/GitLab) — deferred within
  this session; still need to do before group registration.
