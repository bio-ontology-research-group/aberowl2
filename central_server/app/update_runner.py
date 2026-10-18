"""
Update runner: claims queued update, reindex and batch-check jobs from Redis
and runs them through the update pipeline (app/intake/updater.py), one at a
time across every runner and API process sharing the queue
(app/intake/update_jobs.py). It needs UPDATE_EXECUTION=queued:

    python -m app.update_runner

It serves no port and schedules nothing: it runs only jobs that an operator,
a webhook or the opt-in scheduled check put on the queue. A batch runs its
ontologies one after another. A job whose outcome is unknown keeps the
global slot, and the runner waits until an operator recovers it.

Ownership. The pipeline gets a fenced registry client (FencedRedis): its
registry writes, and the checks before each step with effects elsewhere,
fail with OwnershipLost once the job no longer holds the slot with this
runner's claim. The heartbeat checks the claim as well and cancels the job
when it is gone, or when Redis has been unreachable (refusing, or not
answering within one heartbeat interval: the client's socket timeouts) for
half of UPDATE_RUNNER_STALE_SECONDS, before any operator may take the runner
for stopped. A heartbeat that fails in any other way stops the runner too.
Either way the runner writes nothing more about the job and exits with
status 3. A step already under way (a request sent, a worker task started)
is not undone; see update_jobs.py, "What this cannot enforce". Service Redis
only while the runner is idle: a longer outage during a job cancels it.

SIGTERM or SIGINT while idle exits with 0. During a job they cancel the run;
the pipeline records its transaction for recovery and the job becomes
recovery_required, keeping the slot. Exit status 2: invalid configuration.
"""

import asyncio
import logging
import os
import signal
import socket
import sys
import uuid
from typing import Any, Dict, Optional, Tuple

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.es_manager import CentralESManager
from app.intake import update_jobs

logger = logging.getLogger("app.update_runner")

# Attempts to record a job's end while Redis is unreachable; the delay
# between them grows to 30 s. If all fail, the job stays running in Redis
# and keeps the global slot.
FINISH_ATTEMPTS = 20
# Waiting behind a held slot or an unresolved transaction needs no fast poll.
BLOCKED_POLL_SECONDS = 30
EXIT_OK, EXIT_CONFIG, EXIT_LOST = 0, 2, 3


class Runner:

    def __init__(self, redis_client, settings: update_jobs.Settings, es_mgr, ontologies_dir: str, es_url: str,
                 runner_id: Optional[str] = None):
        self.redis = redis_client
        self.settings = settings
        self.queue = update_jobs.JobQueue(redis_client, settings)
        self.es_mgr = es_mgr
        self.ontologies_dir = ontologies_dir
        self.es_url = es_url
        self.runner_id = runner_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.job_id: Optional[str] = None
        self.claim: Optional[str] = None
        # Set when this runner must write nothing more and exit (status 3):
        # its claim is gone, or its outcome could not be recorded.
        self.lost = False
        self.heartbeat_interval = settings.heartbeat_seconds
        # Redis unreachable this long during a job: the runner cancels the
        # job itself, well before an operator may take it for stopped.
        self.redis_loss_limit = settings.stale_seconds / 2
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._last_reason: Optional[str] = None

    # Control --------------------------------------------------------------

    def stop(self) -> None:
        """Stop claiming; cancel the job in progress, if any."""
        if not self._stop.is_set():
            logger.warning("Runner %s stopping%s", self.runner_id,
                           f"; cancelling job {self.job_id}" if self.job_id else "")
        self._stop.set()
        self._cancel_job()

    def _cancel_job(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _fence_out(self, reason: str) -> None:
        """Stop the job without writing anything more about it."""
        if not self.lost:
            logger.critical("%s: %s; stopping and writing nothing more",
                            f"Job {self.job_id}" if self.job_id else f"Runner {self.runner_id}", reason)
        self.lost = True
        self._cancel_job()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    @property
    def working(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _note(self, reason: Optional[str]) -> None:
        if reason != self._last_reason:
            if reason:
                logger.warning("Not claiming: %s", reason)
            self._last_reason = reason

    # Loop -----------------------------------------------------------------

    async def run_forever(self) -> int:
        """Returns the process exit status."""
        logger.info("Update runner %s started (queue %s)", self.runner_id, self.settings.prefix)
        beat = asyncio.ensure_future(self._heartbeat_loop())
        try:
            while not self.stopping and not self.lost:
                try:
                    claimed, blocked = await self.run_once()
                except (RedisError, OSError, update_jobs.QueueError) as e:
                    logger.error("Cannot read the queue: %s", e)
                    await self._sleep(BLOCKED_POLL_SECONDS)
                    continue
                if self.lost:
                    break
                if not claimed:
                    await self._sleep(BLOCKED_POLL_SECONDS if blocked else self.settings.poll_seconds)
            return EXIT_LOST if self.lost else EXIT_OK
        finally:
            beat.cancel()
            try:
                await beat
            except BaseException:
                pass
            try:
                await self.queue.drop_runner(self.runner_id)
            except (RedisError, OSError):
                pass
            logger.info("Update runner %s stopped", self.runner_id)

    async def run_once(self) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Claim and run at most one job. Returns (the job as it ended, or
        None; whether claiming is blocked)."""
        if await self.queue.pending_count() == 0:
            self._note(None)
            return None, False
        # A transaction anywhere may belong to a task still running on a
        # worker or in Elasticsearch: nothing heavy starts beside it.
        blocker = await self.queue.unresolved_transaction()
        if blocker:
            self._note(f"{blocker}; reconcile it and POST /admin/ontology/<id>/resolve_update")
            return None, True
        job, reason = await self.queue.claim(self.runner_id)
        if job is None:
            self._note(reason)
            return None, bool(reason)
        self._note(None)
        return await self._run(job), False

    def _beat_info(self) -> Dict[str, Any]:
        return {"runner": self.runner_id, "job": self.job_id, "pid": os.getpid(), "host": socket.gethostname(),
                "settings": self.settings.shared()}

    async def heartbeat_once(self, last_ok: float) -> float:
        """One heartbeat. While a job runs, also check its claim: gone means
        fence out. Returns the time of the last successful contact; Redis
        unreachable for redis_loss_limit during a job fences out too."""
        loop = asyncio.get_running_loop()
        try:
            await self.queue.heartbeat(self.runner_id, self._beat_info())
            if self.working and self.claim and not await self.queue.holds(self.job_id, self.claim):
                self._fence_out("its claim no longer holds the global slot")
            return loop.time()
        except (RedisError, OSError) as e:
            logger.warning("Heartbeat not written: %s", e)
            if self.working and loop.time() - last_ok > self.redis_loss_limit:
                self._fence_out(f"Redis unreachable for more than {self.redis_loss_limit:g}s")
            return last_ok

    async def _heartbeat_loop(self) -> None:
        last_ok = asyncio.get_running_loop().time()
        while True:
            try:
                last_ok = await self.heartbeat_once(last_ok)
            except asyncio.CancelledError:
                raise  # normal shutdown
            except Exception:
                # Without a heartbeat this runner would look stale and lose
                # its own fence: stop rather than run on unwatched.
                logger.exception("The heartbeat failed unexpectedly")
                self._fence_out("the heartbeat failed")
                self._stop.set()
                return
            await asyncio.sleep(self.heartbeat_interval)

    # One job --------------------------------------------------------------

    async def _run(self, job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        job_id, claim = job["id"], job["claim"]
        self.job_id, self.claim = job_id, claim
        logger.info("Claimed %s (%s, %d ontologies, source %s) as %s", job_id, job["kind"],
                    len(job.get("ontologies") or []), job.get("source"), claim)
        try:
            try:
                await self.queue.heartbeat(self.runner_id, self._beat_info())
            except (RedisError, OSError):
                pass
            if self.stopping:
                # Stopped between the claim and the start: nothing ran.
                return await self._finish(job_id, claim, False, update_jobs.CANCELLED,
                                          {"stop_reason": "runner_stopped"})
            self._task = asyncio.ensure_future(self._execute(job))
            state = update_jobs.DONE
            try:
                hold, state, fields = await self._task
            except update_jobs.OwnershipLost as e:
                self._fence_out(str(e))
                return None
            except asyncio.CancelledError:
                if not self._task.cancelled():
                    raise  # this coroutine itself was cancelled
                if self.lost:
                    return None  # fenced out by the heartbeat: write nothing
                hold, fields = True, {"error": "cancelled: the runner stopped during the job"}
            except Exception as e:
                logger.exception("Job %s raised", job_id)
                hold, fields = True, {"error": f"runner error: {type(e).__name__}: {e}"}
            self._task = None
            return await self._finish(job_id, claim, hold, state, fields)
        finally:
            self._task = None
            self.job_id = self.claim = None

    async def _finish(self, job_id: str, claim: str, hold: bool, state: str,
                      fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for attempt in range(FINISH_ATTEMPTS):
            try:
                ended = await self.queue.finish(job_id, claim, hold, state, **fields)
                log = logger.error if hold else logger.info
                log("Job %s ended %s", job_id, ended["state"])
                return ended
            except update_jobs.OwnershipLost as e:
                self._fence_out(f"{e}; the outcome was not recorded on the job")
                return None
            except (RedisError, OSError, update_jobs.QueueError) as e:
                logger.error("Recording the end of %s failed (%s); retrying", job_id, e)
                await asyncio.sleep(min(30, 2 ** attempt))
        self._fence_out("its end could not be recorded; it stays running and keeps the global slot "
                        "until an operator recovers it")
        return None

    async def _execute(self, job: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """Run the job. Returns (hold the slot, end state, job fields)."""
        job_id, claim, kind = job["id"], job["claim"], job["kind"]
        fenced = update_jobs.FencedRedis(self.queue, job_id, claim)
        ctx = (fenced, self.es_mgr, self.ontologies_dir, self.es_url)
        if kind in ("update", "reindex"):
            oid = job["ontologies"][0]
            await self.queue.progress(job_id, claim, current=oid)
            result = await update_jobs.run_one(kind, oid, *ctx, force=bool(job.get("force")))
            fields: Dict[str, Any] = {"result": update_jobs.sanitize(result)}
            if update_jobs.holds_ownership(result):
                fields["held_by"] = oid
                return True, update_jobs.RECOVERY_REQUIRED, fields
            fields["current"] = None
            return False, update_jobs.DONE, fields

        summary = job.get("summary") or update_jobs.new_summary()

        async def before(oid: str) -> bool:
            current = await self.queue.progress(job_id, claim, current=oid)
            return not (current.get("cancel_requested") or self.stopping)

        async def after(oid: str) -> None:
            await self.queue.progress(job_id, claim, summary=summary, current=None)

        outcome = await update_jobs.run_check(job["ontologies"], summary, *ctx, before=before, after=after)
        fields = {"summary": summary, "current": None}
        if outcome["stop"] == "held":
            fields.update(held_by=outcome["held_by"], stop_reason="outcome_unknown")
            return True, update_jobs.RECOVERY_REQUIRED, fields
        if outcome["stop"] == "cancelled":
            fields["stop_reason"] = "runner_stopped" if self.stopping else "cancelled"
            return False, update_jobs.CANCELLED, fields
        return False, update_jobs.DONE, fields


def redis_client(settings: update_jobs.Settings):
    """The runner's Redis client. Connecting and every command time out
    after one heartbeat interval, so a Redis that hangs rather than refuses
    counts as unreachable for the heartbeat's fence-out. A checkpoint or
    fenced write that times out raises, and the step after it never starts."""
    timeout = settings.heartbeat_seconds
    return redis.from_url(settings.redis_url, decode_responses=True,
                          socket_connect_timeout=timeout, socket_timeout=timeout)


async def _amain(settings: update_jobs.Settings) -> int:
    client = redis_client(settings)
    runner = Runner(client, settings, CentralESManager(), os.getenv("ONTOLOGIES_DIR", "/data/ontologies"),
                    os.getenv("CENTRAL_ES_URL", "http://elasticsearch:9200"))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, runner.stop)
    try:
        return await runner.run_forever()
    finally:
        await client.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    settings = update_jobs.load_settings()
    if settings.mode != "queued":
        logger.error("The update runner needs UPDATE_EXECUTION=queued (%s)", settings.error or settings.mode)
        sys.exit(EXIT_CONFIG)
    sys.exit(asyncio.run(_amain(settings)))


if __name__ == "__main__":
    main()
