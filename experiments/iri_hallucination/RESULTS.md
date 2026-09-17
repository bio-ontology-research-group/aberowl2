# Grounding results

These are the current results from the saved responses, replacing the earlier
173-item summaries. Reproduce them from the repository root:

```bash
python3 experiments/reproduce.py --out-dir results/paper-reproduction
```

Use an absent or empty output directory. The command is offline and writes
`report.json`, `grounding/by_stratum.txt`, `grounding/aggregate.txt`,
`grounding/by_difficulty.txt`, `grounding/repeats.txt`, and
`paired_contrasts.json`, alongside the other evaluation outputs.

## Main grounding table

All values are percentages. Column order matches the main manuscript:
positive accuracy and hallucination use the **forced** regime; negative
declines use the **abstain** regime. “Tool” means `find_iri` is available.

| Model | Positive accuracy, none | Positive accuracy, tool | Positive hallucination, none | Positive hallucination, tool | Negative declines, none | Negative declines, tool |
|---|---:|---:|---:|---:|---:|---:|
| Gemini-3.5-flash | 36.1 | 95.9 | 2.5 | 0.0 | 52.5 | 95.0 |
| DeepSeek-V3.2 | 27.0 | 96.7 | 1.7 | 0.0 | 27.5 | 97.5 |
| Llama-4-Scout | 6.6 | 92.6 | 12.3 | 3.3 | 15.0 | 82.5 |
| Qwen3.6-35B | 9.0 | 95.9 | 6.6 | 0.0 | 85.0 | 90.0 |
| GPT-OSS-20B | 6.6 | 96.7 | 21.5 | 0.0 | 92.5 | 85.0 |

Positive cells have 122 runs except GPT-OSS-20B forced/none (121). One
DeepSeek-V3.2 forced/none response has unresolved existence: its accuracy
uses 122, and hallucination uses 121. All negative cells shown have 40 runs.
The tool improves positive accuracy in each model; negative declines improve
for four models and decrease for GPT-OSS-20B. These results do not establish
model-size equivalence or coverage beyond this selected benchmark.

## Aggregate over positives and negatives

This supplementary table pools the strata. Accuracy counts only correct
positive identifiers: negative declines do not count as correct identifiers.
Hallucination here mixes absent identifiers on positives with any identifier
assigned to a negative item. It must not be read as positive-only fabrication.
Values are percentages; `n` is the cell's response count. Column order matches
the supplementary aggregate table.

| Model | Forced none n | Accuracy | Hallucination | Valid wrong | Forced tool n | Accuracy | Hallucination | Abstain tool n | Accuracy | Hallucination |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Gemini-3.5-flash | 162 | 27.2 | 25.9 | 46.3 | 162 | 72.2 | 9.3 | 162 | 72.2 | 1.2 |
| DeepSeek-V3.2 | 162 | 20.4 | 26.1 | 53.4 | 162 | 72.8 | 13.0 | 162 | 72.8 | 0.6 |
| Llama-4-Scout | 162 | 4.9 | 33.3 | 60.5 | 162 | 69.8 | 25.3 | 162 | 62.3 | 8.6 |
| Qwen3.6-35B | 162 | 6.8 | 24.7 | 59.9 | 159 | 73.6 | 13.2 | 162 | 72.8 | 2.5 |
| GPT-OSS-20B | 160 | 5.0 | 35.6 | 46.2 | 162 | 72.8 | 16.7 | 162 | 72.8 | 3.7 |

Hallucination and valid-wrong rates exclude unresolved existence (one response
in DeepSeek-V3.2 forced/none); accuracy and abstention keep all cell responses.
The generated aggregate output also includes the abstain/none cells and
abstention rates, which help interpret differences in answering frequency.
Across all 20 cells, **3,231 responses** remain from **3,450 saved non-error
responses** after gold filtering and prompt de-duplication. The original grid
contained 3,460 calls, ten excluded for invalid JSON. Four de-duplicated cells
are incomplete: Qwen forced/tool (159), Qwen abstain/none (161), GPT-OSS
forced/none (160), and GPT-OSS abstain/none (159).

## Deterministic grounding baseline

| Measure | Count | Percentage |
|---|---:|---:|
| Exactly one correct exact match on positives | 117/122 | 95.9 |
| Ambiguous exact matches on positives (unsuccessful) | 5/122 | 4.1 |
| No exact match on negatives | 40/40 | 100.0 |

All five ambiguous responses contain gold and another identifier. The scorer
does not select between them. Negative success is **no-exact-match detection**,
not abstention by the tool. The MCP tool can return suggestions after an empty
exact result. The baseline uses later saved resolver responses and is not a
ceiling on agent accuracy. See [method and inputs](direct_lookup/README.md).

## Limited repeat analysis

| Condition | Pairs | Exact-IRI agreement (%) | Correctness agreement (%) |
|---|---:|---:|---:|
| No tool | 119 | 60.5 | 89.1 |
| `find_iri` | 120 | 98.3 | 99.2 |

These 239 pairs cover ten identical scoped prompts within the original batches
at temperature 0. They do not measure provider drift or broad run-to-run
stability. Paired condition contrasts in `paired_contrasts.json` use two-sided
exact McNemar tests; their p-values are exploratory and unadjusted for multiple
comparisons. The [README](README.md) documents scoring, selection bias, logging
limits, and missing historical service/provider provenance.
