# Deterministic grounding baseline

This baseline scores the exact resolver underlying `find_iri` on the same
162 term/ontology inputs used in the grounding experiment. It uses saved
beta responses fetched on 25 and 30 August 2026, with no LLM in the loop.
It does not replay the complete MCP interaction or its fuzzy suggestions.

## Reproduce offline

From the repository root, reproduce all reported evaluation results:

```bash
python3 experiments/reproduce.py --out-dir results/paper-reproduction
```

The output directory must be empty. Baseline outputs appear in its
`direct_lookup/` subdirectory. Python 3.10 or later and the standard library
suffice; no network, service or credentials are needed.

To score only this baseline, run from this directory:

```bash
python3 score.py --out-dir /tmp/aberowl-direct-lookup
```

The scorer reads `responses.jsonl` and `../gold_dedup.jsonl`. It writes
`scored.jsonl` with per-item outcomes and `summary.json` with counts, response
dates and input SHA-256 hashes. `--gold` and `--responses` can override the
input paths. With no `--out-dir`, the scorer refreshes those two derived
outputs in this directory; it never changes the raw response snapshot.

## Success criteria and results

Each request supplies the term and its ontology scope. The scorer collects the
distinct IRIs in the exact-match response, then compares them with gold. The
scorer does not choose between multiple matches.

| Item type | Success criterion | Successful items |
| --- | --- | ---: |
| Positive | Exactly one distinct exact-match IRI, equal to gold | 117/122 (95.9%) |
| Constructed negative | Empty exact-match list | 40/40 (100%) |

Five positive terms each have two exact matches and count as unsuccessful:
apoptosis, diabetes mellitus, cell fusion, negative regulation of retinal
cell programmed cell death, and peripheral blood mononuclear cell. Their
gold identifier occurs in the returned list, but that does not satisfy the
single-match criterion. There are no positive responses with an empty list
or a single incorrect match.

For negatives, the outcome is **no-exact-match detection**. `find_iri` can
still offer fuzzy suggestions after an empty exact result; the caller
must decide whether to abstain. This result must not be labelled as 40
abstentions by the tool.

The summary identifies the measure as `single_correct_exact_match`.
Its main count fields are `positive_correct`, `positive_ambiguous` and
`negative_no_exact_match`. Per-item `outcome` and `success` fields make the
counts auditable. Failed requests, missing or duplicate items, malformed
responses and potentially capped responses cause an error rather than
counting as successful negative detections.

## Provenance and interpretation

`responses.jsonl` contains the original 162 cached response bodies, request
URLs, HTTP status codes and timestamps, with term/ontology keys added. The
snapshot is self-contained. Every request used a limit of 25 records and
returned at most two, so the responses did not reach that limit.

Benchmark construction selected mined positive terms for resolver success.
These results therefore describe this selected set, not general resolver
coverage. Agents can disambiguate cases that fail the single-match criterion,
so this baseline is not an upper bound on agent accuracy. The logs omit
historical service commits and original ontology checksums; the cached
responses do not reconstruct the service state during the model runs. Score
differences cannot be attributed entirely to agent behavior.