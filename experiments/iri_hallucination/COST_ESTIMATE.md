# Cost estimate — OpenRouter run

Live OpenRouter pricing (fetched 2026-07). One **full run** = ~160 gold items
(`--n 40`) × 6 models × 2 conditions (`none`, `find_iri`) × 2 regimes
(`forced`, `abstain`) ≈ **5,760 LLM calls**.

| Model | in $/M | out $/M | low $ | high $ |
|---|---|---|---|---|
| openai/gpt-5.5 | 5.00 | 30.00 | 4.69 | 15.73 |
| google/gemini-3.5-flash | 1.50 | 9.00 | 1.41 | 2.07 |
| deepseek/deepseek-v3.2 | 0.23 | 0.34 | 0.14 | 0.24 |
| qwen/qwen3.6-35b-a3b | 0.14 | 1.00 | 0.14 | 0.36 |
| meta-llama/llama-4-scout | 0.10 | 0.30 | 0.07 | 0.07 |
| openai/gpt-oss-20b | 0.03 | 0.14 | 0.02 | 0.03 |
| **TOTAL (one full run)** | | | **~6.50** | **~18.50** |

## Takeaways
- **One full run ≈ $6–19** (realistically ~$8–12). The band is reasoning-token
  volume; a "return one IRI" task should sit low-to-mid.
- **gpt-5.5 is ~75–85% of the cost.** The other five combined are ~$2–3. Drop or
  downgrade gpt-5.5 (e.g. `gpt-5.4-mini`) → whole run under ~$3.
- **Pilot (deepseek-v3.2 + llama-4-scout, full 160 items)** ≈ **$0.30**.
- **Upgrade to 100/stratum** (~400 items) ≈ **$16–46**.

## Assumptions
Per call: `none`=1 call, `find_iri`≈2 calls; tokens ~150 (none) to ~450+950
(find_iri call1+call2, incl. tool schema + result); reasoning models emit 4–6×
output on the high end. The harness logs real token usage — recalibrate from a
pilot.

## Recompute
Re-fetch pricing + re-estimate: see the pricing block in the session, or query
`https://openrouter.ai/api/v1/models` and multiply by the token assumptions above.
