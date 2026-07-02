# Pilot results — IRI hallucination reduction via find_iri

**Pilot run (2026-07):** 2 models (`deepseek/deepseek-v3.2`, `meta-llama/llama-4-scout`)
× gold `--n 40` (173 items: L1=8, L2=48, L3=46, L4=71 incl. 40 nonexistent)
× 2 conditions (`none`, `find_iri`) × 2 regimes (`forced`, `abstain`) = 1,384 runs,
0 errors, **$0.373**. Data: `runs_pilot.jsonl`. (4 more models pending — see below.)

Metrics: **accuracy** (produced IRI == gold), **halluc%** (produced a
non-existent IRI, or any IRI for a nonexistent-term item), **h/ans%** (of
non-abstentions), **abst%**, **usedTool%** (autonomously invoked find_iri).

## Accuracy by difficulty (none → find_iri)
| | | L1 | L2 | L3 | L4 |
|---|---|---|---|---|---|
| deepseek | none | 100 | 29 | 17 | 15 |
| deepseek | **find_iri** | 100 | **97** | **89** | 43 |
| llama-4-scout | none | 50 | 6 | 2 | 1 |
| llama-4-scout | **find_iri** | 100 | **95** | **82** | 42 |
*(forced regime; L4 find_iri "accuracy" is lower because ~40/71 L4 items are nonexistent → correct behaviour is abstention, counted separately.)*

## Aggregate (across difficulty)
| model | regime | cond | acc% | halluc% | h/ans% | abst% | usedTool% |
|---|---|---|---|---|---|---|---|
| deepseek | forced | none | 24 | 25 | 25 | 0 | 0 |
| deepseek | forced | **find_iri** | **73** | **12** | 14 | 11 | 100 |
| deepseek | abstain | none | 22 | 19 | 20 | 6 | 0 |
| deepseek | abstain | **find_iri** | **73** | **1** | 1 | 23 | 100 |
| llama | forced | none | 5 | 31 | 32 | 2 | 0 |
| llama | forced | **find_iri** | **71** | **24** | 25 | 2 | 100 |
| llama | abstain | none | 5 | 28 | 30 | 6 | 0 |
| llama | abstain | **find_iri** | **64** | **8** | 11 | 25 | 100 |

## Findings
1. **Grounding restores accuracy.** Without the tool, accuracy collapses with
   difficulty (deepseek 100→29→17→15; llama 50→6→2→1). find_iri lifts L1–L3 to
   ~82–97% for both models. Aggregate accuracy: deepseek 24→73%, llama 5→71%.
2. **Hallucination drops sharply, most under the abstain regime.** deepseek
   19→**1%**, llama 28→**8%** (abstain). In the forced regime the drop is smaller
   (deepseek 25→12%, llama 31→24%) because the model must answer.
3. **The tool enables honest abstention.** find_iri's "could not resolve" lets
   models abstain on nonexistent terms instead of fabricating (abst% on find_iri:
   deepseek 23%, llama 25% in abstain regime, vs ~6% without).
4. **The weaker model benefits more** (llama's no-tool baseline is near-zero) —
   BUT it retains higher residual hallucination *with* the tool (24% forced),
   because **llama-4-scout garbles IRIs when copying tool output** (e.g.
   `purlibrary.org/obo/GO9986`) — a weak-model transcription failure, not a
   grounding failure.
5. **Autonomous tool use = 100%** — every time find_iri was available (unhinted),
   both models chose to call it.

## Next run (ready to fire — paused 2026-07, awaiting internet)
Remaining 3 models (gpt-5.5 deferred for cost — rerun later, it's cheap to add).
On the laptop, online:
```bash
cd ~/claude-projects/aberowl2/experiments/iri_hallucination
source ~/.local/bin/export_openrouter_key            # sets OPENROUTER_API_KEY
setsid bash -c 'uv run --extra test --project ../.. python harness.py \
  --gold gold.jsonl --out runs_rest.jsonl \
  --models google/gemini-3.5-flash qwen/qwen3.6-35b-a3b openai/gpt-oss-20b \
  > rest.log 2>&1' &                                 # detached (~$6.5, ~15-25 min)
# when done:
cat runs_pilot.jsonl runs_rest.jsonl > runs_full.jsonl
uv run --extra test --project ../.. python score.py --runs runs_full.jsonl --by-difficulty
```
Then optionally add `openai/gpt-5.5` (~$19) for the frontier data point.

## Caveats
- Pilot n per stratum: L1=8 (small), L2=48, L3=46, L4=71.
- deepseek loops find_iri to the turn cap on hard/nonexistent terms (cost/latency).
- L4 "accuracy" conflates obscure-real (resolvable) with nonexistent (abstain-correct);
  read halluc%/abst% for the nonexistent behaviour.
