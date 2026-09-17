# DL query evaluation

This evaluation measures how agents translate natural-language questions into DL
queries and report entailed answer sets. The released data contain 120 questions,
five models and four conditions: **2,400 runs, 480 per model**. See
[RESULTS.md](RESULTS.md) for the current results.

## Reproduce the paper offline

From the repository root, with Python 3.10 or later and no additional packages:

```bash
python3 experiments/reproduce.py --out-dir results/paper-reproduction
```

Choose an empty output directory. This command validates the selected inputs and
reproduces both evaluations, paired contrasts and reasoning diagnostics without
calling a model or a live service. It writes `report.json`,
`paired_contrasts.json` and per-run reasoning scores under the output directory.

For reasoning scores alone, run from `experiments/dl_reasoning/`:

```bash
python3 score_dl.py --gold gold_all.jsonl \
  --runs runs_gemini-3.5-flash.jsonl runs_deepseek-v3.2.jsonl \
         runs_llama-4-scout.jsonl runs_qwen3.6-35b-a3b.jsonl \
         runs_gpt-oss-20b.jsonl \
  --classes classes_go.txt classes_cl.txt classes_so.txt \
  --out /tmp/aberowl-reasoning-scored.jsonl
```

Use these five complete files explicitly. A `runs_*.jsonl` wildcard can also
select partial or exploratory runs in a working checkout. Previously generated
`scored_*.jsonl` files are derived outputs; regenerate the current scores from the
listed raw files. `make_figure.py` is a historical plotting helper with older
default inputs and is not used by the current manuscript.

## Questions and conditions

Each ontology contributes 20 T1 and 20 T2 questions, giving 60 per task type:

- **T1, subsumption:** return all strict subclasses of a named class.
- **T2, existential restriction:** return all named classes satisfying a restriction
  with one named property and filler.

| Condition | Tools available | Additional prompt |
|---|---|---|
| `none` | None | None |
| `lookup` | `find_iri`, `search_classes` | None |
| `dlquery` | Lookup tools plus `run_dl_query` | None |
| `dlquery_hint` | Same as `dlquery` | Worked Manchester syntax example |

The harness enforces each condition's tool allow-list and records refused calls.
Lookup provides identifier resolution and text search; the reasoning condition
adds retrieval of entailed answer sets. The evaluated forms could also be served
from suitably materialized inference. The comparison therefore measures the
benefit of the provided reasoning interface over these lookup tools, not a requirement
for online reasoning or superiority over every retrieval method.

## Gold sets and provenance

`build_dl_gold.groovy` loads local OWL files and computes gold sets using ELK 0.4.3
and OWLAPI 4.5.29 outside AberOWL. Both reference and service use ELK: this is a
service-integration reference, not independent validation of the reasoner.

| Ontology | Recorded class universe | Recorded release date |
|---|---:|---|
| GO | 51,937 | 2026-03-25 |
| CL | 19,151 | 2026-03-26 |
| SO | 2,752 | Not recorded |

The builder selects entities with a single distinct label, filters answer sets
to 3–25 members, and shuffles candidates with a configurable seed (default 42).
For T2, at least half the answers must be absent from its explicit
subclass-restriction check. The original seed is not recorded in the released
gold files. `gold_all.jsonl` fixes the sampled questions and answer sets;
`gold_go.jsonl`, `gold_cl.jsonl` and `gold_so.jsonl` retain the per-ontology sets.

`classes_go.txt`, `classes_cl.txt` and `classes_so.txt` record the class IRIs used
for offline fabrication scoring. **These universes do not freeze ontology axioms
or identify an exact release.** Original OWL checksums and SO's exact version are
missing; exact reconstruction of the original classification inputs is therefore
not guaranteed. Offline rescoring uses the saved gold and universes directly.

The model runs span approximately August–September 2026. The logs omit their
exact deployed service commits. Qwen, DeepSeek and Gemini runs record sets of
provider names; GPT-OSS and Llama runs predate that logging. The logs do not
retain call-by-call provider mappings. Current configuration and container tags
must not be treated as historical version pins. `service_gold_agreement.json`
records a later check on 14 September 2026: all 120 gold expressions returned
exactly their saved answer sets. That establishes agreement on the check date,
not the service state during earlier model runs.

## Scoring and budgets

Primary success is exact equality between the final extracted IRI set and gold.
The scorer also reports set precision, recall and F1, answer presence, explicit
`NONE` answers, and fabrication against the saved class universes. The scorer
retains truncated runs and evaluates their returned answers.

For the no-hint reasoning condition, diagnostics separate tool adoption,
formulation and final-answer relay. The scorer parses answer rows from tool text,
excluding the header that echoes the submitted query. When several reasoning
calls occur, it selects the result closest to gold by F1. This is a best-call
within-run diagnostic, not first-attempt performance. The reproduction report
also gives first- and last-call strict formulation counts.

Strict diagnostics compare sets directly. Normalized diagnostics remove the
question's anchor class from both sets: the named class for T1, the filler for T2.
This accommodates `subeq`, which includes the named class while T1 gold contains
strict subclasses. The primary final-answer exact-match scores remain strict.

The released harness uses temperature 0, a 12-turn tool budget, and a 6,000-character
limit for both visible and logged tool results. It does not set a completion-token
cap. `truncated=True` marks exhaustion of the turn budget without a final answer;
it is distinct from truncation of an individual tool result. Eleven of 2,017
recorded reasoning results reached the character boundary. The gold maximum of
25 classes limits response size but does not eliminate this separate constraint.

## Optional new runs

These operations are separate from offline reproduction. Model reruns require
OpenRouter credentials, network access, the harness dependencies and an available
MCP service; they incur API charges and may use changed models or ontologies.
With `OPENROUTER_API_KEY` set in the environment, run one model at a time from this
directory and use a new output filename:

```bash
python3 run_model.py --model openai/gpt-oss-20b \
  --gold gold_all.jsonl --out /tmp/aberowl-new-gpt-oss-20b.jsonl
```

Model IDs and conditions are in `config.py`; the current configuration has
`PIN_PROVIDER = False`. The provider-preference table is not an enforced pin.
The harness records provider information when available and saves raw responses.

To build new gold data, supply an OWL file to the Groovy builder in an environment
with its declared Grapes dependencies. This classifies the ontology and should
run on an appropriately provisioned compute host. For example, from this directory:

```bash
groovy build_dl_gold.groovy --owl /path/to/go.owl --id go \
  --out /tmp/new-gold-go.jsonl --classes /tmp/new-classes-go.txt --n 20 --seed 42
```

Record the OWL checksum, imports, versions and full command for new builds. This
creates a new sample; it is not a substitute for the released scoring inputs.
To perform a new live service/gold check without overwriting the saved check:

```bash
python3 check_service_gold.py --gold gold_all.jsonl \
  --out /tmp/aberowl-new-service-gold-agreement.json
```

Use `--api` to override the service base URL. Neither new gold generation nor a
new live check is needed to reproduce the reported scores.