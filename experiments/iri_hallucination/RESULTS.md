# Results — IRI hallucination reduction via find_iri

*Regenerated 2026-07-16 with `score.py` from `runs_full.jsonl`. Full scorer
output saved in `score_aggregate.txt` and `score_by_difficulty.txt`.*

**5 models** (`deepseek/deepseek-v3.2`, `google/gemini-3.5-flash`,
`qwen/qwen3.6-35b-a3b`, `openai/gpt-oss-20b`, `meta-llama/llama-4-scout`) ×
gold `--n 40` (173 items: L1=8, L2=48, L3=46, L4=71 incl. 40 nonexistent) ×
2 conditions (`none`, `find_iri`) × 2 regimes (`forced`, `abstain`).
3,460 runs (10 transient errors dropped → **3,450 scored**; a few cells have
n<173, noted in the aggregate table). gpt-5.5 deferred (cost — see below).
Cost so far **~$26.3** ($0.37 + $25.9).

Metrics: **accuracy** (IRI == gold) · **halluc%** (produced a non-existent IRI,
or any IRI for a nonexistent-term item) · **h/ans%** (of non-abstentions) ·
**abst%** · **usedTool%** (autonomously invoked find_iri).

## Accuracy by difficulty — forced regime (none → find_iri)
| model | none [L1 L2 L3 L4] | find_iri [L1 L2 L3 L4] |
|---|---|---|
| deepseek-v3.2 | 100 · 29 · 17 · 15 | 100 · **98 · 89** · 44 |
| gemini-3.5-flash | 100 · 35 · 28 · 18 | 100 · **98 · 87** · 44 |
| qwen3.6-35b-a3b | 75 · 2 · 2 · 8 | 100 · **96 · 89** · 46 |
| gpt-oss-20b | 75 · 2 · 0 · 4 | 100 · **98 · 89** · 44 |
| llama-4-scout | 50 · 6 · 2 · 1 | 100 · **96 · 83** · 42 |
*(L4 find_iri ~44% because ~40/71 L4 items are nonexistent → correct behaviour is abstention, scored separately.)*

## Aggregate (across difficulty)
| model | regime | cond | acc% | halluc% | h/ans% | abst% | tool% | n |
|---|---|---|---|---|---|---|---|---|
| deepseek | forced | none | 24 | 40 | 40 | 0 | 0 | 173 |
| deepseek | forced | **find_iri** | **73** | **14** | 16 | 11 | 100 | 173 |
| deepseek | abstain | none | 22 | 45 | 48 | 6 | 0 | 173 |
| deepseek | abstain | **find_iri** | **73** | **2** | 3 | 23 | 100 | 173 |
| gemini | forced | none | 29 | 45 | 45 | 1 | 0 | 173 |
| gemini | forced | **find_iri** | **73** | **10** | 12 | 17 | 100 | 173 |
| gemini | abstain | none | 29 | 28 | 34 | 16 | 0 | 173 |
| gemini | abstain | **find_iri** | 72 | **2** | 3 | 25 | 100 | 173 |
| qwen | forced | none | 8 | 41 | 45 | 8 | 0 | 173 |
| qwen | forced | **find_iri** | **74** | **14** | 15 | 11 | 100 | 170 |
| qwen | abstain | none | 8 | 19 | 33 | 44 | 0 | 172 |
| qwen | abstain | **find_iri** | 73 | **3** | 4 | 22 | 100 | 173 |
| gpt-oss-20b | forced | none | 6 | 64 | 73 | 13 | 0 | 170 |
| gpt-oss-20b | forced | **find_iri** | **73** | **18** | 19 | 8 | 100 | 173 |
| gpt-oss-20b | abstain | none | 6 | 29 | 61 | 52 | 0 | 170 |
| gpt-oss-20b | abstain | **find_iri** | 73 | **5** | 7 | 20 | 100 | 173 |
| llama | forced | none | 5 | 59 | 60 | 2 | 0 | 173 |
| llama | forced | **find_iri** | **71** | **25** | 26 | 2 | 100 | 173 |
| llama | abstain | none | 5 | 54 | 57 | 6 | 0 | 173 |
| llama | abstain | **find_iri** | 64 | **10** | 13 | 25 | 100 | 173 |

## Findings
1. **Grounding restores accuracy across the whole capability gradient.** Without
   the tool, accuracy collapses with difficulty (frontier models hold L1 but drop
   to 15–35% by L2; small models are ~0–8% on L2–L4). find_iri lifts L2/L3 to
   **~96–98% / ~83–89% for every model** — the tool *equalizes* strong and weak
   models. Aggregate accuracy jumps to ~73% for all five (llama 71%).
2. **Hallucination drops sharply**, most under the abstain regime where the tool
   lets models say "no such class": find_iri aggregate halluc% → **2–5%** for
   deepseek/gemini/qwen/gpt-oss (abstain), vs 19–54% without (abstain, `none`).
   Under the forced regime it falls from 40–64% to 10–18% (llama 25%).
3. **Autonomous tool use = 100%** — every model called find_iri whenever it was
   available (unhinted). Tool *selection* was never the bottleneck here.
4. **Weak model, residual failure:** llama-4-scout keeps ~25% hallucination even
   *with* the tool (forced) — it **garbles IRIs when copying tool output**
   (`purlibrary.org/obo/GO9986`), a transcription failure, not a grounding one.
5. **Smaller models self-abstain more** when allowed (gpt-oss/qwen `none` abstain
   regime: 52%/44% abstention) — they hedge rather than fabricate; the frontier
   models fabricate more confidently without the tool.

## Caveats
- `halluc%` vs `valid_wrong` split relies on beta's `getClass` existence oracle,
  which is flaky under load; checks retry + cache (`iri_exists_cache.json`),
  but the `none`-baseline halluc split is ±a few points. find_iri's *low*
  hallucination is robust. **Most no-tool errors are real-but-wrong IRIs
  (mislinking), not fabrications** — models know valid IDs, pick the wrong one.
- Accuracy (exact match to gold) is deterministic from `runs_full.jsonl` + gold,
  so those numbers are reproducible; a few cells have n<173 from dropped
  transient-error runs (gpt-oss `none` n=170, qwen `abstain none` n=172,
  qwen `forced find_iri` n=170).
- Pilot n/stratum: L1=8 (small), L2=48, L3=46, L4=71.
- 10/3460 runs dropped (transient qwen/gpt-oss malformed-JSON responses; a
  sequential retry hung on a stalled MCP session and was abandoned).

## Cost & next
- Measured: 2 models $0.37; +3 models **$25.9** — **gemini-3.5-flash dominated**
  (extended-thinking tokens at $9/M output; see `COST_ESTIMATE.md`).
- **gpt-5.5 DROPPED** (not run) — would be ~$40+ (reasoning tokens), not worth it.
  The 5 models already show the effect cleanly across the capability gradient.
  If ever wanted, it's a one-command add (`--models openai/gpt-5.5`, then re-score).

---

# Phase 0 corrections (2026-08-19)

Re-scored from the saved runs. No new model calls. Reproduce with:

```bash
python build_exists_map.py --runs runs_full.jsonl --out iri_exists_resolved.json
python dedup_gold.py                       # -> gold_dedup.jsonl, replicates.json
python score.py --runs runs_full.jsonl --gold gold_dedup.jsonl [--by-stratum]
```

Two defects moved the numbers: the existence oracle scored failed checks as
"does not exist" (77.2% false-negative rate), and 11 duplicate gold rows
double-weighted 199 runs. Scoring now uses 162 unique items over 3,231 runs.

## Corrected Table 1 (forced regime)

| model | accuracy none -> tool | published | hallucination none -> tool | published | valid-wrong (none) |
|---|---|---|---|---|---|
| Gemini-3.5-flash | 27.2 -> 72.2 | 29/73 | **25.9 -> 9.3** | 45/10 | 46.3 |
| DeepSeek-V3.2 | 20.4 -> 72.8 | 24/73 | **26.1 -> 13.0** | 40/14 | 53.4 |
| Llama-4-Scout | 4.9 -> 69.8 | 5/71 | **33.3 -> 25.3** | 59/25 | 60.5 |
| Qwen3.6-35B | 6.8 -> 73.6 | 8/74 | **24.7 -> 13.2** | 41/14 | 59.9 |
| GPT-OSS-20B | 5.0 -> 72.8 | 6/73 | **35.6 -> 16.7** | 64/18 | 46.2 |

Accuracy barely moves. Hallucination without the tool falls from 40-64% to
24.7-35.6%, so the published column overstates it roughly twofold.

**The manuscript's disputed sentence is correct.** Valid-but-wrong identifiers
(46.2-60.5%) exceed fabricated ones (24.7-35.6%) for every model, so
"most no-tool errors are valid IRIs applied to the wrong class" holds, and the
reviewer's proposed replacement ("hallucinated identifiers dominate") does not.
Phrase it as item shares, not "most errors": with abstentions counted, the
valid-wrong share falls below half for some models.

## Stratum split (W5, W10)

Splitting the 162 items into 122 real classes and 40 constructed-nonexistent
terms shows the aggregate hides two different measurements:

| | accuracy none -> tool | hallucination none -> tool |
|---|---|---|
| real classes (n=122) | 27.0-36.1 -> 95.9-96.7 | 1.7-2.5 -> 0.0 |
| null-gold (n=40) | 0.0 -> 0.0 (impossible by construction) | 97.5-100 -> 37.5-52.5 |

The aggregate ceiling is **75.3%** (122/162), because a null-gold item can never
score correct. Reported against that ceiling the tool reaches 95.9-96.7% on real
classes, which is stronger than the 73% headline suggests. Conversely the large
no-tool hallucination rates come almost entirely from the null-gold stratum,
where the forced regime forbids abstention and any produced IRI counts as
fabricated; that measures instruction compliance, not identifier knowledge.

**The abstract's "as high as 64%" is not defensible.** The corrected all-items
maximum is 35.6% and the real-class maximum is 2.5%.

## Determinism (W7)

The 11 duplicate gold rows produced 239 identical-prompt pairs, the only repeat
measurement in the study. Temperature 0 is not deterministic here:

| condition | pairs | exact-IRI agreement | correctness agreement |
|---|---|---|---|
| no tool | 119 | 60.5% | 89.1% |
| `find_iri` | 120 | 98.3% | 99.2% |

Run-to-run noise is therefore concentrated in the arm without the tool. All
pairs sit within a single harness invocation, so they bound run-to-run variation
but say nothing about provider drift over time.

## Still to disclose

- 10 of 3,460 API calls returned a non-JSON body and were excluded;
  `runs_full.jsonl` is by construction the error-free subset (W9).
- The harness advertised each condition's tools but did not enforce them:
  7/1727 `find_iri` runs (0.4%) executed an unavailable tool.
- Provider routing was unpinned and unlogged, so these runs cannot be
  attributed to a serving endpoint or quantization retrospectively.
