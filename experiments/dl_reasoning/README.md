# DL-reasoning experiment (Q2): can an LLM agent exploit reasoning-as-a-service?

Companion to `../iri_hallucination`, and the fix for review point **W1**: the paper
is titled *description logic reasoning as a service for LLM agents*, but the
existing experiment only exercises `find_iri`, whose code path ends in an
Elasticsearch term query and never touches ELK. Nothing in the submitted paper
shows an agent reasoning.

## What this measures

**Not** whether ELK is correct. "ELK inside AberOWL agrees with ELK outside
AberOWL" is a regression test, not a result. What is measured is whether an agent
can **exploit** a reasoning service, and where it fails when it cannot.

Task: given a class expression in **natural language**, return the **set** of
classes satisfying it. Set-valued, which is why `../iri_hallucination/score.py`
cannot be reused (its `extract_iri` returns one IRI per answer).

### Arms

| condition | tools exposed | isolates |
|---|---|---|
| `none` | — | parametric recall |
| `lookup` | `find_iri`, `search_classes` | grounding **without** reasoning |
| `dlquery` | + `run_dl_query` | full capability |
| `dlquery_hint` | + Manchester example in system prompt | is formulation failure a *documentation* artifact? |

`lookup` is the arm that makes the experiment worth running. Without it, a reviewer
says the gain came from grounding, which the previous experiment already showed.

### Task types

- **T1** subsumption: "all subclasses of X". Partly reachable from memory or lookup.
- **T2** existential: "all classes that are `R` some `C`". Reachable **only** by
  reasoning; no label lookup returns an inferred subsumption.

The result is the **interaction**, not any single cell: if `lookup` closes much of
the T1 gap and almost none of the T2 gap, the T2 gain is attributable to reasoning.

### The four-stage decomposition

End-to-end F1 cannot say *why* a run failed. Because the harness logs each tool
call's `args` verbatim, every reasoning-arm failure attributes to a stage:

| stage | measured by |
|---|---|
| adoption | did `run_dl_query` appear in `tools_invoked`? |
| formulation | did the expression the model built return the gold set? |
| service | did AberOWL answer the model's own query correctly? |
| relay | did the final answer preserve what the tool returned? |

This is the contribution. "Models call the tool X% of the time, build a correct
expression Y% of the time, relay it faithfully Z%" says something about designing
agent-facing reasoning services; one accuracy number does not.

## Gold sets are built OUTSIDE AberOWL

`build_dl_gold.groovy` loads the OWL release from disk and runs its own ELK. The
service is never consulted.

This is not optional. The IRI experiment's `build_gold.py` admitted an item only if
AberOWL resolved it (`resolves_to` is literally `resolve(term, ont) == iri`), so
AberOWL scored 100% **by construction** and no service comparison from it is valid.

Using ELK for gold while AberOWL also runs ELK is fine: ELK is sound and complete
for OWL 2 EL, so the answer set is a property of the **ontology**, not the
implementation. Independence means not asking the system under test to grade itself.

It also dumps each ontology's **class universe** (`--classes`), which makes
fabrication a set-membership test — offline and deterministic, with none of the
resolver flakiness (302s not followed, 429 storms scored as non-existence) that made
the IRI experiment's hallucination column wrong.

### Corpus pinning

Gold must come from the **deployed** release, or version drift is misattributed to
the model. Verified against the registry:

| id | role | classes | release |
|---|---|---|---|
| `go` | large | 51,937 | 2026-03-25 |
| `cl` | medium | 19,151 | 2026-03-26 |
| `so` | small | 2,752 | current |

Size is **not** the hypothesis; it proxies training-data familiarity. The prediction
is that the `none` arm does best on GO and worst on SO, so the reasoning gain grows
as familiarity falls (the same shape as L1→L4 in the IRI experiment).

`symp` and `iao` were rejected: SYMP has **1 object property**, so T2 is impossible,
and IAO has 267 classes, so answer sets are too small to score.

## Running

Grapes cannot resolve OWLAPI on the bare workstation (Ivy fails to download
`commons-io` and `j2objc-annotations` even though curl fetches them). Run inside the
worker image, which already carries the resolved classpath:

```bash
docker run --rm -v ~/dl_gold:/work -e JAVA_OPTS="-Xmx24g" \
  --entrypoint sh aberowl2-ontology-api:latest -c \
  "cd /work && groovy build_dl_gold.groovy --owl go.owl --id go \
     --out gold_go.jsonl --classes classes_go.txt --n 20"
```

Then, **one model at a time**:

```bash
OPENROUTER_API_KEY=... python run_model.py --model openai/gpt-oss-20b \
    --gold gold_all.jsonl
```

`run_model.py` refuses more than one model per invocation, records the credit delta
to `credits_log.jsonl`, and stops. Model order in `config.py` is ascending cost:

    gpt-oss-20b -> llama-4-scout -> qwen3.6-35b -> deepseek-v3.2 -> gemini-3.5-flash

Gemini is last on purpose: it consumed ~$24 of the IRI experiment's ~$26.4 because
extended thinking is on by default, 4x the projection. Check the measured burn on
the cheap models before committing to it.

Scoring:

```bash
python score_dl.py --gold gold_all.jsonl --runs 'runs_*.jsonl' \
    --classes classes_go.txt classes_cl.txt classes_so.txt
```

## Known gotchas

- **Ambiguous labels.** Some OBO releases carry a second `rdfs:label` as an xref
  artifact (`SO_0000101` is both `transposable_element` and `wiki`), returned in
  non-deterministic order. `labelOf` skips any entity without exactly one label.
- **Tool-output truncation.** `exec_tool` caps what the model sees. Gold sets are
  filtered to 3–25 members so a truncated result never masquerades as a reasoning
  failure. `TOOL_LOG_CHARS` must equal `TOOL_RESULT_CHARS` or relay fidelity is
  unmeasurable.
- **Turn cap.** `MAX_TOOL_TURNS = 12` here; 6 already truncated 39/346 of Gemini's
  runs on the simpler IRI task.
