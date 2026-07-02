"""Agent harness: bridges the AberOWL MCP server to OpenRouter's function-calling.

For each (model, condition, regime, item): expose the condition's tools via the
API (unhinted), run an agent loop, execute any tool calls over MCP, and record
the final answer + which tools were actually invoked.

Run:
    OPENROUTER_API_KEY=... python experiments/iri_hallucination/harness.py \
        --gold experiments/iri_hallucination/gold.jsonl \
        --out  experiments/iri_hallucination/runs.jsonl
"""
import argparse, asyncio, json, sys, time

import httpx
from mcp import ClientSession
try:
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client
except ImportError:  # older mcp
    from mcp.client.streamable_http import streamablehttp_client

import config as C
import prompts as P


def _mcp_to_openai(tool):
    return {"type": "function", "function": {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.inputSchema or {"type": "object", "properties": {}},
    }}


async def call_openrouter(client, model, messages, tools):
    body = {"model": model, "messages": messages, "temperature": C.TEMPERATURE}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    last = "?"
    for attempt in range(6):
        try:
            r = await client.post(C.OPENROUTER_URL, json=body,
                                  headers={"Authorization": f"Bearer {C.OPENROUTER_API_KEY}"},
                                  timeout=C.REQUEST_TIMEOUT)
        except Exception as e:                      # ReadError/ConnectError/timeouts
            last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(2 * (attempt + 1)); continue
        if r.status_code == 200:
            try:
                return r.json()["choices"][0]["message"]
            except Exception as e:
                last = f"parse: {e}: {r.text[:200]}"; break
        last = f"HTTP {r.status_code}: {r.text[:300]}"
        if r.status_code in (429, 500, 502, 503) and attempt < 5:
            await asyncio.sleep(2 * (attempt + 1)); continue
        break
    return {"role": "assistant", "content": "", "_error": last}


async def exec_tool(session, name, args):
    try:
        res = await session.call_tool(name, args)
        return "".join(c.text for c in res.content if getattr(c, "text", None))[:4000]
    except Exception as e:
        return f"(tool error: {e})"


async def _agent(client, session, tools, model, condition, regime, item):
    messages = [
        {"role": "system", "content": P.system_prompt(condition, regime)},
        {"role": "user", "content": P.user_prompt(item["term"], item.get("ontology"))},
    ]
    invoked = []          # which tools the model actually chose
    for _ in range(C.MAX_TOOL_TURNS):
        msg = await call_openrouter(client, model, messages, tools)
        if msg.get("_error"):
            return _result(item, model, condition, regime, "", invoked, error=msg["_error"])
        messages.append({k: msg[k] for k in ("role", "content", "tool_calls") if k in msg and msg[k] is not None})
        tcs = msg.get("tool_calls")
        if not tcs:
            return _result(item, model, condition, regime, msg.get("content") or "", invoked)
        for tc in tcs:
            fn = tc["function"]["name"]
            try: args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception: args = {}
            out = await exec_tool(session, fn, args)
            invoked.append({"tool": fn, "args": args, "result": out[:600]})
            messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": out})
    return _result(item, model, condition, regime, msg.get("content") or "", invoked, truncated=True)


async def run_item(client, model, condition, regime, item):
    """Run one item. Tool conditions open a FRESH MCP session (robust to idle drops)."""
    tool_names = P.CONDITION_TOOLS[condition]
    if not tool_names:
        return await _agent(client, None, None, model, condition, regime, item)
    async with streamablehttp_client(C.MCP_URL) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            by_name = {t.name: _mcp_to_openai(t) for t in (await session.list_tools()).tools}
            tools = [by_name[n] for n in tool_names if n in by_name] or None
            return await _agent(client, session, tools, model, condition, regime, item)


def _result(item, model, condition, regime, text, invoked, error=None, truncated=False):
    return {"term": item["term"], "ontology": item.get("ontology"), "gold_iri": item.get("gold_iri"),
            "difficulty": item.get("difficulty"), "model": model, "condition": condition,
            "regime": regime, "answer": text.strip(), "tools_invoked": [t["tool"] for t in invoked],
            "tool_calls": invoked, "error": error, "truncated": truncated}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", nargs="*", default=C.MODELS)
    ap.add_argument("--conditions", nargs="*", default=C.CONDITIONS)
    ap.add_argument("--regimes", nargs="*", default=C.REGIMES)
    a = ap.parse_args()
    if not C.OPENROUTER_API_KEY:
        sys.exit("set OPENROUTER_API_KEY")
    gold = [json.loads(l) for l in open(a.gold) if l.strip()]

    jobs = [(model, regime, condition, item)
            for model in a.models for regime in a.regimes
            for condition in a.conditions for item in gold]
    sem = asyncio.Semaphore(C.CONCURRENCY)
    fout = open(a.out, "w"); lock = asyncio.Lock(); done = 0

    async with httpx.AsyncClient() as client:
        async def worker(model, regime, condition, item):
            nonlocal done
            async with sem:
                try:
                    res = await run_item(client, model, condition, regime, item)
                except Exception as e:
                    res = _result(item, model, condition, regime, "", [], error=f"{type(e).__name__}: {e}")
            async with lock:
                done += 1
                fout.write(json.dumps(res) + "\n"); fout.flush()
                if done % 20 == 0 or done == len(jobs):
                    print(f"  [{done}/{len(jobs)}] {model.split('/')[-1]} {regime}/{condition} -> {res['answer'][:40]!r}")
            return res
        await asyncio.gather(*(worker(*j) for j in jobs))
    fout.close()
    print(f"wrote {done} results to {a.out}")


if __name__ == "__main__":
    asyncio.run(main())
