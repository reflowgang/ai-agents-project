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

# Week 2: a structured-output extractor, measured

---

## Week 2

**Run conditions.** model: qwen3:4b-instruct | temperature: 0.0 | prompt
version: week02-zero-shot-v1 | served locally | date: 22.09.2026 | scored
on: my own machine (live, not the shipped recording)

### 1. The output contract

The conventions I chose, and why:

- `due_date`, when the message states no date: `null` (Python `None`).
- `due_date`, when the message states only a relative expression (e.g.
  "before the end of the month", "as soon as possible"): also `null` — a
  relative expression is not a calendar date, and the schema/prompt both
  say so explicitly so the model isn't left to guess.
- `quote`, and what "verbatim" means in my scorer: the returned string must
  appear as an exact substring of the original message text (`in`), with no
  lowercasing, stripping, or whitespace normalization. The moment that check
  is relaxed, the field stops measuring whether the model copied and starts
  measuring whether it approximately copied.
- What my scorer does with a record that failed validation: counts it in
  `invalid` and marks it wrong on **every** field, rather than skipping it.

A scorer that skips records it could not parse reports a number that
improves as the model gets worse — fewer valid completions means fewer
chances to be scored wrong, which is the opposite of what an evaluation
should reward.

### 2. Zero-shot, per field

| field           | correct | of |
| --------------- | ------- | -- |
| category        | 7       | 10 |
| urgency         | 9       | 10 |
| due_date        | 7       | 10 |
| quote           | 8       | 10 |
| invalid records | 0       | 10 |

My prediction, written before block 3: examples will help most on
**due_date**, because zero-shot invents plausible dates on messages that
state none (REQ-01, REQ-05, REQ-10). I expect category/urgency to move
little, since they are closed-label decisions less dependent on seeing a
worked example. I expect a real risk that **quote could get worse** if my
few-shot examples show tidied or paraphrased quotes instead of exact spans
— I tried to avoid that by copying every example quote verbatim from its
source.

### 3. Few-shot

Examples chosen, and the job each one does:

| example                                                   | why it is in the block                                                                                            | field it should move                 |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| EX-01 (en, access — badge reader rejected, not blocking) | shows the access-vs-hardware boundary: a physical device whose correct category is still "access", not "hardware" | category                             |
| EX-02 (fr, facilities — elevator stuck, urgent)          | non-English example; demonstrates urgent phrasing and a stated`due_date=null`                                   | urgency, due_date, language coverage |
| EX-03 (de, hardware — broken laptop charger, has a date) | the only example with a real stated date; shows the DD/MM/YYYY → ISO conversion; also non-English                | due_date, category                   |
| EX-04 (en, other/info — intranet search notice)          | demonstrates the rare "info" urgency label and "other" category for pure notices                                  | urgency, category                    |

| field    | zero-shot | few-shot | move |
| -------- | --------- | -------- | ---- |
| category | 7         | 5        | -2   |
| urgency  | 9         | 10       | +1   |
| due_date | 7         | 9        | +2   |
| quote    | 8         | 9        | +1   |

### 4. What got worse

**category**, from 7/10 to 5/10. Looking at the failure lines rather than
the counts: all three original zero-shot errors (REQ-04 access↔facilities,
REQ-08 facilities↔hardware, REQ-09 facilities↔other) persisted **completely
unchanged** — same wrong label, before and after. None of them were fixed.
On top of that, two *new* failures appeared on documents that zero-shot got
right: REQ-01 (access → wrongly billing) and REQ-02 (hardware → wrongly
facilities).

My diagnosis: the example block leans toward "facilities" framing (EX-02,
the elevator) against only one clear "hardware" example (EX-03), and EX-01
itself pairs a physical device with a non-hardware label. Together this
appears to bias the model toward over-using "facilities" as a catch-all for
anything physical or technical, which both failed to fix the pre-existing
boundary confusion and broke two previously-correct calls. Few-shot did not
disappear an error here — it left every old error in place and added new
ones.

### 5. What the examples cost

- extra input tokens per call: 317
- per thousand calls: 317,000 extra input tokens
- estimated euros per thousand calls on the small tier: **0.06 EUR**
  (317 extra input tokens, 0 extra output tokens, against the price list
  dated 2026-08-10). Estimate, not a measurement.

### 6. Ship it or not

I would **not** ship the few-shot variant as it stands. `due_date` (+2) and
`quote` (+1) are genuine, checkable improvements over real zero-shot
failures. But `category` — the field designed to be detectable with no
judgment at all — got measurably worse, with every old error persisting and
two new regressions added, for an extra 0.06 EUR/1000 calls. Ten records is
nowhere near enough to be confident either way, but the fact that not one
category error was fixed, and two more appeared, is a consistent enough
signal that this specific example set is miscalibrated for category rather
than just noisy.

What would change my mind: rebuilding the example block with an explicit
hardware-vs-facilities contrastive pair (e.g. a broken printer or file
server labeled hardware, placed next to the facilities example), and seeing
whether that recovers category without losing the due_date/quote gains —
ideally measured on more than ten records.

### Sensitivity variant

Variant assigned: **role** (self-chosen; no instructor assignment in this
setting). What I changed: prepended "You are a senior service desk
analyst." to the few-shot prompt, nothing else. What moved: category +1
(5→6), urgency -1 (10→9), due_date +0, quote -1 (9→8) — net hits go from
33/40 to 32/40, essentially flat to slightly worse, while input tokens rose
slightly (7281→7415, +134 over 10 calls). Per language: French picked up
one more error (2→3), English and German unchanged.

This matches the file's own prediction: a persona line buys close to
nothing on a task with a closed schema, while still costing tokens on every
call. Nothing here justifies keeping it.

### The gold set

Ten cases written to `artifacts/goldset.json`, tagged by language (en/fr/de).

One thing my scorer cannot currently detect: it cannot distinguish a
correctly formatted date that is simply the *wrong* date from a properly
extracted one, because `due_date` is compared only by string equality — a
date that is right in shape but wrong in value scores identically to a
completely invented one.

### Deferred

- Only the `role` sensitivity variant was measured; `reordered`,
  `no_delimiter`, and `english_only` were not run, since no instructor
  assigned a specific one and time was limited — `role` was chosen as the
  fastest to implement correctly.
