# DL query evaluation results

All five models completed 480 runs each: 120 questions in four conditions,
**2,400 scored runs**. These results use the five complete raw run files listed
in [README.md](README.md), the released gold sets and the answer-row parser in
`score_dl.py`. They supersede the earlier two-model summaries.

From the repository root, reproduce the tables and diagnostics offline with:

```bash
python3 experiments/reproduce.py --out-dir results/paper-reproduction
```

The output directory must be empty. The resulting `report.json` contains the
primary and diagnostic counts; `paired_contrasts.json` contains the paired
tests. Reproduction does not require a live service, model calls or additional
Python packages.

## Primary result

Exact final answer-set match (%), **60 questions per cell**, in manuscript order:

| Model | None T1 | None T2 | Lookup T1 | Lookup T2 | DL query T1 | DL query T2 | + syntax example T1 | + syntax example T2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Gemini-3.5-flash | 0.0 | 0.0 | 6.7 | 3.3 | 90.0 | 96.7 | 90.0 | 93.3 |
| DeepSeek-V3.2 | 0.0 | 0.0 | 11.7 | 11.7 | 95.0 | 86.7 | 95.0 | 76.7 |
| Llama-4-Scout | 0.0 | 0.0 | 0.0 | 3.3 | 98.3 | 100.0 | 83.3 | 65.0 |
| Qwen3.6-35B | 0.0 | 0.0 | 10.0 | 11.7 | 88.3 | 98.3 | 93.3 | 86.7 |
| GPT-OSS-20B | 0.0 | 0.0 | 0.0 | 8.3 | 93.3 | 96.7 | 98.3 | 35.0 |

T1 requests strict subclasses of a named class; T2 requests classes satisfying
an existential restriction. Each ontology contributes 20 questions per task.
No unaided run matched gold. The lookup tools alone reach at most 11.7% per cell,
while access to `run_dl_query` raises exact match across all five models.
This supports the utility of the provided reasoning interface for these selected
queries. It does not compare against hierarchy traversal or retrieval from a
materialized inference graph, nor establish model superiority or equivalence.

Gemini's lookup results are budget-limited: **105 of 120** runs exhausted the
12-turn budget without a final answer. The scorer retains these runs in the
denominator and evaluates their returned answers. Ten of 600 no-hint reasoning
runs exhausted that budget.

## No-hint reasoning diagnostics

Of 600 `dlquery` runs, **599 invoked the reasoning tool**. Formulation compares
the tool's answer set with gold; relay compares it with the model's final answer.
The default diagnostic chooses the tool call closest to gold by F1 when there
are several calls. Anchor normalization removes the queried class (T1) or filler
(T2) from both sets; strict primary final-answer scoring does not remove it.

| Diagnostic | Count | Percentage |
|---|---:|---:|
| First-call strict formulation | 493/599 | 82.3% |
| Last-call strict formulation | 477/599 | 79.6% |
| Best-call strict formulation | 538/599 | 89.8% |
| Best-call anchor-normalized formulation | 585/599 | 97.7% |
| Best-call strict relay | 537/599 | 89.6% |
| Best-call anchor-normalized relay | 578/599 | 96.5% |

Normalization affects only T1 in these runs. T2 strict and normalized relay
both equal 289/300 (96.3%). In 41 T1 runs the model omitted the anchor returned
by the tool; 39 then matched gold exactly. These diagnostics use the corrected
parser that reads answer rows, not IRIs echoed in the tool response header.

The **34 final-answer failures** break down as follows:

| Failure | Runs |
|---|---:|
| Did not invoke reasoning | 1 |
| Incorrect formulation | 12 |
| Altered the returned answer set | 13 |
| Retained the anchor class | 8 |

This decomposition concerns final-answer failures and does not change with the
first-/last-call formulation summaries. Best-call metrics describe success at
some point during a run, not the reliability of the first or final tool call.

## Syntax example and uncertainty

The worked Manchester syntax example lowers T2 exact match for every model in
this sample. Recorded failures include copying example identifiers and withholding
answers, but the experiment does not isolate the causal effect of example copying
from other prompt changes. Three paired contrasts have unadjusted, two-sided
exact McNemar p-values at most 0.016; DeepSeek and Gemini have p-values 0.26 and
0.50. Treat these as exploratory paired comparisons, not multiplicity-adjusted
claims. With 60 questions per cell, one answer changes the score by 1.7 percentage
points; even 60/60 has a 95% Wilson interval of 94.0–100.0%.

## Scope of the reference

The gold builder computes reference sets with ELK outside AberOWL. The deployed
service also uses ELK. The evaluation checks agent use and service integration
with that reference, not independent reasoner correctness. A later saved check
on **14 September 2026** found exact service/gold agreement for **120/120**
expressions; it does not recover the deployed service version during the
earlier model runs.

The saved class universes support deterministic offline fabrication scoring but
do not preserve ontology axioms. SO's exact release, original OWL checksums and
historical service commits are missing, and original provider logging is partial.
The README documents these provenance limits and the distinction between
rescoring saved outputs and conducting new runs.
