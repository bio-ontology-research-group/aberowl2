# DL-reasoning experiment — results

Addresses review point **W1**: the paper claims *description logic reasoning as a
service for LLM agents*, but the submitted evaluation only exercised `find_iri`,
whose path ends in an Elasticsearch term query and never reaches ELK.

Metrics are computed from saved per-run files (`runs_<model>.jsonl`,
`scored_<model>.jsonl`), never from console output. Re-score at any time with
`score_dl.py`; no re-run is needed to change a metric.

## Status

| model | runs | errors | truncated | tokens in/out | spent |
|---|---|---|---|---|---|
| `openai/gpt-oss-20b` | 480/480 | 0 | 17 | 3,484,813 / 3,263,037 | $0.5756 |
| `meta-llama/llama-4-scout` | 480/480 | 0 | 0 | 1,518,000 / 88,300 | $0.2036 |
| `qwen/qwen3.6-35b-a3b` | pending (needs key cap raised) | | | | |
| `deepseek/deepseek-v3.2` | pending | | | | |
| `google/gemini-3.5-flash` | pending (~$30; needs cap raised) | | | | |

## gpt-oss-20b

Exact-set match, n=60 per cell, 95% Wilson CI:

| arm | T1 subsumption | T2 existential |
|---|---|---|
| `none` (parametric) | 0.0 [0.0,6.0] | 0.0 [0.0,6.0] |
| `lookup` (grounding only) | 0.0 [0.0,6.0] | 8.3 [3.6,18.1] |
| `dlquery` (reasoning) | **93.3** [84.1,97.4] | **96.7** [88.6,99.1] |
| `dlquery_hint` | 98.3 [91.1,99.7] | 35.0 [24.2,47.6] |

**The isolator works.** Parametric memory scores exactly zero on both task types.
Grounding alone — with `find_iri` and `search_classes` available — barely moves it.
Reasoning takes it to 93–97%. Because the `lookup` arm had full label-to-IRI
resolution and still failed, the gain is attributable to reasoning rather than to
grounding, which is the claim the title needs and the previous experiment could not
support.

**Zero is not "poor", it is zero.** In the `none` arm the model returned IRIs on 43
of 115 attempted runs and **not one overlapped gold**, while 192 of the 213 distinct
IRIs it produced (90%) are real classes. This reproduces the IRI experiment's
valid-but-wrong result in a set-retrieval setting: well-formed, genuinely existing
ontology classes that are simply not the answer.

**Failure decomposition** (`dlquery`): adoption 100%, formulation 95.0/96.7%
(T1/T2), relay 98.3/100%. Given the tool, this model uses it, builds the right
expression, and reports it faithfully.

**The model knows when it needs a reasoner.** In the `lookup` arm it *attempted*
`run_dl_query` — and was refused — on **30.0%** of T2 items versus **8.3%** of T1.
Demand for reasoning tracks whether the question actually requires it.

### By ontology

| ontology | classes | `none` | `lookup` | `dlquery` |
|---|---|---|---|---|
| GO | 51,937 | 0.0 | 12.5 | 97.5 |
| CL | 19,151 | 0.0 | 0.0 | 92.5 |
| SO | 2,752 | 0.0 | 0.0 | 95.0 |

The familiarity hypothesis (larger/more famous ontology → better unaided
performance) gets **no support in the success metric**: `none` is zero everywhere.
It shows only weakly in willingness to answer (GO 50.0%, SO 40.0%, CL 27.5%
attempted). The reasoning benefit is essentially flat across a 19x range in ontology
size, which should be stated rather than spun.

### The hint ablation backfired, informatively

`dlquery_hint` collapsed on T2 (35.0 vs 96.7) while slightly helping T1. Cause is
diagnosable rather than mysterious: **34 of 40 sampled failed queries reuse IRIs from
the hint's own example**, and 11 of 39 failures hit the turn cap while retrying
plausible-but-wrong `RO_*` properties. Attempt rate fell to 43.3% and `NONE`
answers rose to 28.3% as the model burned turns.

So a syntax hint carrying **concrete IRIs induces IRI anchoring** and degrades
formulation. This is a useful design warning for agent-facing tools, but it is
confounded with the hint's construction, which mixed an abstract pattern
(`<propertyIRI> some <classIRI>`) with a concrete example. Separating "syntax help"
from "IRI anchoring" needs one added cell with a placeholder-only hint; it does not
require changing the existing arms.

## Validity notes

- **Tool allow-list is enforced**, not merely advertised. The MCP session exposes all
  ten tools, so before the fix any tool name the model emitted was executed: 21% of a
  pre-fix lookup arm called `run_dl_query`. Verified on this run: zero out-of-allowlist
  executions, 28 attempts correctly blocked. The same defect affects the published IRI
  experiment at 0.4% (7/1727), disclosed in the paper's revision plan.
- **Gold is built outside AberOWL** by `build_dl_gold.groovy` from the pinned deployed
  releases, so the service is not grading itself.
- **Corpus alignment** was verified 24/24 before the run: prod returns byte-identical
  answer sets to the pinned releases, so a service-side mismatch cannot be
  misattributed to the model.
- **Formulation is scored modulo the anchor class.** `subeq` returns the queried class
  itself while gold is strict subclasses; 63/73 T1 tool results were exactly
  gold+anchor. Scoring those as failures understated formulation as 8.3% against a
  true 95%. Strict values are retained as `*_strict`.
- **17 runs hit the 12-turn cap** (all in the hint arm); they are counted as failures,
  not excluded.


## llama-4-scout — the effect replicates

Exact-set match, n=60 per cell:

| arm | T1 | T2 |
|---|---|---|
| `none` | 0.0 [0.0,6.0] | 0.0 [0.0,6.0] |
| `lookup` | 0.0 [0.0,6.0] | 3.3 [0.9,11.4] |
| `dlquery` | **98.3** [91.1,99.7] | **100.0** [94.0,100.0] |
| `dlquery_hint` | 83.3 [72.0,90.7] | 65.0 [52.4,75.8] |

The pattern is identical to gpt-oss-20b: parametric memory scores **zero**, grounding
alone barely moves it, reasoning solves it. On T2 llama is perfect (60/60).
Decomposition: adoption 100%, formulation 100%, relay 98.3/100%.

### The two models fail in opposite ways without the tool

Per-run rates, averaged over runs that attempted an answer. **Use per-run, not pooled
distinct IRIs**: one gpt-oss run enumerated 2,644 sequential `NCBITaxon_*` IDs and by
itself supplied ~90% of the distinct IRIs in its arm, which makes any pooled
fabrication rate an artifact of a single outlier.

| | gpt-oss `none` | gpt-oss `dlquery` | llama `none` | llama `dlquery` |
|---|---|---|---|---|
| exact set | 0.0 | **95.0** | 0.0 | **99.2** |
| attempted | 39.2% | 98.3% | 81.7% | 100% |
| mean fabrication | 2.1% | **0.0** | 37.5% | **0.0** |
| median IRIs returned | 4 | 5 | 2 | 5 |
| max IRIs returned | **2,939** | **25** | **860** | **25** |

Three results here:

- **Fabrication differs 18x between models unaided** (2.1% vs 37.5%) and goes to
  **zero for both** with the reasoner. gpt-oss-20b mostly emits real classes that are
  simply the wrong ones (the valid-but-wrong result, review point W3); llama-4-scout
  invents well-formed OBO identifiers that do not exist, plus 60 malformed strings
  (`http://purl.obol`, `.../obo/Biological_Process_0000380`).
- **Unaided models do not know when to stop.** Maximum answer sizes of 2,939 and 860
  IRIs against a gold ceiling of 25. With the reasoner the maximum is exactly 25 and
  the median is 5, matching gold. The tool constrains cardinality, not just content.
- **Grounding alone does not fix either problem**: the `lookup` arm reaches 4.2% and
  1.7% exact, with runaway generation still present (max 2,644).

### Hint anchoring replicates

`dlquery_hint` degraded T2 for both models (gpt-oss 96.7 -> 35.0; llama 100.0 -> 65.0),
so the effect is not a single-model quirk. It is weaker in llama, whose formulation
falls to 66.7% rather than 47.2%. A hint carrying concrete IRIs anchors the model on
them; this needs a placeholder-only cell to separate syntax help from IRI anchoring.

### One model-level difference worth noting

In the `lookup` arm, gpt-oss-20b attempted `run_dl_query` (and was refused) on 30.0%
of T2 items versus 8.3% of T1 — it recognised when a question needed a reasoner.
llama-4-scout never attempted it (0.0%). Recognising the need for reasoning is not
the same capability as using the reasoner well, and only the larger model showed it.
