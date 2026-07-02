# Cost estimate — OpenRouter run

## Measured (pilot, 2026-07)
2-model pilot (`deepseek/deepseek-v3.2` + `meta-llama/llama-4-scout`), gold
`--n 40` (173 items) × 2 conditions × 2 regimes = **1,384 runs**:

- **Spent: $0.373** (credits 32.512 → 32.139) → **~$0.00027/run** average.
- Backed-out token volume ≈ **~680 input + ~820 output tokens/run** (avg over
  the `none` 1-call and `find_iri` multi-call+reasoning mix; DeepSeek loops the
  tool, inflating output).

This ran **higher** than the first a-priori estimate — reasoning + tool-looping
tokens dominate. Calibrated per-model projection below uses that token volume ×
each model's live price.

## Projected — full run, per model (692 runs = 173 × 2 cond × 2 regime)
| Model | in/out $/M | projected $ | note |
|---|---|---|---|
| deepseek/deepseek-v3.2 | 0.23 / 0.34 | ~0.30 | measured (part of pilot) |
| meta-llama/llama-4-scout | 0.10 / 0.30 | ~0.07 | measured (part of pilot) |
| **openai/gpt-5.5** | 5.00 / 30.00 | **~19** | **cost driver (~75% of remaining)** |
| google/gemini-3.5-flash | 1.50 / 9.00 | ~5.8 | |
| qwen/qwen3.6-35b-a3b | 0.14 / 1.00 | ~0.6 | |
| openai/gpt-oss-20b | 0.03 / 0.14 | ~0.1 | |

- **4 remaining models ≈ $25** (dominated by gpt-5.5 ~$19).
- **Full 6-model run ≈ $26** ($0.37 already spent on the 2 pilot models).

## Levers
- **gpt-5.5 dominates.** Swapping it for `openai/gpt-5.4-mini` (or `gpt-oss-120b`)
  drops the remaining run to **~$7**. Keep gpt-5.5 only if the frontier-model
  data point is worth ~$19.
- Caveat: gpt-5.5 / gemini may reason more or less than DeepSeek per item, so
  their figures are ±50%. The harness could log real token usage for exact
  numbers on a first small batch.
