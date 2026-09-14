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
| `so` | small | 2,752 | not recorded (the class universe `classes_so.txt` fixes the release's 2,752 classes) |

Size is **not** the hypothesis; it proxies training-data familiarity. The prediction
is that the `none` arm does best on GO and worst on SO, so the reasoning gain grows
as familiarity falls (the same shape as L1→L4 in the IRI experiment).

`symp` and `iao` were rejected: SYMP has **1 object property**, so T2 is impossible,
and IAO has 267 classes, so answer sets are too small to score.

**Pin provenance.** The `release` column above was verified against the central
registry at gold-build time; no version IRI, release date, or checksum is stamped
into `gold_go.jsonl` / `gold_cl.jsonl` / `gold_so.jsonl` or into
`build_dl_gold.groovy` itself (checked by grep; none of the three JSONL files or
the script carry a `versionIRI` field or a recorded md5/sha256). `build_dl_gold.groovy`
takes the OWL release as a local `--owl FILE` argument; it has no embedded
download step or URL, so a reproducer must obtain the same release independently
(e.g. the dated OBO Foundry release for the pinned date above) and should **record
its `md5sum` before running the build**; the md5 of the originals used for the
released gold files was not recorded anywhere in this repo. Each gold file's
companion `--classes` output (`classes_go.txt`, `classes_cl.txt`, `classes_so.txt`,
51,937 / 19,151 / 2,752 lines respectively) is the exact class-IRI universe of the
same loaded release, dumped by the same script in the same run, so it is the
closest available proxy for "which release" if the md5 is unrecoverable.

## Which service answered these runs

By default `config.py` points at production (`MCP_URL` defaults to
`http://aber-owl.net/mcp/ontology/mcp`, `ABEROWL_API` to `http://aber-owl.net/api`),
not beta. Per `credits_log.jsonl`, the four logged per-model runs completed
2026-08-19T07:06 UTC (`llama-4-scout`) through 2026-09-01T02:44 UTC
(`gemini-3.5-flash`); `gpt-oss-20b` has no `credits_log.jsonl` entry, and its
`runs_gpt-oss-20b.jsonl` / `runs_gpt-oss-20b.laptop-partial.jsonl` files are dated
2026-08-25 and 2026-08-18 respectively, so the released runs span roughly
2026-08-18 to 2026-09-01.

**The exact deployed service commit for that window is not recorded in the run
files** (no commit/version field is logged per row or per model). The closest
documented pin is in `DEPLOY_FOLLOWUPS.md` ("Image tags: prod runs a DIFFERENT
`:2.0` than Docker Hub now serves"): as verified there on 2026-08-20, production
was running image manifests `d61961ae…` (worker) / `22dc82e7…` (central), and was
"deliberately not redeployed" when the `:2.0` tags were later overwritten on Docker
Hub, so that manifest pin, not a git commit, is the only documented identifier
for what likely served this window, and `CHANGES.md` has no entries for
2026-07 through 2026-09 to cross-check it against. **`kaustborg/aberowl-central:2.0`
and `kaustborg/aberowl-worker:2.0` on Docker Hub are the current published release,
not necessarily the code that served these runs**; per the same section, Docker
Hub overwrote those tags in place on 2026-08-20 while production kept running the
older pulled image.

## Running

Grapes cannot resolve OWLAPI on the bare workstation (Ivy fails to download
`commons-io` and `j2objc-annotations` even though curl fetches them). Run inside the
worker image, which already carries the resolved classpath:

```bash
docker run --rm -v "$PWD/dl_gold":/work -e JAVA_OPTS="-Xmx24g" \
  --entrypoint sh kaustborg/aberowl-worker:2.0 -c \
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

## Harness constants in force during the released runs

From `config.py` as committed:

- `MAX_TOOL_TURNS = 12`
- `TOOL_RESULT_CHARS = 6000` (what the model sees per tool result)
- `TOOL_LOG_CHARS = 6000` (what is recorded to the run file; deliberately equal to
  `TOOL_RESULT_CHARS`, "or relay fidelity is unmeasurable" per the comment beside it)
- `TEMPERATURE = 0.0`
- Completion cap: **not fixed by this harness.** `config.py` sets no `max_tokens` and
  `run_model.py` sends none in the request payload; the "147k/236k max completion"
  figures in `config.py`'s `PROVIDERS` comments are the OpenRouter *endpoint's* own
  ceiling, not a cap this harness applies.

`truncated=True` in a run row means the 12-turn budget (`MAX_TOOL_TURNS`) was
exhausted before the model produced a final answer, see
`../iri_hallucination/harness.py`, which returns `_result(..., truncated=True, ...)`
immediately after its `for _ in range(C.MAX_TOOL_TURNS):` loop ends without an
earlier return; this experiment's own run loop follows the same shared pattern.

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

## Service/gold agreement

`check_service_gold.py` submits every gold expression to the deployed service and compares
the answer sets with `gold_iris`, strictly and with the anchor class set aside.
`service_gold_agreement.json` is the run of 14 September 2026 against `https://aber-owl.net`:
all 120 expressions returned the gold set exactly. The check establishes agreement between
the served ontologies and the gold sets at the time it runs; it does not recover the
service version that answered the released model runs.
