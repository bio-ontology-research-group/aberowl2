# IRI-hallucination-reduction experiment

Does the AberOWL `find_iri` MCP tool reduce how often LLMs **hallucinate
ontology-class IRIs** — and let them abstain honestly instead of fabricating?

## Design

**Task.** Given a term, return the single canonical ontology-class IRI.

**Conditions** (which tools are *available* via the API — never hinted in the prompt):
- `none` — no tools (pure parametric) → baseline.
- `find_iri` — the exact-match grounding tool available; the model decides whether to call it.
- *(optional)* `search_classes`, `both` — fuzzy-search control / autonomous tool-selection test.

**Regimes** (to defuse the abstention confound):
- `forced` — must return an IRI (no abstain) → **raw hallucination**, primary metric.
- `abstain` — UNKNOWN offered *neutrally* → calibration (does the tool enable honest abstention).

**Difficulty** (effect should grow L1→L4): L1 famous · L2 exact label · L3 synonym ·
L4 obscure-ontology / nonexistent.

**Subjects** (capability gradient, tool-calling, via OpenRouter): `openai/gpt-5.5`,
`google/gemini-3.5-flash`, `deepseek/deepseek-v3.2`, `qwen/qwen3.6-35b-a3b`,
`meta-llama/llama-4-scout`, `openai/gpt-oss-20b`.

**Metrics** (reported separately so prompt framing can't hide anything):
accuracy · hallucination-rate (of all) · hallucination-among-*answered* ·
abstention-rate · **used-find_iri%** (autonomous tool use).

## Architecture

```
harness.py ──MCP client──► AberOWL MCP (find_iri)      # tool discovery + execution
     │  find_iri schema → OpenAI "tools" (unhinted)
     ▼
OpenRouter /chat/completions  ──model emits tool_call──►  executed over MCP, looped
     ▼
runs.jsonl  ──► score.py ──(getClass existence oracle)──► metrics table
```
The model only sees tools via the API function list — deciding *whether* and
*which* to call is a measured behaviour.

## Run

```bash
export OPENROUTER_API_KEY=...            # subjects
export ABEROWL_MCP_URL=https://beta.aber-owl.net/mcp/ontology/mcp   # the tool under test
export ABEROWL_API=https://beta.aber-owl.net/api                    # scorer existence oracle

python build_gold.py --out gold.jsonl --n 40      # candidate gold (REVIEW it, see below)
python harness.py   --gold gold.jsonl --out runs.jsonl
python score.py     --runs runs.jsonl [--by-difficulty]
```

## Harness constants in force during the released runs

From `config.py` as committed: `MAX_TOOL_TURNS = 6`, `TEMPERATURE = 0.0`. `config.py`
does **not** set `TOOL_RESULT_CHARS` or `TOOL_LOG_CHARS`; `harness.py` falls back to
its own defaults via `getattr(C, ..., default)`: `TOOL_RESULT_CHARS` defaults to
**4000** (what the model receives per tool result, `harness.py` line 88) while
`TOOL_LOG_CHARS` defaults to **600** (what gets written to `runs*.jsonl`, line 133).
Because the recorded value is a sixth of what the model saw, **every logged tool
response in these run files is a prefix**, not the full text the model reasoned
over. `truncated=True` (line 135, set after the `for _ in range(C.MAX_TOOL_TURNS):`
loop exits without an earlier return) means the model exhausted its 6-turn budget
without producing a final answer.

## Which service answered these runs

`config.py` defaults to beta (`MCP_URL` = `https://beta.aber-owl.net/mcp/ontology/mcp`,
`ABEROWL_API` = `https://beta.aber-owl.net/api`), matching the `## Run` example above.
None of `runs_full.jsonl` / `runs_pilot.jsonl` / `runs_rest.jsonl` carry a per-row
timestamp. The closest evidence is `rest.log`, which brackets the "rest" batch
between `START Thu Jul 2 19:53:14 +03 2026` and `DONE Thu Jul 2 22:58:49 +03 2026`;
`pilot.log`'s own mtime (2026-07-02) and the fact that `runs_full.jsonl` is the
pilot+rest merge point to the same day, so these released runs were executed on
**2026-07-02**. (The three `runs_*.jsonl` files' own mtimes read 2026-08-19; that
is `retry_errors.py`, which "re-run[s] only the error records in a runs file and
patch[es] them back in place," rewriting the file it is pointed at; it is a later
error-retry pass over the same rows, not the original run time.)

**The exact beta service commit on 2026-07-02 is not recorded in the run files**,
and neither `DEPLOY_FOLLOWUPS.md` nor `CHANGES.md` names a commit deployed to beta
on that date; the earliest beta-relevant entry in `DEPLOY_FOLLOWUPS.md` is a
frontend docs fix deployed 2026-07-14 (committed as `eecd6a7`), which postdates
this run by twelve days and does not describe beta's state on 2026-07-02.
**`kaustborg/aberowl-central:2.0` and `kaustborg/aberowl-worker:2.0` on Docker Hub
are the current published release, not necessarily the code that served these
runs**; see `DEPLOY_FOLLOWUPS.md`'s "Image tags" section, which documents the
`:2.0` tags being overwritten in place on 2026-08-20, more than a month after this
run.

## Open decisions (gold validity — needs review before running for real)
1. **Contamination** — subjects are recent (2026); L1/L2 famous terms may be
   memorized (tool "won't help"). The effect lives in L3/L4. Need genuinely
   hard/OOD terms — obscure ontologies (have) + ideally post-cutoff classes (TBD).
2. **Ontology set + sizes** per stratum (statistical power).
3. **Nonexistent construction** (L4b) — current near-miss mutation is crude;
   verify they truly don't resolve and are *plausible*.
4. **Gold review** — spot-check that mined L2 labels / L3 synonyms are correct and
   unambiguous (a term in 2 ontologies needs the scope pinned).
