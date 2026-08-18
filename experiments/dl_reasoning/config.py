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
MODELS = [
    "openai/gpt-oss-20b",
    "meta-llama/llama-4-scout",
    "qwen/qwen3.6-35b-a3b",
    "deepseek/deepseek-v3.2",
    "google/gemini-3.5-flash",
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

TEMPERATURE = 0.0
REQUEST_TIMEOUT = 180
CONCURRENCY = 16

# --- Gold ---
# Built OFFLINE by build_dl_gold.groovy from the DEPLOYED release. Never from the
# service: that circularity is what invalidated the IRI gold set.
ONTOLOGIES = ["go", "cl", "so"]     # large / medium / small (51,937 / 19,151 / 2,747 classes)
