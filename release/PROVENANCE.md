# Release provenance

The release cleanup is **merged to `main`** at commit
`1a71218434999b63dda88364d28c4fa019cdf657`. The beta service was updated and
validated on 18 September 2026 at `f6b65bd`, the commit carrying every code change
in this release; `1a71218` adds documentation and a scrub of the deployment host alias from the provisioning scripts on top. That validation found 859 of 859
ontologies online, with the central API, web interface, MCP endpoint, exact
identifier lookup, a DL reasoning query and a SPARQL rewrite all checked. The cleanup retired the standalone LLM query parsers
and the worker interface's LLM tab, removed 50 audited paths, sanitised retained
documentation, scripts and compose comments, retired an unreachable provisioning
endpoint, made the worker integration tests runnable, narrowed the worker proxy
to serve only the interface files, and scrubbed the deployment host alias from
the provisioning scripts after a privacy audit of the release directory.

The earlier archive predates all of this, retains the privacy findings the cleanup
fixed, and must not be published.

The working release candidate dated 18 September 2026 supports **offline rescoring of
saved responses**. Missing historical records prevent exact repetition of the model
calls and reconstruction of the services that answered them.

## Source revisions

The software release revision is `1a71218434999b63dda88364d28c4fa019cdf657`. The
audit that selected these files inspected `4f81f12f6c2a8105516cf51524d3049fff1f373f`,
the commit the cleanup was built on. The separate manuscript source revision is
`ed2c4e3baa1c6c254f7ef2fe1de2f7fedbe8be12`. The software package excludes the
manuscript repository and generated manuscript PDFs.

The archive selects software, data and documentation from the release revision.
Four packaging records contain metadata generated after that commit:
`release/provenance.json`, `release/PROVENANCE.md`, `release/contents.json` and
`release/SHA256SUMS`. These records identify the source revisions, file selection
and checksums of the exported bytes. The archive excludes local working changes
and Git history. The version DOI remains unset in [provenance.json](provenance.json).

[contents.json](contents.json) selects the package files. The release checksum
inventory identifies the exported bytes; the reproduction report independently
records hashes of scoring inputs. Neither reconstructs unrecorded historical service
or model versions. Dependency declarations describe this release's supported
environment. Historical environment records are incomplete.

## Evaluation evidence

| Artifact | What is preserved | Limits |
| --- | --- | --- |
| Grounding `gold.jsonl`, `gold_dedup.jsonl` | Original 173 rows and 162 distinct term/ontology pairs; positive identifiers and constructed negatives | We selected mined positives for resolver success, which limits the sample's coverage. |
| Grounding `runs_full.jsonl`, pilot/rest batches | Raw responses, prompts and partial tool logs; 3,450 saved full-file rows give 3,231 scored runs after filtering and collapse; 239 repeat pairs | Batches are described as July 2026 with later retries. Per-row timestamps, deployed commits and provider routing were not recorded. Pilot/rest files are historical sources, not extra observations to concatenate. |
| Grounding `iri_exists_resolved.json` | Later existence checks against production and beta on 19 August 2026 | Not the ontology state at each model call. |
| `direct_lookup/responses.jsonl` | 162 exact-resolver response bodies, request URLs, status codes and timestamps from beta on 25 and 30 August 2026 | Underlying exact resolver, not a replay of full MCP interaction or fuzzy suggestions. Original service commits and ontology checksums are absent. |
| Reasoning gold and `classes_*.txt` | 120 formal expressions, ELK-computed answers and identifier universes for GO, CL and SO | Identifier universes do not preserve OWL axioms or imports. Original OWL files/checksums and the SO release date were not retained. Original command-line sampling seed is unrecorded; the builder's default is not evidence that it was used. |
| Five complete reasoning run files | 2,400 responses from approximately August–September 2026 | Deployed service commits were not logged. Provider records are partial. |
| `service_gold_agreement.json` | Check at `2026-09-14T04:40:00Z`: all 120 gold expressions agreed with the service | Later agreement, not proof of original-run service state or an independent-reasoner comparison; reference and service both use ELK. |

Grounding passed up to 4,000 characters of a tool response to a model but logged at
most 600. Reasoning passed and logged at most 6,000; 11 of 2,017 recorded
reasoning-tool results reached that boundary. These limits prevent a claim of
complete, untruncated historical tool transcripts.

Reasoning gold construction recorded GO as 51,937 classes dated 2026-03-25, CL as
19,151 dated 2026-03-26, and SO as 2,752 with no recorded release date. The registry
supplied these dates. We did not retain checksummed OWL release archives. The
grounding configuration points to beta and the reasoning configuration to production.
Both services have changed since the model runs.

The experiments did not pin OpenRouter routing or log grounding provider routes.
Reasoning GPT-OSS-20B and Llama-4-Scout runs predate provider logging; the other
three model files record sets of provider names per run. They do not map each call to
its provider. Those records do not identify all serving configurations or
quantization settings. Model IDs alone are insufficient to reproduce provider
behavior.

The current offline command is:

```bash
python3 experiments/reproduce.py --out-dir /tmp/aberowl-reproduction
```

Choose an empty output directory. The current scorers implement the revised paper's
criteria, including exactly one correct exact-match IRI for positive baseline success
and an empty exact-match list for negative success. The latter is no-exact-match
detection, not tool abstention. Scorer revision dates and response-snapshot dates are
different kinds of provenance.

## Dated deployment evidence

The two files under `deploy/measurements/` preserve September 13 latency/restart
observations and September 14 reasoning-contract responses. The measurement note
records images published on September 14 from `2addd92`:

| Image | Recorded digest |
| --- | --- |
| `kaustborg/aberowl-central:2.0` | `sha256:3163da734d84db84d7776764bead805d5e2e43190d10f63b09c05a0422a7e3b2` |
| `kaustborg/aberowl-worker:2.0` | `sha256:9759dc16b8b561e513e0d38e2375bbe22f25f3f3d99e743c32f817c5c41d2d6b` |

These digests identify the images recorded in the dated deployment evidence. They do
not identify the images used for the earlier model runs. Tags `2.0` and `latest` are
mutable. We did not recheck image availability during the offline release cleanup.
