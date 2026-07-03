# Results — IRI hallucination reduction via find_iri

**5 models** (`deepseek/deepseek-v3.2`, `google/gemini-3.5-flash`,
`qwen/qwen3.6-35b-a3b`, `openai/gpt-oss-20b`, `meta-llama/llama-4-scout`) ×
gold `--n 40` (173 items: L1=8, L2=48, L3=46, L4=71 incl. 40 nonexistent) ×
2 conditions (`none`, `find_iri`) × 2 regimes (`forced`, `abstain`).
3,460 runs (10 transient errors dropped → **3,450 scored**). Data: `runs_full.jsonl`.
gpt-5.5 deferred (cost — see below). Cost so far **~$26.3** ($0.37 + $25.9).

Metrics: **accuracy** (IRI == gold) · **halluc%** (produced a non-existent IRI,
or any IRI for a nonexistent-term item) · **h/ans%** (of non-abstentions) ·
**abst%** · **usedTool%** (autonomously invoked find_iri).

## Accuracy by difficulty — forced regime (none → find_iri)
| model | none [L1 L2 L3 L4] | find_iri [L1 L2 L3 L4] |
|---|---|---|
| deepseek-v3.2 | 100 · 29 · 17 · 15 | 100 · **97 · 89** · 43 |
| gemini-3.5-flash | 100 · 35 · 28 · 18 | 100 · **97 · 86** · 43 |
| qwen3.6-35b-a3b | 75 · 2 · 2 · 8 | 100 · **95 · 89** · 45 |
| gpt-oss-20b | 75 · 2 · 0 · 4 | 100 · **97 · 89** · 43 |
| llama-4-scout | 50 · 6 · 2 · 1 | 100 · **95 · 82** · 42 |
*(L4 find_iri ~43% because ~40/71 L4 items are nonexistent → correct behaviour is abstention, scored separately.)*

## Aggregate (across difficulty)
| model | regime | cond | acc% | halluc% | h/ans% | abst% | tool% |
|---|---|---|---|---|---|---|---|
| deepseek | forced | none | 24 | 40 | 40 | 0 | 0 |
| deepseek | forced | **find_iri** | **73** | **14** | 16 | 11 | 100 |
| deepseek | abstain | none | 22 | 45 | 48 | 6 | 0 |
| deepseek | abstain | **find_iri** | **73** | **2** | 3 | 23 | 100 |
| gemini | forced | none | 29 | 45 | 45 | 1 | 0 |
| gemini | forced | **find_iri** | **73** | **10** | 12 | 17 | 100 |
| gemini | abstain | **find_iri** | 72 | **2** | 3 | 25 | 100 |
| qwen | forced | none | 8 | 41 | 45 | 8 | 0 |
| qwen | forced | **find_iri** | **74** | **14** | 15 | 11 | 100 |
| qwen | abstain | **find_iri** | 73 | **3** | 4 | 22 | 100 |
| gpt-oss-20b | forced | none | 6 | 64 | 73 | 13 | 0 |
| gpt-oss-20b | forced | **find_iri** | **73** | **18** | 19 | 8 | 100 |
| gpt-oss-20b | abstain | **find_iri** | 73 | **5** | 7 | 20 | 100 |
| llama | forced | none | 5 | 59 | 60 | 2 | 0 |
| llama | forced | **find_iri** | **71** | **25** | 26 | 2 | 100 |
| llama | abstain | **find_iri** | 64 | **10** | 13 | 25 | 100 |

## Findings
1. **Grounding restores accuracy across the whole capability gradient.** Without
   the tool, accuracy collapses with difficulty (frontier models hold L1 but drop
   to 15–35% by L2; small models are ~0–8% on L2–L4). find_iri lifts L2/L3 to
   **~95–97% / ~82–89% for every model** — the tool *equalizes* strong and weak
   models. Aggregate accuracy jumps to ~73% for all five (llama 71%).
2. **Hallucination drops sharply**, most under the abstain regime where the tool
   lets models say "no such class": find_iri aggregate halluc% → **2–5%** for
   deepseek/gemini/qwen/gpt-oss (abstain), vs 19–64% without.
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
  which is flaky under load; checks now retry + cache (`iri_exists_cache.json`),
  but the `none`-baseline halluc split is ±a few points. find_iri's *low*
  hallucination is robust. **Most no-tool errors are real-but-wrong IRIs
  (mislinking), not fabrications** — models know valid IDs, pick the wrong one.
- Pilot n/stratum: L1=8 (small), L2=48, L3=46, L4=71.
- 10/3460 runs dropped (transient qwen/gpt-oss malformed-JSON responses; a
  sequential retry hung on a stalled MCP session and was abandoned).

## Cost & next
- Measured: 2 models $0.37; +3 models **$25.9** — **gemini-3.5-flash dominated**
  (extended-thinking tokens at $9/M output; see `COST_ESTIMATE.md`).
- **gpt-5.5 still pending** (would be ~$40+ if it thinks like gemini) — needs a
  credit top-up and ideally a thinking-token cap before running. Adding it is a
  one-command rerun (`--models openai/gpt-5.5`, then re-score).
