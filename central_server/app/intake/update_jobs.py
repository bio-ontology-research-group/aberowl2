"""
Where heavy update work runs, and that only one runs at a time.

An update, a reindex and a batch check download, hash, validate, index and
hot-swap ontologies (updater.py). UPDATE_EXECUTION selects where:

  inline   The API process runs them, as before queued mode existed. Kept
           for compatibility; supported with exactly one API process and no
           runner. One heavy job at a time in that process, across every
           trigger (manual, webhook, batch, scheduled, reindex); a second
           is refused with HTTP 409. This is the default when unset.
  queued   The API process only records a job in Redis. A separate runner
           process (app.update_runner) claims and runs it. One job at a time
           across every API and runner process sharing the namespace.

Any other value, or an invalid setting below, refuses every update request
(HTTP 503) and stops the runner; there is no fallback to inline.

Global ownership. The job that runs holds the global slot, the state hash
field "active" (queued) or "inline" (inline), from its claim until its
outcome is known. A known outcome releases it. An unknown one keeps it: a
transaction left recovery_required, an exception outside the pipeline's own
handling, a cancelled, crashed or unreachable runner. Nothing heavy starts
while the slot is held, and nothing releases it because time passed: an
operator reconciles and POSTs /admin/update_jobs/{id}/recover. Inline and
queued execution exclude each other through the same hash, so a runner never
claims while an inline run holds it, and an inline run refuses while a job
holds it or a runner sends heartbeats.

Fencing. The runner hands the pipeline a FencedRedis for its job: every
registry write is a transaction that applies only while the job holds the
slot with its claim, running, and every phase boundary and step with
effects outside Redis (worker requests, index creation and deletion, the
alias, file promotion and removal) first checks the claim
(updater._checkpoint). The runner's heartbeat checks it too, and cancels the
job when the claim is gone or Redis has been unreachable for half the stale
period. So a runner whose job was released writes nothing more to the
registry or the job, and starts no further step.

What this cannot enforce: a request already sent, or a worker task already
started, is not undone; a checkpoint and the step after it are one round
trip apart; and a stopped (paused) process checks nothing until it runs
again. The recovery endpoint therefore refuses a job whose runner sent a
heartbeat within UPDATE_RUNNER_STALE_SECONDS, and needs the operator's
confirmation that the runner is stopped and the state reconciled.

API writers. In queued mode the API's own registry writes (status poll,
source sync, metadata fallbacks, registration, source, disable, enable,
resolve) go through JobQueue.mutate_entry: a transaction on the newest
entry, so a stale copy never overwrites the runner's update_txn,
quarantine or version markers.

Redis layout, under UPDATE_QUEUE_PREFIX (default aberowl:update_jobs):
  <p>:state     hash: active (claim JSON), inline (marker JSON, "" when
                none), fence (claim counter), last_scheduled
  <p>:jobs      hash: job id -> job JSON
  <p>:pending   list: queued job ids, oldest first
  <p>:dedupe    hash: request key -> id of the queued or running job
  <p>:finished  list: ended job ids, newest first; UPDATE_JOB_RETAIN kept
  <p>:runners   hash: runner id -> last heartbeat JSON
Every change to these keys except heartbeats and the inline marker is a
WATCH/MULTI/EXEC transaction, so concurrent API and runner processes see one
order of events.
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Tuple

from redis.exceptions import WatchError

from app.intake import updater

logger = logging.getLogger(__name__)

REGISTRY_KEY = "registered_servers"
MODES = ("inline", "queued")
# Job states. A job in a holding state owns the global slot.
QUEUED, RUNNING, RECOVERY_REQUIRED = "queued", "running", "recovery_required"
DONE, CANCELLED, RECOVERED = "done", "cancelled", "recovered"
ENDED = (DONE, CANCELLED, RECOVERED)
KINDS = ("update", "reindex", "check")
TX_ATTEMPTS = 50


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    mode: str                 # "inline", "queued" or "invalid"
    error: Optional[str]
    redis_url: str
    prefix: str
    wait_seconds: float       # longest ?wait=true wait for a queued job
    queue_max: int            # queued jobs accepted at once
    retain: int               # ended jobs kept for status
    stale_seconds: float      # a runner heartbeat older than this is stale
    heartbeat_seconds: float
    poll_seconds: float
    max_download_bytes: Optional[int] = None
    max_expanded_bytes: Optional[int] = None

    def shared(self) -> Dict[str, Any]:
        """The settings an API process and a runner must agree on. A runner
        reports them in its heartbeat; GET /admin/update_jobs flags a
        difference. (A different UPDATE_QUEUE_PREFIX cannot be flagged: the
        two would not see each other at all.)"""
        return {"stale_seconds": self.stale_seconds, "heartbeat_seconds": self.heartbeat_seconds,
                "max_download_bytes": self.max_download_bytes, "max_expanded_bytes": self.max_expanded_bytes}


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    env = os.environ if env is None else env
    errors: List[str] = []

    def number(name: str, default: float, low: float, high: float) -> float:
        raw = (env.get(name) or "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            errors.append(f"{name}={raw!r} is not a number")
            return default
        if not low <= value <= high:
            errors.append(f"{name}={raw} is outside {low:g}..{high:g}")
            return default
        return value

    raw_mode = (env.get("UPDATE_EXECUTION") or "inline").strip().lower()
    if raw_mode not in MODES:
        errors.append(f"UPDATE_EXECUTION={raw_mode!r}: use inline or queued")
    prefix = (env.get("UPDATE_QUEUE_PREFIX") or "aberowl:update_jobs").strip()
    if not prefix or any(c.isspace() for c in prefix):
        errors.append("UPDATE_QUEUE_PREFIX must be a non-empty string without spaces")
    heartbeat = number("UPDATE_RUNNER_HEARTBEAT_SECONDS", 15, 1, 600)
    stale = number("UPDATE_RUNNER_STALE_SECONDS", 120, 5, 86400)
    if stale < 3 * heartbeat:
        errors.append("UPDATE_RUNNER_STALE_SECONDS must be at least three heartbeats")
    wait_seconds = number("UPDATE_WAIT_SECONDS", 25, 0, 600)
    queue_max = int(number("UPDATE_QUEUE_MAX", 100, 1, 10000))
    retain = int(number("UPDATE_JOB_RETAIN", 200, 1, 10000))
    poll_seconds = number("UPDATE_RUNNER_POLL_SECONDS", 5, 0.1, 600)
    limits = {}
    for name, default in (("UPDATE_MAX_DOWNLOAD_BYTES", 4 << 30), ("UPDATE_MAX_EXPANDED_BYTES", 8 << 30)):
        limits[name] = updater.byte_limit(env.get(name), default)
        if limits[name] is None:
            errors.append(f"{name}={env.get(name)!r} is not a whole number of bytes from 1 to 1 TiB")
    return Settings(
        mode=raw_mode if not errors else "invalid",
        error="; ".join(errors) or None,
        redis_url=(env.get("REDIS_URL") or "redis://redis").strip(),
        prefix=prefix,
        wait_seconds=wait_seconds,
        queue_max=queue_max,
        retain=retain,
        stale_seconds=stale,
        heartbeat_seconds=heartbeat,
        poll_seconds=poll_seconds,
        max_download_bytes=limits["UPDATE_MAX_DOWNLOAD_BYTES"],
        max_expanded_bytes=limits["UPDATE_MAX_EXPANDED_BYTES"],
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

class QueueError(Exception):
    """A refused request: status is the HTTP status, detail a str or dict."""

    def __init__(self, status: int, detail: Any):
        super().__init__(detail if isinstance(detail, str) else json.dumps(detail))
        self.status = status
        self.detail = detail


class OwnershipLost(Exception):
    """The claim this runner holds is no longer the one in the global slot."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age(stamp: Optional[str]) -> Optional[float]:
    if not stamp:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return None


def _load(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _dump(value: Dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def new_job_id(prefix: str = "uj") -> str:
    return f"{prefix}-" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S") + "-" + secrets.token_hex(3)


def request_key(kind: str, ontologies: List[str], force: bool) -> str:
    """Equal requests share a key: a duplicate of a queued or running job
    returns that job."""
    ids = sorted(set(ontologies))
    digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:24]
    return f"{kind}:{int(bool(force))}:{len(ids)}:{digest}"


_SECRET_WORDS = ("secret", "token", "password", "authorization", "apikey", "api_key", "credential")


def sanitize(value: Any) -> Any:
    """A pipeline outcome as stored on a job: no field whose name says it
    holds a credential. Outcomes carry none today; this keeps it so."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if any(w in str(k).lower() for w in _SECRET_WORDS) else sanitize(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def holds_ownership(result: Optional[Dict[str, Any]]) -> bool:
    """Whether an outcome leaves the global slot held: the run left an
    unresolved transaction, or it ended outside the pipeline's handling."""
    return bool(result) and bool(result.get("recovery_required") or result.get("unknown_outcome"))


def touched(record: Dict[str, Any]) -> List[str]:
    """Ontologies whose state a holding job may have left unknown."""
    ids = []
    if record.get("kind") in ("update", "reindex"):
        ids.extend(record.get("ontologies") or [])
    ids.extend(x for x in (record.get("current"), record.get("held_by")) if x)
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------------------------
# Running one update, reindex or batch (inline mode and the runner)
# ---------------------------------------------------------------------------

async def run_one(kind: str, ontology_id: str, redis_client, es_mgr, ontologies_dir: str, es_url: str,
                  force: bool = False) -> Dict[str, Any]:
    """One update or reindex. An exception that escapes the pipeline (it
    converts its own inside a transaction) is an unknown outcome."""
    raw = await redis_client.hget(REGISTRY_KEY, ontology_id)
    entry = json.loads(raw) if raw else {"ontology_id": ontology_id}
    try:
        if kind == "reindex":
            return await updater.execute_reindex(
                ontology_id=ontology_id, registry_entry=entry, redis_client=redis_client, es_mgr=es_mgr,
                ontologies_base_path=ontologies_dir, es_url=es_url)
        return await updater.execute_update_pipeline(
            ontology_id=ontology_id, registry_entry=entry, redis_client=redis_client, es_mgr=es_mgr,
            ontologies_base_path=ontologies_dir, es_url=es_url, force=force)
    except OwnershipLost:
        raise  # the runner stops; nothing about this run may be written
    except Exception as e:
        logger.exception("%s of %s raised", kind, ontology_id)
        return {"success": False, "status": "failed", "error": f"unhandled: {e}", "message": "",
                "recovery_required": False, "unknown_outcome": True}


def new_summary() -> Dict[str, Any]:
    return {"updated": [], "unchanged": [], "failed": {}, "skipped": {}, "not_run": []}


async def select(redis_client, selected: Optional[List[str]]) -> Tuple[List[str], List[str]]:
    """The registered ids among selected (all registered ids without a
    selection), and the selected ids that are not registered."""
    all_keys = await redis_client.hkeys(REGISTRY_KEY)
    if not selected:
        return list(all_keys), []
    known = set(all_keys)
    wanted = list(dict.fromkeys(selected))
    return [k for k in wanted if k in known], [k for k in wanted if k not in known]


async def check_one(ontology_id: str, redis_client, es_mgr, ontologies_dir: str, es_url: str
                    ) -> Tuple[str, Any, Optional[Dict[str, Any]]]:
    """One ontology of a batch check: (bucket, detail, pipeline outcome)."""
    raw = await redis_client.hget(REGISTRY_KEY, ontology_id)
    entry = json.loads(raw) if raw else None
    if not entry or not entry.get("source_url"):
        return "skipped", "no_source_url", None
    if entry.get("update_status") == "disabled":
        return "skipped", "disabled", None
    if entry.get("update_txn"):
        return "skipped", "recovery_required", None
    if updater.update_in_progress(ontology_id):
        return "skipped", "update_in_progress", None
    logger.info("Checking update for %s", ontology_id)
    result = await run_one("update", ontology_id, redis_client, es_mgr, ontologies_dir, es_url)
    status = result.get("status")
    logger.info("%s: update check %s %s", ontology_id, status, result.get("error") or "")
    if status in ("updated", "unchanged"):
        return status, None, result
    if status == "skipped":
        return "skipped", result.get("error"), result
    return "failed", result.get("error"), result


async def run_check(ontologies: List[str], summary: Dict[str, Any], redis_client, es_mgr,
                    ontologies_dir: str, es_url: str,
                    before: Optional[Callable[[str], Awaitable[bool]]] = None,
                    after: Optional[Callable[[str], Awaitable[None]]] = None) -> Dict[str, Any]:
    """Check the ontologies one after another, recording each in summary.
    before(oid) returning False stops the batch (cancelled); an outcome that
    holds the global slot stops it too. The ids not reached go to not_run.
    Returns {"stop": None | "cancelled" | "held", "held_by": id or None}."""
    for i, oid in enumerate(ontologies):
        if before is not None and not await before(oid):
            summary["not_run"] = list(ontologies[i:])
            return {"stop": "cancelled", "held_by": None}
        bucket, detail, result = await check_one(oid, redis_client, es_mgr, ontologies_dir, es_url)
        if bucket in ("updated", "unchanged"):
            summary[bucket].append(oid)
        else:
            summary[bucket][oid] = detail
        if after is not None:
            await after(oid)
        if result is not None and holds_ownership(result):
            summary["not_run"] = list(ontologies[i + 1:])
            logger.error("Batch check stopped at %s: its outcome is unknown; the rest is not run", oid)
            return {"stop": "held", "held_by": oid}
    return {"stop": None, "held_by": None}


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

class JobQueue:
    """Jobs and the global slot in Redis. Cheap to construct; holds no state
    of its own."""

    def __init__(self, redis_client, settings: Settings):
        self.r = redis_client
        self.s = settings
        p = settings.prefix
        self.k_state = f"{p}:state"
        self.k_jobs = f"{p}:jobs"
        self.k_pending = f"{p}:pending"
        self.k_dedupe = f"{p}:dedupe"
        self.k_finished = f"{p}:finished"
        self.k_runners = f"{p}:runners"

    async def _tx(self, keys: List[str], body):
        """Run body(pipe) with keys watched. body reads through pipe and
        returns (result, write); write(pipe), if given, queues the changes,
        which apply only if no watched key changed meanwhile."""
        for _ in range(TX_ATTEMPTS):
            async with self.r.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(*keys)
                    result, write = await body(pipe)
                    if write is not None:
                        pipe.multi()
                        write(pipe)
                        await pipe.execute()
                    return result
                except WatchError:
                    pass
            await asyncio.sleep(random.uniform(0.002, 0.05))
        raise QueueError(503, "the update queue is busy; try again")

    # Reads ---------------------------------------------------------------

    async def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        return _load(await self.r.hget(self.k_jobs, job_id))

    async def pending_count(self) -> int:
        return int(await self.r.llen(self.k_pending))

    async def holder(self) -> Optional[Dict[str, Any]]:
        """The global slot's holder, active job or inline marker, with the
        fields guards need. Uses plain hash reads only."""
        active = _load(await self.r.hget(self.k_state, "active"))
        if active:
            job = _load(await self.r.hget(self.k_jobs, active.get("job", ""))) or {}
            return {**job, "job": active.get("job"), "state": active.get("state"), "where": "active",
                    "runner": active.get("runner"), "claim": active.get("claim")}
        inline = _load(await self.r.hget(self.k_state, "inline"))
        if inline:
            return {**inline, "where": "inline"}
        return None

    async def holder_for(self, ontology_id: str) -> Optional[Dict[str, Any]]:
        holder = await self.holder()
        if holder and ontology_id in touched(holder):
            return holder
        return None

    async def fresh_runners(self) -> List[Dict[str, Any]]:
        fresh = []
        for raw in await self.r.hvals(self.k_runners):
            beat = _load(raw)
            age = _age((beat or {}).get("at"))
            if beat and age is not None and age < self.s.stale_seconds:
                fresh.append({**beat, "age_seconds": round(age, 1)})
        return fresh

    async def unresolved_transaction(self) -> Optional[str]:
        """An ontology with an update_txn, which keeps every job waiting."""
        for raw in await self.r.hvals(REGISTRY_KEY):
            entry = _load(raw) or {}
            txn = entry.get("update_txn")
            if txn:
                oid = entry.get("ontology_id") or entry.get("ontology")
                return f"{oid} has the unresolved transaction {txn.get('id')} ({txn.get('state')})"
        return None

    async def status(self, limit: int = 20) -> Dict[str, Any]:
        state = await self.r.hgetall(self.k_state)
        pending_ids = await self.r.lrange(self.k_pending, 0, limit - 1)
        finished_ids = await self.r.lrange(self.k_finished, 0, limit - 1)
        runners = []
        for rid, raw in (await self.r.hgetall(self.k_runners)).items():
            beat = _load(raw) or {}
            age = _age(beat.get("at"))
            runners.append({"runner": rid, **beat, "age_seconds": None if age is None else round(age, 1),
                            "fresh": age is not None and age < self.s.stale_seconds,
                            "settings_match": beat.get("settings") == self.s.shared()})
        active = _load(state.get("active"))
        if active:
            active["job_record"] = brief(await self.get(active["job"]))
        return {
            "active": active,
            "inline": _load(state.get("inline")),
            "pending_count": await self.pending_count(),
            "pending": [brief(await self.get(i)) for i in pending_ids],
            "recent": [brief(await self.get(i)) for i in finished_ids],
            "runners": runners,
            "last_scheduled": state.get("last_scheduled"),
        }

    # Submission ----------------------------------------------------------

    async def enqueue(self, kind: str, ontologies: List[str], *, force: bool = False, source: str = "admin",
                      requested: Any = None, skipped: Optional[Dict[str, str]] = None,
                      schedule_interval: Optional[float] = None) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Queue a job; (job, duplicate). An equal request whose job is still
        queued or running returns that job. With schedule_interval, nothing
        is queued (None, False) when the last scheduled submission is more
        recent than that, so restarts and several API processes do not
        resubmit the scheduled batch."""
        if kind not in KINDS:
            raise ValueError(kind)
        key = request_key(kind, ontologies, force)

        async def body(pipe):
            if schedule_interval is not None:
                age = _age(await pipe.hget(self.k_state, "last_scheduled"))
                if age is not None and age < schedule_interval:
                    return (None, False), None
            existing = await pipe.hget(self.k_dedupe, key)
            if existing:
                job = _load(await pipe.hget(self.k_jobs, existing))
                if job and job.get("state") in (QUEUED, RUNNING):
                    return (job, True), None
            if int(await pipe.llen(self.k_pending)) >= self.s.queue_max:
                raise QueueError(503, f"{self.s.queue_max} jobs are queued already; wait or cancel some")
            job = {"id": new_job_id(), "kind": kind, "ontologies": list(ontologies), "force": bool(force),
                   "source": source, "requested": requested, "state": QUEUED, "created": _now(),
                   "request_key": key, "events": []}
            if kind == "check":
                job["summary"] = new_summary()
                job["summary"]["skipped"].update(skipped or {})

            def write(p):
                p.hset(self.k_jobs, job["id"], _dump(job))
                p.hset(self.k_dedupe, key, job["id"])
                p.rpush(self.k_pending, job["id"])
                if schedule_interval is not None:
                    p.hset(self.k_state, "last_scheduled", job["created"])
            return (job, False), write

        keys = [self.k_dedupe, self.k_jobs, self.k_pending, self.k_state]
        return await self._tx(keys, body)

    async def cancel(self, job_id: str, note: str = "") -> Dict[str, Any]:
        """A queued job ends now. A running batch stops before its next
        ontology. A running update or reindex cannot be cancelled."""
        async def body(pipe):
            job = _load(await pipe.hget(self.k_jobs, job_id))
            if not job:
                raise QueueError(404, f"no job {job_id}")
            event = {"at": _now(), "event": "cancel", "note": note}
            if job["state"] == QUEUED:
                job.update(state=CANCELLED, finished=_now())
                job["events"].append(event)
                release = await self._release_reads(pipe, job)

                def write(p):
                    p.lrem(self.k_pending, 0, job_id)
                    release(p)
                return job, write
            if job["state"] == RUNNING and job["kind"] == "check":
                job["cancel_requested"] = True
                job["events"].append(event)
                return job, lambda p: p.hset(self.k_jobs, job_id, _dump(job))
            if job["state"] == RUNNING:
                raise QueueError(409, f"{job_id} is a running {job['kind']}; it cannot be stopped part way. "
                                      "It ends on its own, or stops for recovery")
            raise QueueError(409, f"{job_id} is {job['state']}")

        return await self._tx([self.k_jobs, self.k_pending, self.k_dedupe, self.k_finished, self.k_state], body)

    # Runner side ----------------------------------------------------------

    async def claim(self, runner_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Take the oldest queued job and the global slot, atomically.
        Returns (job, None), (None, why nothing was claimed) or (None, None)
        when nothing is queued."""
        async def body(pipe):
            state = await pipe.hgetall(self.k_state)
            active = _load(state.get("active"))
            if active:
                return (None, f"job {active.get('job')} holds the global slot ({active.get('state')})"), None
            inline = _load(state.get("inline"))
            if inline:
                return (None, f"the inline execution {inline.get('job')} holds the global slot "
                              f"({inline.get('state')})"), None
            job_id = await pipe.lindex(self.k_pending, 0)
            if job_id is None:
                return (None, None), None
            job = _load(await pipe.hget(self.k_jobs, job_id))
            if not job or job.get("state") != QUEUED:
                # Cancelled or pruned: drop it and look again.
                return (None, "skip"), lambda p: p.lpop(self.k_pending)
            fence = int(state.get("fence") or 0) + 1
            claim = f"{fence}:{runner_id}"
            now = _now()
            job.update(state=RUNNING, claimed=now, runner=runner_id, claim=claim)
            active = {"job": job_id, "claim": claim, "runner": runner_id, "since": now, "state": RUNNING}

            def write(p):
                p.lpop(self.k_pending)
                p.hset(self.k_jobs, job_id, _dump(job))
                p.hset(self.k_state, mapping={"active": _dump(active), "fence": fence})
            return (job, None), write

        for _ in range(TX_ATTEMPTS):
            job, reason = await self._tx([self.k_state, self.k_pending, self.k_jobs], body)
            if reason != "skip":
                return job, reason
        return None, "too many stale queue entries; read GET /admin/update_jobs"

    async def holds(self, job_id: str, claim: str) -> bool:
        """Whether job_id still holds the global slot with claim, running."""
        return _holds(_load(await self.r.hget(self.k_state, "active")), job_id, claim)

    async def _owned(self, pipe, job_id: str, claim: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        active = _load(await pipe.hget(self.k_state, "active"))
        if not _holds(active, job_id, claim):
            raise OwnershipLost(f"job {job_id}: claim {claim} no longer holds the global slot")
        job = _load(await pipe.hget(self.k_jobs, job_id))
        if not job or job.get("state") != RUNNING:
            raise OwnershipLost(f"job {job_id} is no longer running")
        return job, active

    async def progress(self, job_id: str, claim: str, **fields) -> Dict[str, Any]:
        """Record progress of the job this runner holds; raises OwnershipLost
        when it no longer holds it."""
        async def body(pipe):
            job, _ = await self._owned(pipe, job_id, claim)
            job.update(fields, updated=_now())
            return job, lambda p: p.hset(self.k_jobs, job_id, _dump(job))
        return await self._tx([self.k_state, self.k_jobs], body)

    async def finish(self, job_id: str, claim: str, hold: bool, state: str = DONE, **fields) -> Dict[str, Any]:
        """End the job this runner holds. hold keeps the global slot (the
        job becomes recovery_required); otherwise it is released."""
        async def body(pipe):
            job, active = await self._owned(pipe, job_id, claim)
            job.update(fields, updated=_now())
            if hold:
                job["state"] = RECOVERY_REQUIRED
                active["state"] = RECOVERY_REQUIRED
                dedupe = await pipe.hget(self.k_dedupe, job.get("request_key", ""))

                def write(p):
                    p.hset(self.k_jobs, job_id, _dump(job))
                    p.hset(self.k_state, "active", _dump(active))
                    if dedupe == job_id:
                        p.hdel(self.k_dedupe, job["request_key"])
                return job, write
            job.update(state=state, finished=_now())
            release = await self._release_reads(pipe, job)
            return job, release
        return await self._tx([self.k_state, self.k_jobs, self.k_dedupe, self.k_finished], body)

    async def _release_reads(self, pipe, job: Dict[str, Any]):
        """Read, under the caller's watch, what ending a job changes; return
        the writes: store it, retire it to finished (dropping the oldest
        beyond UPDATE_JOB_RETAIN), clear its dedupe key and, if it holds
        the global slot, the slot."""
        job_id = job["id"]
        evict = await self._evicted(pipe, job_id)
        dedupe = await pipe.hget(self.k_dedupe, job.get("request_key") or "")
        active = _load(await pipe.hget(self.k_state, "active"))

        def write(p):
            p.hset(self.k_jobs, job_id, _dump(job))
            self._retire(p, job_id, evict)
            if dedupe == job_id:
                p.hdel(self.k_dedupe, job["request_key"])
            if active and active.get("job") == job_id:
                p.hdel(self.k_state, "active")
        return write

    async def _evicted(self, pipe, job_id: str) -> List[str]:
        """Ended jobs that fall out of retention when job_id is added."""
        ids = await pipe.lrange(self.k_finished, self.s.retain - 1, -1)
        return [i for i in ids if i != job_id]

    def _retire(self, p, job_id: str, evict: List[str]) -> None:
        p.lpush(self.k_finished, job_id)
        p.ltrim(self.k_finished, 0, self.s.retain - 1)
        if evict:
            p.hdel(self.k_jobs, *evict)

    async def heartbeat(self, runner_id: str, info: Dict[str, Any]) -> None:
        """Record a runner's heartbeat, and drop the entries of runners
        silent for a day (at least ten stale periods): a crashed runner never
        removes its own, and its job's recovery treats a missing heartbeat
        as stale anyway."""
        await self.r.hset(self.k_runners, runner_id, _dump({**info, "at": _now()}))
        limit = max(86400.0, 10 * self.s.stale_seconds)
        gone = []
        for rid, raw in (await self.r.hgetall(self.k_runners)).items():
            age = _age((_load(raw) or {}).get("at"))
            if rid != runner_id and (age is None or age > limit):
                gone.append(rid)
        if gone:
            await self.r.hdel(self.k_runners, *gone)

    async def drop_runner(self, runner_id: str) -> None:
        await self.r.hdel(self.k_runners, runner_id)

    # Inline marker (plain hash commands: inline mode runs with whatever
    # client the API has) -----------------------------------------------

    async def inline_marker(self) -> Optional[Dict[str, Any]]:
        return _load(await self.r.hget(self.k_state, "inline"))

    async def set_inline(self, marker: Optional[Dict[str, Any]]) -> None:
        await self.r.hset(self.k_state, "inline", _dump(marker) if marker else "")

    async def queued_activity(self) -> Optional[str]:
        """Why inline execution must not run alongside queued execution."""
        active = _load(await self.r.hget(self.k_state, "active"))
        if active:
            return f"the queued job {active.get('job')} holds the global slot"
        fresh = await self.fresh_runners()
        if fresh:
            return f"the update runner {fresh[0].get('runner')} is running"
        return None

    # Registry writes from the API ----------------------------------------

    async def mutate_entry(self, ontology_id: str, change: Callable[[Dict[str, Any]], Any], *,
                           guard: Optional[str] = None, create: bool = False
                           ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Apply change(entry) to the newest registry entry in one WATCH
        transaction, retried when the entry (or, with a guard, the queue)
        changed before the write. A stale copy therefore never overwrites
        what the runner wrote meanwhile: update_txn, quarantine, version
        markers. change may return False to leave the entry unwritten, and
        may raise to refuse. With create, a missing entry starts empty.

        guard, checked inside the same transaction:
          "jobs"     refuse (409) while any queued, running or held job, or
                     an inline run, covers the ontology: source, disable
                     and enable changes. Cancel a queued job first.
          "running"  refuse while the holder of the global slot is running
                     on the ontology: resolving its transaction.

        Returns (entry, written); entry is None when it does not exist."""
        keys = [REGISTRY_KEY] + ([self.k_state, self.k_jobs, self.k_pending] if guard else [])

        async def body(pipe):
            if guard:
                blocker = await self._blocker(pipe, ontology_id, guard)
                if blocker:
                    raise QueueError(409, f"{ontology_id}: {blocker}")
            entry = _load(await pipe.hget(REGISTRY_KEY, ontology_id))
            if entry is None:
                if not create:
                    return (None, False), None
                entry = {}
            if change(entry) is False:
                return (entry, False), None
            return (entry, True), lambda p: p.hset(REGISTRY_KEY, ontology_id, json.dumps(entry))
        return await self._tx(keys, body)

    async def _blocker(self, pipe, ontology_id: str, guard: str) -> Optional[str]:
        """Why a guarded registry change of ontology_id must wait, read
        through pipe (under its WATCH), or None."""
        state = await pipe.hgetall(self.k_state)
        holders = []
        active = _load(state.get("active"))
        if active:
            job = _load(await pipe.hget(self.k_jobs, active.get("job", ""))) or {}
            holders.append({**job, "job": active.get("job"), "state": active.get("state")})
        inline = _load(state.get("inline"))
        if inline:
            holders.append(inline)
        for holder in holders:
            covered = ontology_id in touched(holder) or (
                guard == "jobs" and ontology_id in (holder.get("ontologies") or []))
            if covered and (guard == "jobs" or holder.get("state") == RUNNING):
                return f"job {holder.get('job')} is {holder.get('state')} on it; wait until it ends"
        if guard == "jobs":
            ids = await pipe.lrange(self.k_pending, 0, -1)
            for raw in (await pipe.hmget(self.k_jobs, ids) if ids else []):
                job = _load(raw)
                if job and job.get("state") == QUEUED and ontology_id in (job.get("ontologies") or []):
                    return (f"job {job['id']} is queued for it; cancel it with "
                            f"POST /admin/update_jobs/{job['id']}/cancel, or wait until it ends")
        return None

    # Operator recovery ----------------------------------------------------

    async def recover(self, job_id: str, claim: str, note: str, reconciled: bool, runner_stopped: bool,
                      local_inline_job: Optional[str] = None) -> Dict[str, Any]:
        """Release the global slot held by job_id after reconciliation.

        Refused unless job_id holds the slot with this claim, reconciled is
        confirmed and a note given. A job still marked running also needs
        runner_stopped and no heartbeat from its runner within
        UPDATE_RUNNER_STALE_SECONDS (for an inline marker: not run by this
        process); it then becomes recovery_required. The slot is released
        only when no ontology the job touched has an update_txn: those are
        resolved first, one by one, with resolve_update, which also refuses
        while a worker task is pending."""
        if not reconciled or not note.strip():
            raise QueueError(422, "confirm \"reconciled\": true and give a note of what was checked and done")

        async def body(pipe):
            state = await pipe.hgetall(self.k_state)
            active, inline = _load(state.get("active")), _load(state.get("inline"))
            if active and active.get("job") == job_id:
                holder, where = active, "active"
                record = _load(await pipe.hget(self.k_jobs, job_id)) or {"id": job_id, "events": []}
            elif inline and inline.get("job") == job_id:
                holder, where = inline, "inline"
                record = {**inline, "id": job_id, "events": list(inline.get("events") or [])}
            else:
                raise QueueError(409, f"{job_id} does not hold the global update slot; read GET /admin/update_jobs")
            if holder.get("claim") != claim:
                raise QueueError(409, f"the claim of {job_id} is {holder.get('claim')}, not {claim}")
            now = _now()
            lost = holder.get("state") == RUNNING
            if lost:
                if not runner_stopped:
                    raise QueueError(409, f"{job_id} is marked running. Stop its process "
                                          f"({holder.get('runner') or holder.get('api')}), then confirm "
                                          "\"runner_stopped\": true")
                if where == "active":
                    beat = _load(await pipe.hget(self.k_runners, holder.get("runner", "")))
                    age = _age((beat or {}).get("at"))
                    if age is not None and age < self.s.stale_seconds:
                        raise QueueError(409, f"runner {holder.get('runner')} sent a heartbeat {age:.0f}s ago; "
                                              f"stop it and retry after {self.s.stale_seconds:.0f}s")
                elif local_inline_job == job_id:
                    raise QueueError(409, f"this API process is still running {job_id}")
                holder["state"] = record["state"] = RECOVERY_REQUIRED
                record["events"].append({"at": now, "event": "marked_lost", "note": note})
            unresolved = []
            for oid in touched(record):
                txn = (_load(await pipe.hget(REGISTRY_KEY, oid)) or {}).get("update_txn")
                if txn:
                    unresolved.append({"ontology_id": oid, "txn_id": txn.get("id"), "state": txn.get("state"),
                                       "phase": txn.get("phase")})
            if unresolved:
                if not lost:
                    raise QueueError(409, {"message": "resolve these transactions first with "
                                                      "POST /admin/ontology/{id}/resolve_update",
                                           "unresolved": unresolved})

                def write_lost(p):
                    if where == "active":
                        p.hset(self.k_jobs, job_id, _dump(record))
                        p.hset(self.k_state, "active", _dump(holder))
                    else:
                        p.hset(self.k_state, "inline", _dump({**holder, "events": record["events"]}))
                return {"status": RECOVERY_REQUIRED, "job_id": job_id, "unresolved": unresolved,
                        "next": "resolve each transaction with POST /admin/ontology/{id}/resolve_update, "
                                "then call recover again"}, write_lost
            record["events"].append({"at": now, "event": "released", "note": note})
            record.update(state=RECOVERED, finished=now)
            if where == "active":
                return {"status": "released", "job_id": job_id}, await self._release_reads(pipe, record)
            evict = await self._evicted(pipe, job_id)

            def write_inline(p):
                # Kept with the queued jobs, so status shows what was released.
                p.hset(self.k_jobs, job_id, _dump(record))
                self._retire(p, job_id, evict)
                p.hset(self.k_state, "inline", "")
            return {"status": "released", "job_id": job_id}, write_inline

        keys = [self.k_state, self.k_jobs, self.k_runners, self.k_dedupe, self.k_finished, REGISTRY_KEY]
        return await self._tx(keys, body)


def _holds(active: Optional[Dict[str, Any]], job_id: str, claim: str) -> bool:
    return bool(active and active.get("job") == job_id and active.get("claim") == claim
                and active.get("state") == RUNNING)


class FencedRedis:
    """The registry client the update runner gives the pipeline for one
    claimed job. Reads pass through. A registry write applies only while the
    job still holds the global slot with this claim, in state running: it is
    WATCH <p>:state, check, MULTI/HSET/EXEC, so a release that lands first
    makes it fail with OwnershipLost, and one that lands after it finds the
    write done. check_claim() is the same test, used by updater._checkpoint
    before steps with effects outside the registry; between that check and
    the step there is one round trip in which a release is not seen.
    Commands the pipeline does not use are not offered."""

    _READS = ("hget", "hgetall", "hkeys", "hvals", "hexists", "hlen")

    def __init__(self, queue: "JobQueue", job_id: str, claim: str):
        self._queue = queue
        self.job_id = job_id
        self.claim = claim

    def __getattr__(self, name: str):
        if name in FencedRedis._READS:
            return getattr(self._queue.r, name)
        raise AttributeError(f"{name} is not offered to a fenced pipeline client")

    async def check_claim(self) -> None:
        if not await self._queue.holds(self.job_id, self.claim):
            raise OwnershipLost(f"job {self.job_id}: claim {self.claim} no longer holds the global slot")

    async def hset(self, name: str, key: str, value: str) -> int:
        if name != REGISTRY_KEY:
            raise ValueError(f"a fenced pipeline client writes only {REGISTRY_KEY}, not {name}")
        q = self._queue

        async def body(pipe):
            if not _holds(_load(await pipe.hget(q.k_state, "active")), self.job_id, self.claim):
                raise OwnershipLost(f"job {self.job_id}: claim {self.claim} no longer holds the global slot; "
                                    f"the write of {key} was refused")
            return 1, lambda p: p.hset(name, key, value)
        return await q._tx([q.k_state], body)


def brief(job: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A job without its per-ontology lists, for status listings."""
    if not job:
        return None
    view = {k: job.get(k) for k in ("id", "kind", "state", "source", "force", "requested", "created", "claimed",
                                    "finished", "runner", "claim", "current", "held_by", "stop_reason",
                                    "cancel_requested") if job.get(k) is not None}
    ontologies = job.get("ontologies") or []
    view["ontology_count"] = len(ontologies)
    if len(ontologies) <= 5:
        view["ontologies"] = ontologies
    summary = job.get("summary")
    if summary:
        view["counts"] = {k: len(v) for k, v in summary.items()}
    if job.get("result"):
        view["result_status"] = job["result"].get("status")
    return view


# ---------------------------------------------------------------------------
# Inline mode
# ---------------------------------------------------------------------------

class InlineGate:
    """Inline mode's global limit: one heavy job at a time in this process,
    whatever triggered it. A reservation is taken synchronously, so a
    request that is answered "triggered" has its slot before the answer.
    While the job runs, the inline marker in Redis keeps a runner from
    claiming; a held outcome leaves it in place until recover."""

    def __init__(self, api_id: str):
        self.api_id = api_id
        self.job: Optional[str] = None

    def reserve(self) -> str:
        if self.job:
            raise QueueError(409, f"one update, reindex or batch check runs at a time; {self.job} is running. "
                                  "Try again when it ends")
        self.job = new_job_id("inline")
        return self.job

    async def reserve_waiting(self, poll: float = 5.0) -> str:
        while self.job:
            await asyncio.sleep(poll)
        return self.reserve()

    def release(self, job_id: str) -> None:
        if self.job == job_id:
            self.job = None

    async def begin(self, queue: JobQueue, job_id: str, kind: str, ontologies: List[str]) -> Dict[str, Any]:
        """Write the inline marker for the reservation job_id, once no
        earlier inline run left one and no queued execution is active. On
        refusal the reservation ends, and the marker is not left behind."""
        written = False
        try:
            previous = await queue.inline_marker()
            if previous:
                raise QueueError(409, f"the inline execution {previous.get('job')} ended without a known outcome "
                                      f"({previous.get('state')}); reconcile it and POST "
                                      f"/admin/update_jobs/{previous.get('job')}/recover")
            marker = {"job": job_id, "claim": secrets.token_hex(8), "api": self.api_id, "kind": kind,
                      "ontologies": list(ontologies), "since": _now(), "state": RUNNING}
            # The marker is written before the queued side is read, and a
            # runner claims only when it reads no marker: one of them refuses.
            await queue.set_inline(marker)
            written = True
            conflict = await queue.queued_activity()
            if conflict:
                raise QueueError(409, f"inline update execution is prohibited while {conflict}; this "
                                      "deployment must use UPDATE_EXECUTION=queued in every API process")
            return marker
        except BaseException:
            self.release(job_id)
            if written:
                try:
                    await queue.set_inline(None)  # nothing ran
                except Exception:
                    logger.exception("Could not remove the inline marker of %s", job_id)
            raise

    async def run(self, queue: JobQueue, marker: Dict[str, Any],
                  fn: Callable[[Dict[str, Any]], Awaitable[Tuple[Any, Optional[str]]]]) -> Any:
        """Run fn(marker) as the job begin() started. fn returns (result,
        held_by): held_by names the ontology whose outcome is unknown, or is
        None. The reservation ends in every case; the marker is removed only
        after a known outcome, and stays (running) if fn raises."""
        try:
            result, held_by = await fn(marker)
            if held_by:
                marker.update(state=RECOVERY_REQUIRED, held_by=held_by, current=None, ended=_now())
                await queue.set_inline(marker)
            else:
                await queue.set_inline(None)
            return result
        finally:
            self.release(marker["job"])

    @staticmethod
    async def note_current(queue: JobQueue, marker: Dict[str, Any], ontology_id: Optional[str]) -> None:
        marker["current"] = ontology_id
        await queue.set_inline(marker)
