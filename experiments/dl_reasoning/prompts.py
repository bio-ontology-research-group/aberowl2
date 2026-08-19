"""Prompts for the DL-reasoning task.

Task: given a class expression stated in NATURAL LANGUAGE, return the SET of
ontology classes that satisfy it.

The natural-language rendering deliberately does NOT contain Manchester syntax.
Translating "all classes that are 'part of' some 'cell nucleus'" into
`<BFO_0000050> some <GO_0005634>` is the formulation stage we are measuring; if
the prompt handed over the syntax, that stage would be trivialised.

Arms:
  none          no tools                     -> parametric recall
  lookup        find_iri + search_classes    -> grounding WITHOUT reasoning
  dlquery       + run_dl_query               -> full capability
  dlquery_hint  as dlquery, plus a Manchester syntax example in the system prompt

`dlquery_hint` is the ablation that answers the strongest objection to this
experiment: that formulation failures measure our tool documentation rather than
agent capability. If the hint closes the gap, that is itself a finding about
designing agent-facing reasoning tools.
"""

REGIMES = ("forced",)

TASKS = {
    "T1": "subsumption retrieval - all subclasses of a named class",
    "T2": "existential retrieval - all classes satisfying a role restriction; "
          "NOT reachable by label lookup, only by reasoning",
}

_BASE = (
    "You answer queries about ontology class hierarchies.\n"
    "Return EVERY class that satisfies the query, and no others.\n\n"
    "Answer with one line per class, each line exactly:\n"
    "  IRI: <full IRI>\n"
    "If no class satisfies the query, answer exactly:\n"
    "  NONE\n"
    "Output nothing else: no prose, no numbering, no explanation."
)

# Only for the ablation arm. A minimal, correct Manchester example -- deliberately
# using a property and class that appear in NO gold item, so it cannot leak an
# answer (the leak that W20 flagged in the IRI experiment's template).
_SYNTAX_HINT = (
    "\n\nWhen using a reasoning tool, class expressions use Manchester syntax, "
    "where a role restriction is written as `<propertyIRI> some <classIRI>`, "
    "for example `<http://purl.obolibrary.org/obo/RO_0002202> some "
    "<http://purl.obolibrary.org/obo/UBERON_0000955>`. Full IRIs in angle "
    "brackets are accepted."
)


def system_prompt(condition: str, regime: str = "forced") -> str:
    p = _BASE
    if condition == "dlquery_hint":
        p += _SYNTAX_HINT
    return p


def user_prompt(nl: str, ontology: str | None = None) -> str:
    scope = f" (ontology: {ontology})" if ontology else ""
    return f"List {nl}{scope}."


# Which MCP tools each condition exposes via the API. `lookup` is the arm that
# isolates reasoning from grounding: it can resolve any label to an IRI but can
# never obtain an inferred subsumption.
CONDITION_TOOLS = {
    "none":         [],
    "lookup":       ["find_iri", "search_classes"],
    "dlquery":      ["find_iri", "search_classes", "run_dl_query"],
    "dlquery_hint": ["find_iri", "search_classes", "run_dl_query"],
}
