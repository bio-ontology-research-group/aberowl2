"""The experiment harness must not stall forever on a queued OpenRouter call.

OpenRouter can accept a request, hold it behind an account-wide concurrency
limit, and keep the connection alive while it waits. httpx's read timeout never
fires in that state, so a run deadlocks silently and produces no error row --
this is what killed two multi-hour DL runs at ~19 of 480 items.

`REQUEST_DEADLINE` puts a ceiling outside httpx. These tests pin both halves of
the contract: it rescues a hung call when set, and changes nothing when unset
(the IRI experiment's config does not define it, and its published results must
stay reproducible).
"""
import asyncio
import sys
import types
from pathlib import Path

import pytest

HARNESS_DIR = Path(__file__).resolve().parents[1] / "experiments" / "iri_hallucination"


@pytest.fixture
def harness(monkeypatch):
    """Import harness.py with a stub config, without touching the real one."""
    monkeypatch.syspath_prepend(str(HARNESS_DIR))
    for mod in ("harness", "config", "prompts"):
        sys.modules.pop(mod, None)

    cfg = types.ModuleType("config")
    cfg.OPENROUTER_URL = "https://example.invalid/v1/chat/completions"
    cfg.OPENROUTER_API_KEY = "test-key"
    cfg.TEMPERATURE = 0.0
    cfg.REQUEST_TIMEOUT = 120
    cfg.CONCURRENCY = 1
    cfg.MODELS = ["m"]
    cfg.CONDITIONS = ["none"]
    cfg.REGIMES = ["forced"]
    sys.modules["config"] = cfg

    import harness as h
    yield h, cfg
    for mod in ("harness", "config"):
        sys.modules.pop(mod, None)


class HangingClient:
    """A client whose post() never returns -- the queued-request failure mode."""

    def __init__(self):
        self.calls = 0

    async def post(self, *a, **kw):
        self.calls += 1
        await asyncio.Event().wait()          # blocks until cancelled


class OkClient:
    def __init__(self):
        self.calls = 0

    async def post(self, *a, **kw):
        self.calls += 1
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"role": "assistant",
                                                   "content": "hi"}}],
                          "usage": {}, "provider": "acme", "id": "gen-1"},
            text="",
        )


def test_deadline_turns_a_hang_into_an_error(harness, monkeypatch):
    h, cfg = harness
    cfg.REQUEST_DEADLINE = 0.05               # short, so the test is fast
    real_sleep = asyncio.sleep                # skip the retry backoff
    monkeypatch.setattr(h.asyncio, "sleep", lambda *_a, **_k: real_sleep(0))

    client = HangingClient()
    msg = asyncio.run(h.call_openrouter(client, "m", [], None))

    assert msg["_error"].startswith("deadline:"), msg["_error"]
    assert client.calls == 6, "should exhaust the existing 6 retries, not hang"


def test_no_deadline_leaves_the_call_unwrapped(harness):
    """Unset -> the original code path. The IRI experiment must be unaffected."""
    h, cfg = harness
    assert not hasattr(cfg, "REQUEST_DEADLINE")

    client = OkClient()
    msg = asyncio.run(h.call_openrouter(client, "m", [], None))

    assert msg["content"] == "hi"
    assert msg["_provider"] == "acme"
    assert client.calls == 1


def test_iri_config_defines_no_deadline():
    """Guard: adding REQUEST_DEADLINE to the IRI config would change its results."""
    text = (HARNESS_DIR / "config.py").read_text()
    assert "REQUEST_DEADLINE" not in text


# --- --resume -------------------------------------------------------------
# A stalled cell lands as an error row. The second pass must re-run exactly
# those, keep the good rows, and not duplicate anything -- including for the
# terms the gold set repeats on purpose.

def _rows(path):
    return [__import__("json").loads(l) for l in open(path) if l.strip()]


def test_resume_reruns_only_failed_and_missing(harness, tmp_path, monkeypatch):
    import json
    h, cfg = harness

    gold = tmp_path / "gold.jsonl"
    gold.write_text("".join(json.dumps(r) + "\n" for r in [
        {"term": "a", "ontology": "GO"},
        {"term": "b", "ontology": "GO"},
        {"term": "a", "ontology": "GO"},        # deliberate duplicate
    ]))

    out = tmp_path / "runs.jsonl"
    out.write_text("".join(json.dumps(r) + "\n" for r in [
        {"term": "a", "ontology": "GO", "model": "m", "regime": "forced",
         "condition": "none", "answer": "kept-1", "error": None},
        {"term": "b", "ontology": "GO", "model": "m", "regime": "forced",
         "condition": "none", "answer": "", "error": "deadline: no response"},
        # only ONE of the two 'a' rows is present -> the duplicate is still owed
    ]))

    ran = []

    async def fake_run_item(client, model, condition, regime, item):
        ran.append(item["term"])
        return {"term": item["term"], "ontology": item.get("ontology"), "model": model,
                "condition": condition, "regime": regime, "answer": "fresh", "error": None}

    monkeypatch.setattr(h, "run_item", fake_run_item)
    monkeypatch.setattr(h.sys, "argv", ["harness.py", "--gold", str(gold), "--out", str(out),
                                        "--models", "m", "--conditions", "none",
                                        "--regimes", "forced", "--resume"])

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(h.httpx, "AsyncClient", lambda *a, **k: _C())

    __import__("asyncio").run(h.main())

    assert sorted(ran) == ["a", "b"], f"re-ran {ran}"     # failed 'b' + owed dup 'a'
    rows = _rows(out)
    assert len(rows) == 3, rows                            # no duplication, none lost
    assert sum(1 for r in rows if r["answer"] == "kept-1") == 1
    assert not [r for r in rows if r.get("error")]


def test_no_resume_flag_overwrites_as_before(harness, tmp_path, monkeypatch):
    """Default path must still truncate -- the original behaviour."""
    import json
    h, cfg = harness
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"term": "a", "ontology": "GO"}) + "\n")
    out = tmp_path / "runs.jsonl"
    out.write_text(json.dumps({"term": "stale", "model": "m", "regime": "forced",
                               "condition": "none", "error": None}) + "\n")

    async def fake_run_item(client, model, condition, regime, item):
        return {"term": item["term"], "model": model, "condition": condition,
                "regime": regime, "answer": "fresh", "error": None}

    monkeypatch.setattr(h, "run_item", fake_run_item)
    monkeypatch.setattr(h.sys, "argv", ["harness.py", "--gold", str(gold), "--out", str(out),
                                        "--models", "m", "--conditions", "none",
                                        "--regimes", "forced"])

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(h.httpx, "AsyncClient", lambda *a, **k: _C())

    __import__("asyncio").run(h.main())
    rows = _rows(out)
    assert len(rows) == 1 and rows[0]["term"] == "a", rows
