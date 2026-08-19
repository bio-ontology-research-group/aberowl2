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
# Set to None to restore load-balanced routing.
PROVIDER = {"order": ["gmicloud/fp8"], "allow_fallbacks": False}

TEMPERATURE = 0.0
REQUEST_TIMEOUT = 180
CONCURRENCY = 16

# --- Gold ---
# Built OFFLINE by build_dl_gold.groovy from the DEPLOYED release. Never from the
# service: that circularity is what invalidated the IRI gold set.
ONTOLOGIES = ["go", "cl", "so"]     # large / medium / small (51,937 / 19,151 / 2,747 classes)
