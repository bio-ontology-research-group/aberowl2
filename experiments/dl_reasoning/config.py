"""Config for the DL-reasoning experiment (Q2: can an agent exploit reasoning?).

Companion to ../iri_hallucination, which measured GROUNDING (find_iri, a single
IRI per item). This one measures REASONING: the model must return the SET of
classes satisfying a class expression, and the arms separate what memory,
grounding and reasoning each contribute.

The dependent variable is end-to-end set F1, decomposed into four stages
(adoption / formulation / service / relay) in score_dl.py.
"""
import os

# --- OpenRouter (OpenAI-compatible) ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/auth/key"   # key cap; /credits is account-wide

# --- AberOWL under test ---
MCP_URL = os.getenv("ABEROWL_MCP_URL", "http://aber-owl.net/mcp/ontology/mcp")
ABEROWL_API = os.getenv("ABEROWL_API", "http://aber-owl.net/api")

# --- Subjects ---
# Same five as the IRI experiment, so the two tables share subjects. Listed in
# ASCENDING cost: run one model at a time, record credits after each, and stop
# before gemini if the measured burn is worse than projected.
#   gpt-oss-20b      0.03/0.14  $/M    ~$0.1 in the IRI run
#   llama-4-scout    0.10/0.30         ~$0.07
#   qwen3.6-35b-a3b  0.14/1.00         ~$1
#   deepseek-v3.2    0.23/0.34         ~$0.30
#   gemini-3.5-flash 1.50/9.00         ~$24  <-- 91% of the IRI budget (thinking on by default)
# Ordered by OUTPUT price, which dominates: this task's cost is driven by
# completion tokens, so deepseek (0.34/M out) is cheaper than qwen (1.00/M out)
# despite a higher input price. Measured on the first two models.
MODELS = [
    "openai/gpt-oss-20b",        # measured $0.576 (480 runs, 6,798 out tok/run)
    "meta-llama/llama-4-scout",  # measured $0.204 (480 runs,   184 out tok/run)
    "deepseek/deepseek-v3.2",    # est ~$1.4-1.9
    "qwen/qwen3.6-35b-a3b",      # est ~$3.8-5.3
    "google/gemini-3.5-flash",   # est ~$35-130  <-- 85-94% of the whole budget
]
# gpt-5.5 stays dropped (~$40+ on the IRI design; worse here).

# --- Conditions (which tools the API exposes; never hinted in the prompt,
#     EXCEPT dlquery_hint, which is the syntax ablation) ---
#   none         : no tools                       -> parametric recall
#   lookup       : find_iri + search_classes      -> grounding WITHOUT reasoning (the isolator)
#   dlquery      : reasoning + lookup             -> full capability, production tool descriptions
#   dlquery_hint : as dlquery + Manchester syntax example in the system prompt
#                  -> ablation: is formulation failure a documentation artifact?
CONDITIONS = ["none", "lookup", "dlquery", "dlquery_hint"]

# Set retrieval has no meaningful abstention analogue, so a single regime.
REGIMES = ["forced"]

# Set retrieval needs more turns than the IRI task, where 6 already truncated
# 39/346 of gemini's runs.
MAX_TOOL_TURNS = 12
TOOL_RESULT_CHARS = 6000   # what the model sees per tool result
TOOL_LOG_CHARS = 6000      # what we RECORD - must match, or relay fidelity is unmeasurable

# --- Provider pinning (reproducibility, not just cost) ---
# OpenRouter load-balances across endpoints that differ in price AND QUANTIZATION:
# deepseek-v3.2 alone has 14 endpoints spanning $0.31-$4.50/M output, and the tags
# show gmicloud/fp8 next to deepinfra/fp4. Unpinned, successive calls to "the same
# model" can be numerically different systems, which is a reproducibility hazard a
# reviewer can check. Pin it, and log the provider actually used on every call.
#
# The pin is PER MODEL, not global: a single global pin is wrong as soon as the
# subject list spans vendors. GMICloud serves deepseek-v3.2 but serves NEITHER
# qwen3.6-35b-a3b NOR gemini-3.5-flash, so the old global `gmicloud/fp8` would have
# hard-failed both (allow_fallbacks is False by design, so it fails loudly rather
# than silently swapping the served system).
#
# Each choice below was made from /api/v1/models/{id}/endpoints on 2026-08-30,
# on price AND 30-day uptime, preferring a NAMED quantization over "unknown"
# (an unnamed quant is exactly the substitution the pin exists to prevent) and
# requiring tool-call support (every arm but `none` needs function calling).
PROVIDERS = {
    # 14 endpoints. Cheapest overall AND fp8: $0.209/$0.310 per M, 99.10% uptime,
    # tools supported, 147k max completion. Runner-up streamlake/fp8 ($0.215/$0.322,
    # 99.95%) is 4% dearer for +0.85pt uptime; the harness already retries 6x with
    # backoff on 429/5xx, so price wins and the deepseek pin is unchanged from the
    # global one it replaces. Explicitly NOT deepinfra/fp4 (same price as several
    # fp8 endpoints, but a coarser quantization = a different system).
    "deepseek/deepseek-v3.2": {"order": ["gmicloud/fp8"], "allow_fallbacks": False},

    # 10 endpoints. Cheapest fp8 endpoint: $0.10/$0.90 per M at 100% uptime, tools
    # supported, 236k max completion. darkbloom/fp4 is nominally cheaper
    # ($0.05/$0.70) and is rejected on quantization, not price. deepinfra/fp8 matches
    # the input price but is dearer on output, has the worst uptime of the fp8 set
    # (97.95%), and caps completions at 16k.
    "qwen/qwen3.6-35b-a3b": {"order": ["akashml/fp8"], "allow_fallbacks": False},

    # 7 endpoints, ALL first-party Google and all quantization "unknown" - a closed
    # model has no quantization choice, so the pin here buys routing stability only.
    # Google Vertex global at 99.57% uptime vs Google AI Studio at 97.16%; the
    # /flex tiers halve the price ($0.75/$4.50) but trade scheduling priority, and
    # this is the one subject where extended thinking makes runs long enough that a
    # deprioritised request can hit REQUEST_TIMEOUT and land in the table as an
    # error row. For an $19 subject, buying the standard tier's latency guarantee
    # is worth more than the $9 saved.
    "google/gemini-3.5-flash": {"order": ["google-vertex/global"], "allow_fallbacks": False},

    # The two already-completed subjects, which ran UNPINNED. Their `providers` and
    # `gen_ids` keys are ABSENT from all 480 rows of each file - not empty, absent -
    # and both keys were introduced by the same commit that introduced the pin
    # (d1b6c22, "pin OpenRouter provider and log call provenance"). Absent keys
    # therefore date those runs to the pre-pin harness: they were load-balanced, and
    # the endpoint that served them is unrecorded and unrecoverable. (This is a
    # provenance gap to disclose in the methods, NOT a case of OpenRouter ignoring a
    # pin.) The token totals do identify it
    # circumstantially: llama's 1,514,656 in / 153,674 out cost $0.2036, which
    # matches deepinfra/fp8 at $0.10/$0.30 ($0.198) and not groq at $0.11/$0.34
    # ($0.219). The pins below are what a RE-RUN would use; they are not a claim
    # about what produced runs_gpt-oss-20b.jsonl / runs_llama-4-scout.jsonl.
    #   gpt-oss-20b : cheapest tool-capable fp8 endpoint ($0.02/$0.10) but only
    #                 96.16% uptime; deepinfra/bf16 ($0.03/$0.14, 99.73%) is the
    #                 price the config header already quotes and the likely original.
    "openai/gpt-oss-20b": {"order": ["deepinfra/bf16"], "allow_fallbacks": False},
    #   llama-4-scout : deepinfra/fp8 ($0.10/$0.30, 100% uptime) - the price the
    #                 config header quotes and the one the measured spend matches.
    "meta-llama/llama-4-scout": {"order": ["deepinfra/fp8"], "allow_fallbacks": False},
}

# Per-M USD prices of the PINNED endpoint, for computing spend from the logged
# token counts. Kept next to the pin so the two cannot drift apart.
PROVIDER_PRICES = {
    "deepseek/deepseek-v3.2":   {"in": 0.2088, "out": 0.3096},
    "qwen/qwen3.6-35b-a3b":     {"in": 0.10,   "out": 0.90},
    "google/gemini-3.5-flash":  {"in": 1.50,   "out": 9.00},
}

# --- Pinning is OFF (2026-08-31) ---------------------------------------------
# Measured, deepseek-v3.2 at CONCURRENCY=2, 90s deadline, 30-request stream:
#   pinned novita/fp8 ....... 3 of the first 5 requests never returned
#   unpinned ................ 2 of the first 6 requests never returned
# So a hard pin makes the stall worse but does NOT cause it: OpenRouter queues
# requests for this key at very low concurrency and holds the socket open, and
# forbidding fallbacks (allow_fallbacks=False) removes the only escape route.
# Pinning at this hang rate makes the remaining models unrunnable, so it is
# disabled and provenance comes from the LOGGED provider on every call
# (msg["_provider"] -> the `providers` field) instead of from an asserted pin.
# That is the weaker guarantee but the honest one: it records what actually
# served each call rather than what we asked for. The PROVIDERS table above is
# retained as the documented preference for a future re-run.
PIN_PROVIDER = False

# The harness reads C.PROVIDER. run_model.py takes exactly one model per
# invocation, so it resolves the table into PROVIDER before handing off.
PROVIDER = None


def provider_for(model: str):
    """The pinned endpoint for `model`. Raises rather than returning None: an
    unpinned model would silently fall back to load-balanced routing, which is the
    failure the pin exists to prevent."""
    try:
        return PROVIDERS[model]
    except KeyError:
        raise KeyError(
            f"no provider pinned for {model!r}. Add one to config.PROVIDERS after "
            f"checking https://openrouter.ai/api/v1/models/{model}/endpoints - "
            f"running unpinned would make the served system non-reproducible."
        )


TEMPERATURE = 0.0
REQUEST_TIMEOUT = 180

# A ceiling enforced outside httpx (see harness.call_openrouter). OpenRouter
# queues requests past an account-wide concurrency limit while holding the socket
# open, so httpx's read timer never fires and the run deadlocks with no error row.
# Without this a stalled run is indistinguishable from a slow one. Set just ABOVE
# REQUEST_TIMEOUT (180) on purpose: httpx still owns the normal slow-response case,
# so this only ever fires on the pathological "socket alive, no bytes" state and
# cannot turn a legitimately slow reasoning call into a false failure.
REQUEST_DEADLINE = 200

# Deliberately low. Measured 2026-08-30: this key is served ~2-3 requests at a
# time; at 16 the first batch returns and every later request queues forever,
# which is what killed two runs at ~19 of 480 items. Throughput only - the items
# are independent and TEMPERATURE is 0, so this cannot move a result.
CONCURRENCY = 2

# --- Gold ---
# Built OFFLINE by build_dl_gold.groovy from the DEPLOYED release. Never from the
# service: that circularity is what invalidated the IRI gold set.
ONTOLOGIES = ["go", "cl", "so"]     # large / medium / small (51,937 / 19,151 / 2,752 classes)
