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
