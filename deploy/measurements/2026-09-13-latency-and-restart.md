# Production measurements, 13 September 2026

Recorded for the AberOWL 2 paper (deployment paragraph). Hostnames and paths omitted.

## Response time, median of 15 sequential calls (`scripts/probe_latency.py`)

| request | on the central host (`127.0.0.1:8000`) | external client via `https://aber-owl.net` |
|---|---|---|
| `/api/resolve` "cell death", GO | 57 ms (p95 64) | 827 ms (p95 937) |
| `/api/dlquery_all` GO:0008219 subeq, 97 answers | 39 ms (p95 51) | 1,054 ms (p95 1,306) |
| `/api/dlquery_all` HP:0000118 subeq, 18,690 answers | 1,090 ms (p95 1,122) | 4,902 ms (p95 7,540) |
| `/api/search_all` "apoptosis", all ontologies | 1,034 ms (p95 1,130) | 2,073 ms (p95 2,210) |

The external client was a laptop outside the KAUST network; the public HTTPS path runs
through a reverse proxy and an SSH tunnel before reaching the central host. The
differences between the two columns are reported as observed, without attributing them
to a single cause.

## Worker restart

`aberowl-worker-19` (MeSH 355,408 classes + BERO 392,307 classes) was restarted at
15:12:40 local time; the worker logged "Ontology loading sequence complete" 435 s later
and answered `listLoadedOntologies` with both ontologies classified. Resident memory after
reload: 14.1 GiB. The other 13 containers on that host kept answering throughout.

## Fleet

Two worker hosts (16 cores, 157 GB each): 13 + 14 containers, 607 + 364 ontologies,
container RSS from 2.0 GiB to 31.6 GiB. Central host: 8 cores, 15 GB; Elasticsearch
6.9 GiB of 8 GiB, API server 184 MiB, Redis 17 MiB. Registry: 971 ontologies,
12,115,179 classes; statuses 958 classified, 6 incoherent (structural reasoner), 7 unknown.

## Reasoning-contract fields live (14 September 2026)

After the full worker restart (27 containers, on-host sequential loop, 05:13 to 06:00 UTC),
`/api/getOntology` and the MCP `get_ontology_info` tool report the active reasoner for all 971
ontologies. Saved responses for a classified ontology (GO: `reasoner_active: elk`), a fallback
ontology (FMA: `reasoner_active: structural`, `reasoner_configured: elk`, status incoherent) and
a failed one (cu-vo: `reasoner_active: none`, status error) are in
`2026-09-14-reasoning-contract-evidence.json`. Registry at that time: 971 online, 958
classified, 6 incoherent, 7 error.

## Published images (14 September 2026)

Built from `main` at `2addd92` and pushed to Docker Hub as `2.0` and `latest`:

| image | digest |
|---|---|
| `kaustborg/aberowl-central:2.0` | `sha256:3163da734d84db84d7776764bead805d5e2e43190d10f63b09c05a0422a7e3b2` |
| `kaustborg/aberowl-worker:2.0` | `sha256:9759dc16b8b561e513e0d38e2375bbe22f25f3f3d99e743c32f817c5c41d2d6b` |

The central image carries the reasoning-contract fields, the result-cap propagation and the
direct-neighbour browsing (#129, #130, #133) and no registry seed file. Production central runs
the same commit; the workers run the same `aberowlapi/` sources bind-mounted from that commit.
