"""Config for the IRI-hallucination-reduction experiment.

Measures whether the AberOWL `find_iri` MCP tool reduces the rate at which LLMs
hallucinate ontology-class IRIs, across a capability gradient and a difficulty
gradient. See README.md.
"""
import os

# --- OpenRouter (OpenAI-compatible) ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# --- AberOWL MCP grounding server (the feature under test) ---
MCP_URL = os.getenv("ABEROWL_MCP_URL", "https://beta.aber-owl.net/mcp/ontology/mcp")
# Base HTTP API for the scorer's independent IRI-existence check.
ABEROWL_API = os.getenv("ABEROWL_API", "https://beta.aber-owl.net/api")

# --- Subjects: capability gradient, all tool-calling capable (verified 2026-07 on OpenRouter) ---
MODELS = [
    # frontier
    "openai/gpt-5.5",
    "google/gemini-3.5-flash",
    "deepseek/deepseek-v3.2",       # robust, mature tool-calling (vs v4-pro preview)
    # small / open — where the grounding effect should be largest
    "qwen/qwen3.6-35b-a3b",
    "meta-llama/llama-4-scout",
    "openai/gpt-oss-20b",
]

# --- Conditions ---
#   none           : no tools (pure parametric)  -> baseline hallucination
#   find_iri       : only the exact-match grounding tool
#   search_classes : only fuzzy search (control — does find_iri beat naive search?)
# Core conditions: which tools are AVAILABLE via the API (never hinted in the
# prompt). `none` vs `find_iri` is the headline hallucination test.
# Optional secondary conditions (not run by default): "search_classes" (fuzzy
# control) and "both" (autonomous tool-selection). Add via --conditions.
CONDITIONS = ["none", "find_iri"]
# forced = must answer (raw hallucination); abstain = UNKNOWN allowed (calibration)
REGIMES = ["forced", "abstain"]

MAX_TOOL_TURNS = 6      # agent loop cap
TEMPERATURE = 0.0       # deterministic; bump + repeat for robustness runs
REQUEST_TIMEOUT = 120
CONCURRENCY = 10        # items in flight at once (reasoning models are slow -> parallelize)
