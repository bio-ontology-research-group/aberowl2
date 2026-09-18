# Ontology IRI grounding evaluation

This experiment measures how agents use `find_iri` to return the canonical
class identifier for a term in a specified ontology. The release contains saved
responses for five models, with and without the tool, under forced-answer and
abstention-allowed prompts.

## Reproduce the reported results offline

From the repository root, using Python 3.10 or later and its standard library:

```bash
python3 experiments/reproduce.py --out-dir results/paper-reproduction
```

The output directory must be absent or empty; choose a new path if it already
contains results. This command does not call services or models and leaves the
saved inputs unchanged. It reproduces both paper evaluations, the grounding
baseline, and the repeat and paired analyses. `report.json` checks the reported
counts and tables; `grounding/` contains scored rows, aggregate and stratum
tables with Wilson intervals, and repeat-pair agreement. See
[RESULTS.md](RESULTS.md) for the current grounding tables.

## Design and released inputs

Every prompt supplies the term and its ontology scope. The conditions are
`none` (no tools) and `find_iri` (advertised through the tool schema, not mentioned
in the prompt). Each is crossed with `forced` and `abstain` prompt regimes.
[config.py](config.py), [prompts.py](prompts.py), and [harness.py](harness.py)
contain the harness configuration, prompt templates, and agent loop.

The five evaluated model identifiers are:

- `google/gemini-3.5-flash`
- `deepseek/deepseek-v3.2`
- `meta-llama/llama-4-scout`
- `qwen/qwen3.6-35b-a3b`
- `openai/gpt-oss-20b`

The original [gold.jsonl](gold.jsonl) contains 173 rows.
[gold_dedup.jsonl](gold_dedup.jsonl) contains 162 distinct scoped terms: 122
positives from 12 ontologies and 40 constructed negatives. Its difficulty
strata contain 8 famous terms, 45 exact labels, 42 synonyms, and 67 obscure or
negative terms. [runs_full.jsonl](runs_full.jsonl) holds 3,450 saved non-error
responses; filtering and collapsing duplicate prompts yields 3,231 scored
responses. The scoring workflow excludes ten of the original 3,460 calls
because they returned invalid JSON. [dedup_gold.py](dedup_gold.py) and
[replicates.json](replicates.json) preserve the duplicate-prompt analysis: 239
pairs across ten distinct prompts.

[score.py](score.py) uses the saved three-state existence map
[iri_exists_resolved.json](iri_exists_resolved.json), not a live existence
check. Correctness compares the extracted identifier with gold using the
scorer's normalization. A different existing identifier is `valid_wrong`; an
absent identifier is `hallucinated`; any identifier for a negative item is a
false assignment, also labeled `hallucinated`. The scorer labels responses
without an extracted identifier as `abstained`, including empty answers and
budget exhaustion. The scorer labels unresolved existence as `unknown` and
excludes these responses from hallucination and valid-wrong denominators.
Accuracy and abstention denominators retain these responses.

The results report positive accuracy, positive hallucination, and negative
declines separately. The aggregate hallucination rate mixes fabrication on
positives with false assignments on negatives and is also affected by
non-answers.

## Deterministic resolver baseline

The [direct lookup baseline](direct_lookup/README.md) gives the same term and
ontology directly to the exact resolver underlying `find_iri`, using saved
responses from 25 and 30 August 2026. Success on a positive requires exactly
one distinct exact-match identifier equal to gold: **117/122 (95.9%)** pass.
Five return two identifiers and count as unsuccessful even though gold is
among them. All **40/40 negatives** have no exact match. This is no-exact-match
detection, not tool abstention: `find_iri` can still offer fuzzy suggestions.

## Scope and provenance limits

Benchmark construction retained mined positives when AberOWL's resolver
returned the expected identifier and checked constructed negatives through
scoped resolution. This selection measures agent use on selected resolvable
terms, not general resolver coverage. Manual validation of negative
plausibility and coverage is not documented. The direct baseline is not an
upper bound: an agent may resolve ambiguity that fails its single-match
criterion.

The existence map records resolution on 19 August 2026 against production and
beta, accepting existence from either host: 806 identifiers exist, 88 are absent,
and four remain unresolved. It is a later check, not a frozen ontology snapshot
from each model call. The model-run files have no per-row timestamps or deployed
service commit; provider routing was unpinned and unlogged. File modification
times and current service versions cannot recover that historical state.
The later direct-resolver responses likewise cannot isolate all differences
between baseline and agents to agent behavior.

The grounding harness used temperature 0 and a six-turn budget. Models received
up to 4,000 characters of a tool response, while logs retained at most 600;
those logs do not reconstruct the full tool context. Empty and truncated answers
count as abstentions even under forced-answer prompts. The historical harness
advertised condition-specific tools but did not enforce that boundary, so some
saved runs executed a tool outside the advertised condition. The 239 repeat
pairs measure limited within-batch variability, not stability across providers
or dates. Overlapping intervals do not establish model equivalence.

## New model runs (paid, separate from reproduction)

New runs require `httpx`, the MCP Python SDK, an OpenRouter API key, and a reachable
AberOWL MCP service. Supply `OPENROUTER_API_KEY` and `ABEROWL_MCP_URL` in the
environment. After installing the harness dependencies, this example runs one
model on the de-duplicated set; from the repository root:

```bash
mkdir -p results/grounding-new
python3 experiments/iri_hallucination/harness.py \
  --gold experiments/iri_hallucination/gold_dedup.jsonl \
  --out results/grounding-new/runs.jsonl \
  --models deepseek/deepseek-v3.2 \
  --conditions none find_iri --regimes forced abstain
```

Use a new output filename: without `--resume`, the harness overwrites it.
`--resume` keeps successful rows and retries missing or errored cells. New runs
incur provider charges and depend on current model and service versions; they
are not expected to reproduce the saved answers. Record those versions and run
dates separately. The released existence map covers the saved answers only;
new identifiers may need new existence checks. Gold-building and map-building
scripts contact services and are not required for offline rescoring.
