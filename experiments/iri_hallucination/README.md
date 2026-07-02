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

## Open decisions (gold validity — needs review before running for real)
1. **Contamination** — subjects are recent (2026); L1/L2 famous terms may be
   memorized (tool "won't help"). The effect lives in L3/L4. Need genuinely
   hard/OOD terms — obscure ontologies (have) + ideally post-cutoff classes (TBD).
2. **Ontology set + sizes** per stratum (statistical power).
3. **Nonexistent construction** (L4b) — current near-miss mutation is crude;
   verify they truly don't resolve and are *plausible*.
4. **Gold review** — spot-check that mined L2 labels / L3 synonyms are correct and
   unambiguous (a term in 2 ontologies needs the scope pinned).
