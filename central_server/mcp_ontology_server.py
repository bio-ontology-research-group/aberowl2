#!/usr/bin/env python3
"""
AberOWL MCP Ontology Server

Provides MCP tools for querying and browsing ontologies in the AberOWL
repository. Uses FastMCP with streamable HTTP transport for both local
and remote access.

Tools:
  - list_ontologies: List all registered ontologies with status
  - search_classes: Search for classes by label/synonym across ontologies
  - lookup_iri: Resolve a label / CURIE / candidate IRI to its canonical ontology IRI
  - run_dl_query: Execute a Description Logic query in Manchester OWL Syntax
  - get_class_info: Get detailed annotations for a specific class
  - get_ontology_info: Get metadata about a specific ontology
  - browse_hierarchy: Get subclasses/superclasses of a class
  - rewrite_sparql: Rewrite SPARQL with embedded OWL DL frames into plain SPARQL
  - list_sparql_examples: Curated SPARQL+OWL example queries to use as templates
  - query_sparql: Rewrite + execute against an external endpoint (Ontobee by default)

Usage:
  python mcp_ontology_server.py          # streamable HTTP on port 8766
  python mcp_ontology_server.py --stdio  # stdio transport (Claude Desktop)
"""

import json
import os
import re
import sys
from typing import Any

import aiohttp
from mcp.server.fastmcp import FastMCP

CENTRAL_SERVER_URL = os.getenv("CENTRAL_SERVER_URL", "http://localhost:80")
PORT = int(os.getenv("MCP_ONTOLOGY_PORT", "8766"))

mcp = FastMCP(
    "aberowl-ontology",
    host="0.0.0.0",
    port=PORT,
)


async def _api_get(path: str, params: dict | None = None) -> dict:
    """Make a GET request to the central AberOWL API."""
    url = f"{CENTRAL_SERVER_URL.rstrip('/')}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                return await resp.json()
            text = await resp.text()
            return {"error": f"HTTP {resp.status}: {text[:500]}"}


async def _api_post(path: str, body: dict) -> dict:
    """Make a POST request to the central AberOWL API."""
    url = f"{CENTRAL_SERVER_URL.rstrip('/')}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status == 200:
                return await resp.json()
            text = await resp.text()
            return {"error": f"HTTP {resp.status}: {text[:500]}"}


@mcp.tool(
    description=(
        "List all ontologies in the AberOWL repository with their status "
        "(online/offline), class count, and other metadata. Use this to "
        "discover available ontologies before querying them."
    ),
)
async def list_ontologies() -> str:
    data = await _api_get("/api/listOntologies")
    ontologies = data.get("result", [])
    lines = [f"Found {len(ontologies)} ontologies:\n"]
    for ont in ontologies:
        status = "ONLINE" if ont.get("status") == "online" else "offline"
        lines.append(f"  [{status}] {ont.get('id', '?')} - {ont.get('title', '')}")
    return "\n".join(lines)


@mcp.tool(
    description=(
        "Search for ontology classes by label, synonym, or OBO ID across "
        "all ontologies (or a specific one). Returns matching classes with "
        "their IRIs, labels, definitions, and source ontology.\n\n"
        "Examples:\n"
        "  - Search for 'apoptosis' across all ontologies\n"
        "  - Search for 'GO:0006915' by OBO ID\n"
        "  - Search for 'cell death' in the GO ontology specifically"
    ),
)
async def search_classes(query: str, ontology: str | None = None, size: int = 50) -> str:
    """
    Args:
        query: Search term (label, synonym, or OBO ID)
        ontology: Optional ontology ID to restrict the search (e.g. 'go', 'hp')
        size: Maximum number of results (default 50)
    """
    params: dict[str, Any] = {"query": query, "size": str(size)}
    if ontology:
        params["ontologies"] = ontology
    data = await _api_get("/api/search_all", params)
    results = data.get("result", [])
    if not results:
        return f"No results found for '{query}'"
    lines = [f"Found {len(results)} results for '{query}':\n"]
    for r in results[:size]:
        label = r.get("label", r.get("class", "?"))
        if isinstance(label, list):
            label = label[0] if label else "?"
        ont = r.get("ontology", "?")
        iri = r.get("class", "")
        defn = r.get("definition", "")
        if isinstance(defn, list):
            defn = defn[0] if defn else ""
        lines.append(f"  {label} [{ont}]")
        lines.append(f"    IRI: {iri}")
        if defn:
            lines.append(f"    Def: {defn[:150]}")
    return "\n".join(lines)


# --- IRI resolution helpers (used by lookup_iri) -------------------------------
#
# Using the wrong IRI is a dominant agent failure mode: a guessed-but-wrong IRI
# silently returns zero results from the reasoner/SPARQL tools with no error.
# These helpers resolve a label, CURIE/OBO id, or candidate IRI to the canonical
# IRI. They prefer the fast central ES index (exact oboid/label) and fall back to
# the reasoner (which works even when the class index is unpopulated), so the
# tool is robust to ES outages.

_IRI_RE = re.compile(r"^https?://", re.I)
# CURIE / OBO short form, e.g. GO:0006915 or GO_0006915 (no spaces).
_CURIE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*[:_][A-Za-z0-9][A-Za-z0-9_]*$")
# Local fragment that looks like an OBO id, e.g. GO_0006915 -> ("GO", "0006915").
_FRAG_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)_([A-Za-z0-9]+)$")


def _strip_iri(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1].strip()
    return s


def _first(v: Any) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v) if v is not None else ""


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _classify(term: str) -> str:
    """Classify the input as 'iri', 'curie', or 'text' (label)."""
    t = _strip_iri(term)
    if _IRI_RE.match(t):
        return "iri"
    if " " not in t and _CURIE_RE.match(t):
        return "curie"
    return "text"


def _iri_to_curie(iri: str) -> str | None:
    frag = re.split(r"[#/]", iri.rstrip("#/"))[-1]
    m = _FRAG_RE.match(frag)
    return f"{m.group(1)}:{m.group(2)}" if m else None


def _curie_parts(curie: str) -> tuple[str, str]:
    m = re.match(r"^([A-Za-z][A-Za-z0-9]*)[:_](.+)$", curie)
    return (m.group(1), m.group(2)) if m else (curie, "")


def _quote_label(label: str) -> str:
    """Single-quote a label for Manchester OWL Syntax."""
    return "'" + label.replace("'", "\\'") + "'"


def _record(iri: str, label: str = "", ontology: str = "", curie: str = "",
            definition: str = "", match: str = "exact") -> dict:
    iri = _strip_iri(iri)
    return {
        "iri": iri,
        "label": label or curie or _iri_to_curie(iri) or iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1],
        "ontology": ontology or "",
        "curie": curie or _iri_to_curie(iri) or "",
        "definition": definition or "",
        "match": match,
    }


async def _es_records(term: str, ontology: str | None, size: int) -> list[dict]:
    """Class hits from the central ES index (empty if the index is unpopulated)."""
    params: dict[str, Any] = {"query": term, "size": str(size)}
    if ontology:
        params["ontologies"] = ontology
    try:
        data = await _api_get("/api/search_all", params)
    except Exception:
        return []
    out = []
    for r in (data.get("result", []) if isinstance(data, dict) else []):
        iri = _strip_iri(r.get("class") or r.get("owlClass") or "")
        if not iri:
            continue
        out.append(_record(iri, _first(r.get("label")), r.get("ontology", ""),
                           _first(r.get("oboid")), _first(r.get("definition"))))
    return out


async def _dl_equivalent(query: str, ontology: str) -> list[dict]:
    """Resolve via the reasoner: equivalent classes of a label/IRI query.

    Always scoped to one ontology — an unscoped dlquery_all fans out to every
    worker, which is both slow (tens of seconds) and noisy, so the reasoner
    fallback is only used when an ontology is known.
    """
    params: dict[str, Any] = {
        "query": query, "type": "equivalent", "labels": "true", "ontologies": ontology,
    }
    try:
        data = await _api_get("/api/dlquery_all", params)
    except Exception:
        return []
    out = []
    for r in (data.get("result", []) if isinstance(data, dict) else []):
        iri = _strip_iri(r.get("class") or r.get("owlClass") or "")
        if iri:
            out.append(_record(iri, _first(r.get("label")), r.get("ontology", ""),
                               "", _first(r.get("definition")), match="reasoner"))
    return out


async def _validate_iri(iri: str, ontology: str) -> dict | None:
    """Confirm an IRI exists in an ontology and fetch its label/definition."""
    try:
        data = await _api_get("/api/getClass", {"query": iri, "ontology": ontology})
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None
    riri = _strip_iri(data.get("class") or iri)
    return _record(riri, _first(data.get("label")), data.get("ontology") or ontology,
                   _first(data.get("oboid")), _first(data.get("definition")))


def _dedup(records: list[dict], limit: int) -> list[dict]:
    seen, out = set(), []
    for r in records:
        if r["iri"] and r["iri"] not in seen:
            seen.add(r["iri"])
            out.append(r)
        if len(out) >= limit:
            break
    return out


async def _resolve(term: str, ontology: str | None, limit: int) -> tuple[str, list[dict]]:
    kind = _classify(term)
    t = _strip_iri(term)

    if kind == "iri":
        # Validate against the most likely ontology: the caller's hint, then the
        # one implied by an OBO-style IRI (purl.obolibrary.org/obo/GO_… -> go).
        guesses, seen_g = [], set()
        if ontology:
            guesses.append(ontology)
        curie = _iri_to_curie(t)
        if curie:
            guesses.append(curie.split(":")[0].lower())
        for g in guesses:
            if not g or g in seen_g:
                continue
            seen_g.add(g)
            rec = await _validate_iri(t, g)
            if rec:
                return kind, [rec]
        # Scoped reasoner backstop only when an ontology was given explicitly.
        if ontology:
            hits = [r for r in await _dl_equivalent(f"<{t}>", ontology) if r["iri"] == t]
            return kind, _dedup(hits, limit)
        return kind, []

    if kind == "curie":
        prefix, local = _curie_parts(t)
        colon = f"{prefix}:{local}"
        # 1) ES exact oboid match.
        es = [r for r in await _es_records(colon, ontology, limit)
              if _norm(r["curie"]) == _norm(colon)]
        if es:
            for r in es:
                r["match"] = "curie"
            return kind, _dedup(es, limit)
        # 2) Construct the OBO PURL IRI and validate it against the ontology the
        #    prefix implies (GO:… -> go) unless the caller scoped one explicitly.
        cand = f"http://purl.obolibrary.org/obo/{prefix}_{local}"
        rec = await _validate_iri(cand, ontology or prefix.lower())
        if rec:
            rec["match"] = "curie"
            return kind, [rec]
        return kind, []

    # text / label: ES first (fast, cross-ontology); reasoner only when scoped,
    # since an unscoped label query would fan out to every worker.
    records = await _es_records(t, ontology, limit)
    if not records and ontology:
        records = await _dl_equivalent(_quote_label(t), ontology)
    return kind, _dedup(records, limit)


async def _suggest(term: str, ontology: str | None, limit: int = 5) -> list[dict]:
    """Best-effort 'did you mean' suggestions for a failed lookup (needs ES)."""
    t = _strip_iri(term)
    frag = re.split(r"[#/]", t.rstrip("#/"))[-1] if _IRI_RE.match(t) else t
    frag = re.sub(r"[_:]", " ", frag).strip()
    return _dedup(await _es_records(frag, ontology, limit), limit) if frag else []


def _fmt_record(rec: dict, idx: int | None = None) -> str:
    head = f"{idx}. " if idx else "✓ "
    lines = [f"{head}{rec['iri']}"]
    pad = "   "
    if rec.get("label"):
        lines.append(f"{pad}label:    {rec['label']}")
    if rec.get("curie"):
        lines.append(f"{pad}CURIE:    {rec['curie']}")
    if rec.get("ontology"):
        lines.append(f"{pad}ontology: {rec['ontology']}")
    if rec.get("definition"):
        lines.append(f"{pad}def:      {rec['definition'][:160]}")
    return "\n".join(lines)


@mcp.tool(
    description=(
        "Resolve a class name, CURIE/OBO id (e.g. GO:0006915), or a candidate "
        "IRI to its canonical AberOWL ontology IRI. USE THIS to obtain or verify "
        "an IRI before passing one to get_class_info, browse_hierarchy, "
        "run_dl_query, or SPARQL — a guessed-but-wrong IRI silently returns zero "
        "results with no error, which is a common failure.\n\n"
        "Accepts:\n"
        "  - a label, e.g. 'apoptosis' or 'apoptotic process'\n"
        "  - a CURIE / OBO id, e.g. 'GO:0006915' or 'GO_0006915'\n"
        "  - a full IRI to validate, e.g. 'http://purl.obolibrary.org/obo/GO_0006915'\n\n"
        "Pass `ontology` (e.g. 'go', 'hp') to scope and disambiguate — recommended "
        "and faster. Returns the canonical IRI, label, CURIE, and source ontology. "
        "If the IRI/CURIE does not exist it says so and suggests close matches, so "
        "you can correct course instead of querying a non-existent class."
    ),
)
async def lookup_iri(term: str, ontology: str | None = None, limit: int = 10) -> str:
    """
    Args:
        term: A class label, CURIE/OBO id, or candidate IRI to resolve/validate.
        ontology: Optional ontology ID to scope/disambiguate (e.g. 'go', 'hp').
        limit: Maximum number of matches to return (default 10).
    """
    term = (term or "").strip()
    if not term:
        return "Provide a class name, CURIE (e.g. GO:0006915), or IRI to look up."

    kind, records = await _resolve(term, ontology, limit)

    if records:
        if len(records) == 1:
            r = records[0]
            return (
                f'Resolved "{term}" ({kind}) -> 1 match:\n\n{_fmt_record(r)}\n\n'
                f"Use this IRI verbatim: in run_dl_query wrap it as <{r['iri']}>; "
                f"in get_class_info / browse_hierarchy pass class_iri=\"{r['iri']}\""
                + (f" with ontology=\"{r['ontology']}\"" if r['ontology'] else "")
                + f"; in SPARQL use <{r['iri']}>."
            )
        lines = [f'Resolved "{term}" ({kind}) -> {len(records)} matches'
                 + ("" if ontology else " (pass `ontology` to narrow):") + "\n"]
        for i, r in enumerate(records, 1):
            lines.append(_fmt_record(r, idx=i))
        return "\n".join(lines)

    # Not found — offer suggestions.
    kind_word = "an IRI" if kind == "iri" else f"a {kind}"
    out = [f'✗ Could not resolve "{term}" as {kind_word}.']
    if kind in ("iri", "curie"):
        out.append("  It may not exist in AberOWL, or the ontology uses a different namespace.")
    sugg = await _suggest(term, ontology, 5)
    if sugg:
        out.append("  Closest matches:")
        for r in sugg:
            tail = f"  ({r['label']} [{r['ontology']}])" if r.get("label") else ""
            out.append(f"  - {r['iri']}{tail}")
    else:
        hint = "omit `ontology`" if ontology else "specify the `ontology`"
        out.append(f'  No close matches. Try search_classes("{term}") or {hint}.')
    return "\n".join(out)


@mcp.tool(
    description=(
        "Run a Description Logic (DL) query in Manchester OWL Syntax "
        "against one or all ontologies. This uses OWL reasoning to find "
        "classes based on logical relationships.\n\n"
        "Manchester OWL Syntax guide:\n"
        "  - Named classes: use the label in single quotes, e.g., 'cell'\n"
        "  - Class IRIs: use angle brackets, e.g., <http://purl.obolibrary.org/obo/GO_0005623>\n"
        "  - Intersection: 'cell' and 'part of' some 'organism'\n"
        "  - Union: 'cell' or 'tissue'\n"
        "  - Existential: 'part of' some 'cell'\n"
        "  - Universal: 'part of' only 'cell'\n"
        "  - Negation: not 'cell'\n\n"
        "Query types:\n"
        "  - subclass: direct subclasses only\n"
        "  - subeq: subclasses + equivalent (most common)\n"
        "  - superclass: direct superclasses only\n"
        "  - supeq: superclasses + equivalent\n"
        "  - equivalent: equivalent classes only\n\n"
        "Examples:\n"
        "  - query='cell', type='subclass' -> all subclasses of 'cell'\n"
        "  - query=\"'part of' some 'cell'\", type='subeq' -> classes that are part of a cell\n"
        "  - query=\"'has part' some 'nucleus'\", type='subeq' -> things that have a nucleus"
    ),
)
async def run_dl_query(query: str, type: str = "subeq", ontology: str | None = None) -> str:
    """
    Args:
        query: DL query in Manchester OWL Syntax
        type: Query type - subclass, subeq, superclass, supeq, or equivalent (default subeq)
        ontology: Optional ontology ID to restrict reasoning
    """
    params: dict[str, Any] = {"query": query, "type": type, "labels": "true"}
    if ontology:
        params["ontologies"] = ontology
    data = await _api_get("/api/dlquery_all", params)
    results = data.get("result", [])
    if not results:
        return f"No results for DL query: {query} (type: {type})"
    lines = [f"Found {len(results)} results for {type} query: {query}\n"]
    for r in results[:100]:
        label = r.get("label", r.get("owlClass", "?"))
        ont = r.get("ontology", "?")
        iri = r.get("class", "")
        lines.append(f"  {label} [{ont}] - {iri}")
    return "\n".join(lines)


@mcp.tool(
    description=(
        "Get detailed information about a specific ontology class, "
        "including all annotations (labels, definitions, synonyms), "
        "axioms, and relationships. Requires both the class IRI and "
        "the ontology ID."
    ),
)
async def get_class_info(class_iri: str, ontology: str) -> str:
    """
    Args:
        class_iri: Full IRI of the class (e.g. 'http://purl.obolibrary.org/obo/GO_0005623')
        ontology: Ontology ID (e.g. 'go', 'hp')
    """
    data = await _api_get("/api/getClass", {"query": class_iri, "ontology": ontology})
    if "error" in data:
        return f"Error: {data['error']}"
    return json.dumps(data, indent=2)


@mcp.tool(
    description=(
        "Get metadata about a specific ontology including title, "
        "description, version, class count, property count, license, "
        "and classification status."
    ),
)
async def get_ontology_info(ontology: str) -> str:
    """
    Args:
        ontology: Ontology ID (e.g. 'go', 'hp', 'chebi')
    """
    data = await _api_get("/api/getOntology", {"ontology": ontology})
    if "error" in data or "detail" in data:
        return f"Error: {data.get('error') or data.get('detail')}"
    return json.dumps(data, indent=2)


@mcp.tool(
    description=(
        "Browse the class hierarchy of an ontology. Get the direct "
        "subclasses or superclasses of a given class. Use this to "
        "navigate the ontology tree structure.\n\n"
        "Pass a class IRI (or 'owl:Thing' for the root) and direction "
        "(subclass or superclass) to explore the hierarchy."
    ),
)
async def browse_hierarchy(class_iri: str, ontology: str, direction: str = "subclass") -> str:
    """
    Args:
        class_iri: Class IRI to browse from (use 'owl:Thing' for root)
        ontology: Ontology ID
        direction: 'subclass' or 'superclass' (default subclass)
    """
    if class_iri == "owl:Thing":
        class_iri = "<http://www.w3.org/2002/07/owl#Thing>"
    params = {
        "query": class_iri,
        "type": direction,
        "ontologies": ontology,
        "labels": "true",
    }
    data = await _api_get("/api/dlquery_all", params)
    results = data.get("result", [])
    label = "subclasses" if direction == "subclass" else "superclasses"
    if not results:
        return f"No {label} found for {class_iri}"
    lines = [f"Direct {label} of {class_iri} ({len(results)} found):\n"]
    for r in results:
        lbl = r.get("label", r.get("owlClass", "?"))
        iri = r.get("class", "")
        lines.append(f"  {lbl} - {iri}")
    return "\n".join(lines)


@mcp.tool(
    description=(
        "Rewrite a SPARQL query that contains embedded OWL DL frames into "
        "plain SPARQL with concrete class IRIs spliced in. AberOWL only "
        "rewrites — it does not execute. Run the returned query against "
        "any SPARQL endpoint you choose (Ontobee, UniProt, Wikidata, …).\n\n"
        "Two embedded frame patterns are supported:\n"
        "  1. VALUES ?var { OWL <type> <ontology_id> { dl_query } }\n"
        "  2. FILTER OWL(?var, <type>, <ontology_id>, \"dl_query\")\n\n"
        "Where <type> is subclass | superclass | equivalent | subeq | supeq, "
        "<ontology_id> is a registered AberOWL ontology id (case-insensitive; "
        "may contain '-' or '.', e.g. go-plus, chebi.ext), and <dl_query> is "
        "a Manchester OWL Syntax expression — a class label like 'cell death' "
        "or a compound such as 'part of' some 'cell'.\n\n"
        "Examples (call list_sparql_examples for the full set):\n"
        "  • Subclasses of 'cell death' in go-plus (VALUES form):\n"
        "      SELECT ?c ?label WHERE {\n"
        "        VALUES ?c { OWL subeq go-plus { 'cell death' } }\n"
        "        ?c <http://www.w3.org/2000/01/rdf-schema#label> ?label .\n"
        "      }\n\n"
        "  • UniProt federation — proteins classified under GO subclasses:\n"
        "      SELECT ?protein ?goClass WHERE {\n"
        "        VALUES ?goClass { OWL subeq go-plus { 'cell death' } }\n"
        "        ?protein a <http://purl.uniprot.org/core/Protein> ;\n"
        "                 <http://purl.uniprot.org/core/classifiedWith> ?goClass .\n"
        "      } LIMIT 50\n\n"
        "  • FILTER form, equivalent semantics:\n"
        "      SELECT ?c WHERE {\n"
        "        ?c <http://www.w3.org/2000/01/rdf-schema#label> ?l .\n"
        "        FILTER OWL(?c, subeq, go-plus, \"'part of' some 'cell'\")\n"
        "      }\n\n"
        "Frames whose ontology is unknown or whose worker is offline are "
        "reported as errors and replaced with an empty match in the rewritten "
        "query, so the rest of the query is still usable."
    ),
)
async def rewrite_sparql(query: str) -> str:
    """
    Args:
        query: SPARQL query string containing one or more OWL DL frames.
    """
    data = await _api_post("/api/sparql", {"query": query})
    if data.get("error") and "rewritten_query" not in data:
        return f"Error: {data['error']}"

    rewritten = data.get("rewritten_query", "")
    expansions = data.get("expansions") or []
    errors = data.get("errors") or []

    lines: list[str] = []
    if expansions:
        lines.append(f"Resolved {len(expansions)} OWL frame(s):")
        for exp in expansions:
            lines.append(
                f"  - {exp.get('pattern')} {exp.get('variable')}: "
                f"{exp.get('type')} on {exp.get('ontology')} → "
                f"{exp.get('result_count')} classes"
            )
        lines.append("")
    if errors:
        lines.append(f"Could not resolve {len(errors)} frame(s) "
                     "(replaced with an empty match in the rewritten query):")
        for err in errors:
            lines.append(
                f"  - {err.get('pattern')} {err.get('variable')} "
                f"({err.get('type')} on {err.get('ontology')}): {err.get('error')}"
            )
        lines.append("")
    if not expansions and not errors:
        lines.append("No OWL DL frames found in the query — returned unchanged.\n")

    lines.append("Rewritten query:")
    lines.append(rewritten or "(empty)")
    return "\n".join(lines)


_SPARQL_EXAMPLES: list[dict[str, str]] = [
    {
        "title": "Subclasses of 'cell death' in GO (VALUES form)",
        "ontology": "go-plus",
        "endpoint": "https://sparql.hegroup.org/sparql",
        "description": (
            "Resolves every subclass of 'cell death' in GO via the reasoner, "
            "then asks Ontobee for each class's label."
        ),
        "query": (
            "SELECT ?c ?label WHERE {\n"
            "  VALUES ?c { OWL subeq go-plus { 'cell death' } }\n"
            "  ?c <http://www.w3.org/2000/01/rdf-schema#label> ?label .\n"
            "} LIMIT 50"
        ),
    },
    {
        "title": "UniProt: proteins classified under a GO subtree",
        "ontology": "go-plus",
        "endpoint": "https://sparql.uniprot.org/sparql",
        "description": (
            "Federation pattern — AberOWL resolves the GO subtree, the "
            "rewritten query goes to UniProt to find matching proteins."
        ),
        "query": (
            "PREFIX up: <http://purl.uniprot.org/core/>\n"
            "SELECT ?protein ?mnemonic ?goClass WHERE {\n"
            "  VALUES ?goClass { OWL subeq go-plus { 'cell death' } }\n"
            "  ?protein a up:Protein ;\n"
            "           up:classifiedWith ?goClass ;\n"
            "           up:mnemonic ?mnemonic .\n"
            "} LIMIT 50"
        ),
    },
    {
        "title": "Manchester compound DL query (FILTER form)",
        "ontology": "go-plus",
        "endpoint": "https://sparql.hegroup.org/sparql",
        "description": (
            "Compound DL queries work the same way: any Manchester syntax "
            "is accepted inside the OWL frame."
        ),
        "query": (
            "SELECT ?c ?label WHERE {\n"
            "  ?c <http://www.w3.org/2000/01/rdf-schema#label> ?label .\n"
            "  FILTER OWL(?c, subeq, go-plus, \"'part of' some 'cell'\")\n"
            "} LIMIT 50"
        ),
    },
    {
        "title": "Multiple frames in one query",
        "ontology": "go-plus, chebi",
        "endpoint": "https://sparql.hegroup.org/sparql",
        "description": (
            "Each OWL frame is resolved independently against the named "
            "ontology — mix and match across ontologies in one rewrite."
        ),
        "query": (
            "SELECT ?go ?chem WHERE {\n"
            "  VALUES ?go   { OWL subeq go-plus { 'apoptotic process' } }\n"
            "  VALUES ?chem { OWL subeq chebi   { 'small molecule' } }\n"
            "}"
        ),
    },
    {
        "title": "Pizza demo — works against the local test worker",
        "ontology": "pizza",
        "endpoint": "(use the /api/sparql rewriter only — no public endpoint)",
        "description": (
            "Smallest reproducible example, useful for verifying the local "
            "test harness before pointing at GO / UniProt."
        ),
        "query": (
            "SELECT ?c WHERE {\n"
            "  VALUES ?c { OWL subeq pizza { Pizza } }\n"
            "}"
        ),
    },
]


@mcp.tool(
    description=(
        "Return curated SPARQL example queries that demonstrate the OWL DL "
        "frame syntax accepted by `rewrite_sparql`. Each example includes a "
        "title, target ontology, suggested SPARQL endpoint to run the "
        "rewritten query against, and the query itself. Use these as "
        "templates when building new queries."
    ),
)
async def list_sparql_examples() -> str:
    lines: list[str] = [f"{len(_SPARQL_EXAMPLES)} SPARQL + OWL examples:\n"]
    for i, ex in enumerate(_SPARQL_EXAMPLES, 1):
        lines.append(f"--- {i}. {ex['title']} ---")
        lines.append(f"Ontology: {ex['ontology']}")
        lines.append(f"Endpoint: {ex['endpoint']}")
        lines.append(f"What it does: {ex['description']}")
        lines.append("Query:")
        for q_line in ex["query"].splitlines():
            lines.append(f"  {q_line}")
        lines.append("")
    lines.append(
        "To resolve any of these, pass the query to `rewrite_sparql`. "
        "AberOWL returns the rewritten SPARQL with concrete IRIs spliced "
        "in; you then run it against the suggested endpoint."
    )
    return "\n".join(lines)


DEFAULT_SPARQL_ENDPOINT = "https://sparql.hegroup.org/sparql"  # Ontobee


async def _execute_sparql(endpoint: str, query: str, timeout: int = 60) -> dict[str, Any]:
    """POST a SPARQL query to an external endpoint and return the JSON results.

    AberOWL itself does not host a SPARQL store; this helper just forwards
    to whatever endpoint the user chose.
    """
    headers = {
        "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint,
                data={"query": query},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    return {"error": f"endpoint returned HTTP {resp.status}: {text[:500]}"}
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"error": f"endpoint returned non-JSON: {text[:500]}"}
    except Exception as e:
        return {"error": f"endpoint unreachable: {e}"}


def _format_sparql_results(results: dict[str, Any], limit: int = 50) -> list[str]:
    """Render a SPARQL JSON results object as compact text rows."""
    head = results.get("head", {})
    body = results.get("results", {})
    bindings = body.get("bindings", []) if isinstance(body, dict) else []
    vars_ = head.get("vars", []) if isinstance(head, dict) else []

    if not bindings:
        # ASK queries put the answer in `boolean`.
        if "boolean" in results:
            return [f"ASK result: {results['boolean']}"]
        return ["No results."]

    lines: list[str] = [f"{len(bindings)} row(s)" + (f" (showing first {limit})" if len(bindings) > limit else "") + ":"]
    for row in bindings[:limit]:
        cells = []
        for v in vars_:
            cell = row.get(v, {})
            value = cell.get("value", "") if isinstance(cell, dict) else ""
            cells.append(f"{v}={value}")
        lines.append("  " + " | ".join(cells))
    return lines


@mcp.tool(
    description=(
        "Rewrite a SPARQL query containing OWL DL frames AND execute it "
        "against an external SPARQL endpoint, returning the result rows. "
        "AberOWL still only does the rewriting; this tool just forwards "
        "the rewritten SPARQL to the chosen endpoint and formats whatever "
        "comes back.\n\n"
        "If `endpoint` is omitted, the rewritten query runs against "
        f"Ontobee ({DEFAULT_SPARQL_ENDPOINT}). Common alternatives:\n"
        "  - https://sparql.uniprot.org/sparql       (UniProt)\n"
        "  - https://query.wikidata.org/sparql       (Wikidata)\n"
        "  - https://dbpedia.org/sparql              (DBpedia)\n"
        "  - https://bio2rdf.org/sparql              (Bio2RDF)\n\n"
        "Frame syntax is identical to `rewrite_sparql`. Per-frame "
        "resolution failures (unknown ontology, offline worker, DL parse "
        "error) are reported but the rest of the query is still executed.\n\n"
        "Example: SELECT ?c ?label WHERE { VALUES ?c { OWL subeq go-plus { 'cell death' } } "
        "?c <http://www.w3.org/2000/01/rdf-schema#label> ?label . } LIMIT 50"
    ),
)
async def query_sparql(query: str, endpoint: str | None = None) -> str:
    """
    Args:
        query:    SPARQL query string. May contain OWL DL frames; they will
                  be resolved by AberOWL before the query is executed.
        endpoint: SPARQL endpoint to execute against. Defaults to Ontobee
                  (https://sparql.hegroup.org/sparql).
    """
    target = (endpoint or DEFAULT_SPARQL_ENDPOINT).strip()

    rewrite = await _api_post("/api/sparql", {"query": query})
    if rewrite.get("error") and "rewritten_query" not in rewrite:
        return f"Rewrite error: {rewrite['error']}"

    rewritten = rewrite.get("rewritten_query", "") or query
    expansions = rewrite.get("expansions") or []
    errors = rewrite.get("errors") or []

    lines: list[str] = [f"Endpoint: {target}"]
    if expansions:
        lines.append(f"Resolved {len(expansions)} OWL frame(s):")
        for exp in expansions:
            lines.append(
                f"  - {exp.get('pattern')} {exp.get('variable')}: "
                f"{exp.get('type')} on {exp.get('ontology')} → "
                f"{exp.get('result_count')} classes"
            )
    if errors:
        lines.append(f"Could not resolve {len(errors)} frame(s) "
                     "(replaced with an empty match in the executed query):")
        for err in errors:
            lines.append(
                f"  - {err.get('pattern')} {err.get('variable')} "
                f"({err.get('type')} on {err.get('ontology')}): {err.get('error')}"
            )

    results = await _execute_sparql(target, rewritten)
    if results.get("error"):
        lines.append("")
        lines.append(f"Endpoint error: {results['error']}")
        lines.append("")
        lines.append("Rewritten query (you can re-run this manually):")
        lines.append(rewritten)
        return "\n".join(lines)

    lines.append("")
    lines.extend(_format_sparql_results(results))
    return "\n".join(lines)


def main() -> None:
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        print(f"AberOWL Ontology MCP server running on http://0.0.0.0:{PORT}/mcp")
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
