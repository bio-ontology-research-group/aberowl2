# Fleet redistribution — measured memory ledger (2026-06-04)

Empirical right-sizing during the xs-first deploy. For each worker we launch
generous (`--memory=16g -Xmx12g`), load + warm with sample queries (findRoot +
subclass DL query per ontology), force a full GC to read the true live heap,
then recreate trimmed and record the result here.

Key insight: warm RSS at a generous -Xmx is inflated by deferred G1 garbage and
never returned to the OS. The **live heap after a forced full GC** is the real
working-set signal. Deployed size ≈ ~2× live heap for -Xmx, +2 G non-heap for
the container limit.

| physical | plan-w | bucket | cfg onts | loaded | plan est | live heap (post-GC) | warm RSS @16G | **deployed** | post-load RSS @deployed | notes |
|----------|--------|--------|----------|--------|----------|---------------------|---------------|--------------|-------------------------|-------|
| worker-34 | 31 | xs | 66 | 60 | 9 G | **3.15 G** | 9.4 G | **8 G / -Xmx6g** | 6.09 G (76%) | 6 failed to load (import/parse errors): teo, apadisorders, materialsmine, msv, ope, oboe-sbc. Repointed 60, routing verified. RSS 6.4G/8G stable after 2 days under real traffic. |
| worker-35 | 33 | xs | 149 | 149 | 8 G | **1.98 G** | — | **8 G / -Xmx6g** | 3.7 G (47%) | **`rdl` (218 MB, 203 cls) PULLED** — it alone drove 11 G live and crash-looped even at 16 G. Without it, 149 onts load in 48 s at 1.98 G. After loader fix, all import-failures load. Repointed 149. |
| worker-37 | giants | dedicated | 6 | 6 | — | **30 G** | — | **48 G / -Xmx40g** | 38 G (80%) | Quarantined ABox-heavy giants: rdl, lcgft, ror, fast-title, xref-funder-reg, nlmvs (huge files, <205 cls each). Repointed. Kept 48G (30G live needs ~40G heap; ~right-sized). Some return empty owl:Thing roots (import-suppression dropped their imported root) — online but flat, accepted tradeoff. Pinned in plan_distribution.py + plan_2026-06-08.json. |
| worker-38 | 35 | xs | 88 | 88 | 7 G | **4.24 G** | — | **10 G / -Xmx8g** | 6.9 G (69%) | plan-w35 minus its 5 giants. All 88 load (import fix). Repointed. Higher live than other xs workers — includes some heftier onts. |
| worker-39 | 30 | s | 33 | 33 | 13 G | **1.97 G** | — | **7 G / -Xmx5g** | 3.5 G (50%) | s-bucket new worker, no giants. All 33 load, repointed. Estimate (13G) ~7x over live. |

## Observations / running conclusions

- **xs over-provisioned ~3×.** 60 small ontologies → 3.15 G live, not 9 G.
  Plan's xs estimates are heavily padded. Trimmed worker-34 from the 9 G plan
  estimate (16 G measurement alloc) down to 8 G.
- **Caveat before generalizing:** live-set ratio will differ for large l/xl
  reasoners (big ontologies = big live sets). Must re-measure a representative
  large worker before trimming those buckets.
- **Load failures are real even with files on disk:** import-resolution and
  parse errors drop ontologies at load time. Repoint only the verified-loaded
  set (listLoadedOntologies), never the config set.
- **Repoint triggers a central metadata-refresh storm:** the central server
  async-fetches getStatistics for each repointed ontology. During that window
  (~30-60 s) central dlquery can briefly return empty even though the worker
  serves it directly. Wait before verifying routing, or re-test.
- **Live heap doesn't track ontology count** — it tracks total class/axiom
  volume. worker-35 (123 tiny onts) = 2.0 G < worker-34 (60 onts) = 3.15 G.
  So per-worker measurement is necessary; can't extrapolate from count alone.
- **Loader fix (RequestManager.groovy):** owl:imports now suppressed entirely
  (SILENT + IRI mapper → /data/.noimport/ non-existent paths, no network). Dead
  imports were hanging loads 600+s; reachable ones OOM'd workers. Measure on the
  FULL load now — partial-load trims undersize workers whose recovered onts are
  big. Restart a worker to pick up the loader (mounted source).
- **Mis-bucketing:** planner buckets by class count; ABox-heavy ontologies are
  huge files with few classes → land in xs → OOM. Quarantine by FILE SIZE. The 6
  giants (rdl/lcgft/ror/fast-title/xref-funder-reg/nlmvs) → dedicated worker-37
  (30 G live combined). Pull giants from any small-worker config before launch.
