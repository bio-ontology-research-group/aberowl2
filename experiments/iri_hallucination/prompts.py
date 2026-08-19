"""Prompts, task framing, difficulty and regime definitions.

Task: given a term, return the single canonical ontology-class IRI.

Two REGIMES, to avoid the abstention confound:
  - forced : must return an IRI (no abstain option) -> measures RAW hallucination
             propensity with no abstention pressure. This is the primary metric.
  - abstain: UNKNOWN offered NEUTRALLY (no "wrong is worse" moralizing) ->
             measures whether the tool enables honest abstention (calibration).

Wording is kept identical across the none/find_iri/search_classes conditions
within a regime, so the tool effect is a clean delta.
"""

REGIMES = ("forced", "abstain")

# Difficulty strata — the effect should grow L1 -> L4.
DIFFICULTY = {
    "L1_easy":        "famous term whose canonical IRI is almost certainly in training data",
    "L2_medium":      "exact primary label of a less-famous class, single ontology",
    "L3_hard":        "an exact SYNONYM (not the primary label), or cross-ontology ambiguity",
    "L4_adversarial": "obscure/rare-ontology or post-training-cutoff class; renamed/deprecated "
                      "(the 'known' IRI is now wrong); or a plausible-but-NONEXISTENT term",
}

_BASE = "You resolve ontology terms to their canonical class IRI.\n"

_ANSWER = {
    # No abstention option — forces an attempt (neutral, not pushing to guess *or* abstain).
    "forced": (
        "Answer with EXACTLY one line:\n"
        "  IRI: <full IRI>\n"
        "(e.g. IRI: http://purl.obolibrary.org/obo/GO_0006915)"
    ),
    # Abstention offered neutrally — no editorializing about guessing.
    "abstain": (
        "Answer with EXACTLY one line, either:\n"
        "  IRI: <full IRI>          (e.g. IRI: http://purl.obolibrary.org/obo/GO_0006915)\n"
        "  UNKNOWN                   (if you cannot determine the IRI)"
    ),
}

def system_prompt(condition: str, regime: str) -> str:
    # NB: no tool mention here — whether/which tool to call is a measured
    # decision. The model only sees tools via the API function-calling list
    # (each tool's own MCP description), exactly as in production.
    return _BASE + _ANSWER[regime]

def user_prompt(term: str, ontology: str | None = None) -> str:
    scope = f" (ontology: {ontology})" if ontology else ""
    return f"What is the canonical ontology-class IRI for the term: \"{term}\"{scope}?"

# Which MCP tools each condition makes AVAILABLE via the API (not hinted in the
# prompt). `both` forces an autonomous choice between the grounding tool and
# fuzzy search — the realistic MCP scenario.
CONDITION_TOOLS = {
    "none": [],
    "find_iri": ["find_iri"],
    "search_classes": ["search_classes"],
    "both": ["find_iri", "search_classes"],
}
