import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useDocumentTitle } from '../hooks/useDocumentTitle'

/**
 * Docs / MCP page.
 *
 * AberOWL 2's primary purpose is to serve ontology reasoning to AI agents over
 * the Model Context Protocol (MCP). This page is the connect-and-go reference:
 * the endpoint URL, copy-paste client configs, and the tool catalogue.
 */

// The MCP endpoint is proxied at <origin>/mcp/ontology/mcp on the deployment.
// Derive it from the current origin so the snippets are correct wherever the
// SPA is served (production beta, a mirror, etc.).
const MCP_URL = `${typeof window !== 'undefined' ? window.location.origin : 'https://beta.aber-owl.net'}/mcp/ontology/mcp`

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        })
      }}
      className="text-xs px-2 py-1 rounded-md border border-gray-300 text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-colors shrink-0"
      aria-label={label}
    >
      {copied ? '✓ Copied' : label}
    </button>
  )
}

function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  return (
    <div className="relative group">
      <div className="absolute right-2 top-2 z-10">
        <CopyButton text={code} />
      </div>
      {lang && (
        <span className="absolute left-3 top-2 text-[10px] uppercase tracking-wider text-gray-400 font-mono">{lang}</span>
      )}
      <pre className="bg-gray-900 text-gray-100 rounded-lg p-4 pt-7 overflow-x-auto text-xs leading-relaxed font-mono">
        <code>{code}</code>
      </pre>
    </div>
  )
}

const TOOLS: Array<{ name: string; sig: string; desc: string }> = [
  { name: 'list_ontologies', sig: '()', desc: 'List every ontology in the repository with status, class count, and metadata. Start here to discover what is available.' },
  { name: 'search_classes', sig: '(query, ontology?, size?)', desc: 'Full-text search for classes by label, synonym, or OBO ID across all ontologies or a single one. Returns IRIs, labels, and definitions.' },
  { name: 'find_iri', sig: '(term, ontology?, limit?)', desc: 'Resolve a term — a label, exact synonym, CURIE/OBO id (GO:0006915), or candidate IRI — to its single canonical ontology IRI by exact match (not fuzzy search), and verify it exists. Use this before passing any IRI to the query tools: a wrong IRI silently returns zero results with no error.' },
  { name: 'run_dl_query', sig: '(query, type?, ontology?)', desc: 'Run a Description Logic query in Manchester OWL Syntax using real OWL reasoning. Find classes by logical relationships, not just text.' },
  { name: 'get_class_info', sig: '(class_iri, ontology)', desc: 'Full detail for one class: labels, definitions, synonyms, axioms, and relationships.' },
  { name: 'get_ontology_info', sig: '(ontology)', desc: 'Metadata for one ontology: title, description, version, counts, license, and classification status.' },
  { name: 'browse_hierarchy', sig: '(class_iri, ontology, direction?)', desc: 'Walk the class tree — direct subclasses or superclasses of a class (pass owl:Thing for the roots).' },
  { name: 'rewrite_sparql', sig: '(query)', desc: 'Rewrite a SPARQL query containing embedded OWL DL frames into plain SPARQL with concrete IRIs spliced in. AberOWL rewrites; you run it anywhere.' },
  { name: 'query_sparql', sig: '(query, endpoint?)', desc: 'Rewrite a SPARQL+OWL query AND execute it against an external endpoint (Ontobee by default; UniProt, Wikidata, …). Returns the result rows.' },
  { name: 'list_sparql_examples', sig: '()', desc: 'Curated SPARQL+OWL example queries to use as templates for the frame syntax.' },
]


// The REST API. Listed before MCP because this is what most callers use, and
// because its apparent absence from this page is what made the API look
// removed (biopragmatics/bioregistry#2030).
const REST: Array<{ method: string; path: string; desc: string; example: string }> = [
  { method: 'GET', path: '/api/listOntologies', desc: 'Every ontology served, with status and counts.',
    example: 'curl "%ORIGIN%/api/listOntologies"' },
  { method: 'GET', path: '/api/getOntology', desc: 'Metadata for one ontology.',
    example: 'curl "%ORIGIN%/api/getOntology?ontology=GO"' },
  { method: 'GET', path: '/api/search_all', desc: 'Full-text class search across the repository.',
    example: 'curl "%ORIGIN%/api/search_all?query=apoptosis"' },
  { method: 'GET', path: '/api/queryNames', desc: 'Class search by label, synonym or OBO id.',
    example: 'curl "%ORIGIN%/api/queryNames?term=cell&ontology=GO"' },
  { method: 'GET', path: '/api/getClass', desc: 'Full detail for one class.',
    example: 'curl "%ORIGIN%/api/getClass?ontology=GO&query=http://purl.obolibrary.org/obo/GO_0006915"' },
  { method: 'GET', path: '/api/dlquery_all', desc: 'Description Logic query across ontologies.',
    example: 'curl "%ORIGIN%/api/dlquery_all?query=\'part of\' some cell&type=subeq&ontologies=GO"' },
  { method: 'GET', path: '/api/resolve', desc: 'Resolve a term, CURIE or IRI to its canonical IRI.',
    example: 'curl "%ORIGIN%/api/resolve?query=apoptosis&ontologies=GO"' },
  { method: 'GET', path: '/api/sparql', desc: 'Rewrite OWL DL frames in a SPARQL query into concrete IRIs.',
    example: 'curl "%ORIGIN%/api/sparql?query=SELECT ?x WHERE { VALUES ?x { OWL subeq go-plus { \'cell death\' } } }"' },
  { method: 'GET', path: '/artefacts/…', desc: 'FAIR semantic-artefact records and distributions.',
    example: 'curl "%ORIGIN%/artefacts/go"' },
]

// AberOWL 1 paths are still served. Anything absent from this table works
// unchanged; these are the ones whose behaviour differs.
const MIGRATION: Array<{ v1: string; v2: string; note: string }> = [
  { v1: 'GET /api/ontology/', v2: 'still works', note: 'Also available as /api/listOntologies, with richer fields.' },
  { v1: 'GET /api/ontology/_find', v2: 'still works', note: 'Also /api/queryOntologies.' },
  { v1: 'GET /api/class/_find', v2: 'still works', note: 'Also /api/search_all.' },
  { v1: 'GET /api/class/_startwith', v2: 'still works', note: 'Also /api/queryNames with prefix=true.' },
  { v1: 'GET /api/dlquery', v2: 'still works', note: 'Also /api/dlquery_all across several ontologies.' },
  { v1: 'GET /api/ontology/{a}/root/{iri}', v2: 'still works', note: '' },
  { v1: 'GET /api/ontology/{a}/objectproperty', v2: 'still works', note: '' },
  { v1: 'POST /api/ontology/{a}/class/_matchsuperclasses', v2: 'still works', note: '' },
  { v1: 'GET /service/api/{script}', v2: 'still works', note: 'Read-only reasoner servlets only.' },
  { v1: 'GET /api/sparql', v2: 'still works', note: 'Rewrites the OWL frame and redirects to the endpoint named in the query, as before.' },
  { v1: 'GET /api/class/_similar', v2: 'removed (410)', note: 'Needed per-class embeddings AberOWL 2 does not compute. Use /api/class/_find.' },
  { v1: 'GET /api/dlquery/logs', v2: 'removed (410)', note: 'AberOWL 2 does not log DL queries.' },
]

const OPENAPI_URL = `${typeof window !== 'undefined' ? window.location.origin : ''}/openapi.json`
const SWAGGER_URL = `${typeof window !== 'undefined' ? window.location.origin : ''}/api/docs`

const cliSnippet = `claude mcp add --transport http aberowl ${MCP_URL}`

const jsonHttp = `{
  "mcpServers": {
    "aberowl": {
      "type": "http",
      "url": "${MCP_URL}"
    }
  }
}`

const jsonStdio = `{
  "mcpServers": {
    "aberowl": {
      "command": "npx",
      "args": ["mcp-remote", "${MCP_URL}"]
    }
  }
}`

// Self-hosting: a single-host instance publishes MCP directly on port 8766 (no
// nginx proxy), and uses a distinct server name so it doesn't collide with a
// connection to the hosted service. See deploy/SELF_HOSTING.md.
const SELFHOST_MCP_URL = 'http://localhost:8766/mcp'

const selfhostCli = `claude mcp add --transport http aberowl-local ${SELFHOST_MCP_URL}`

const selfhostJsonHttp = `{
  "mcpServers": {
    "aberowl-local": {
      "type": "http",
      "url": "${SELFHOST_MCP_URL}"
    }
  }
}`

const dlExample = `run_dl_query(
  query="'part of' some cell",
  type="subeq",
  ontology="GO"
)`

const sparqlExample = `query_sparql(query="""
  SELECT ?protein ?goClass WHERE {
    VALUES ?goClass { OWL subeq go-plus { 'cell death' } }
    ?protein a <http://purl.uniprot.org/core/Protein> ;
             <http://purl.uniprot.org/core/classifiedWith> ?goClass .
  } LIMIT 50
""", endpoint="https://sparql.uniprot.org/sparql")`

function Section({ id, title, children }: { id?: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-20">
      <h2 className="text-xl font-bold text-gray-900 mb-4">{title}</h2>
      {children}
    </section>
  )
}

export default function Docs() {
  useDocumentTitle('API Docs — REST and MCP')

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-12">
      {/* Hero */}
      <div className="text-center">
        <h1 className="text-3xl font-extrabold text-gray-900 mb-2 tracking-tight">AberOWL API</h1>
        <p className="text-gray-500 max-w-2xl mx-auto">
          OWL reasoning over <strong>900+ biomedical ontologies</strong>, available two ways: a
          <strong> REST API</strong> for programs, and <strong>MCP</strong> for AI agents. The
          AberOWL&nbsp;1 endpoints are still served, so existing clients keep working.
        </p>
        <div className="mt-4 flex items-center justify-center gap-3 flex-wrap text-sm">
          <a href={OPENAPI_URL} className="px-3 py-1.5 rounded-md border border-gray-300 hover:bg-gray-50 text-indigo-700 font-medium">
            openapi.json
          </a>
          <a href={SWAGGER_URL} className="px-3 py-1.5 rounded-md border border-gray-300 hover:bg-gray-50 text-indigo-700 font-medium">
            Interactive API docs
          </a>
        </div>
      </div>

      {/* REST API */}
      <Section id="rest" title="1. REST API">
        <p className="text-sm text-gray-500 mb-4">
          Plain HTTP, JSON responses, no key required. The full machine-readable
          specification is at{' '}
          <a href={OPENAPI_URL} className="text-indigo-600 hover:underline">/openapi.json</a>, and{' '}
          <a href={SWAGGER_URL} className="text-indigo-600 hover:underline">/api/docs</a> is an
          interactive browser for it.
        </p>
        <div className="border border-gray-200 rounded-xl overflow-hidden divide-y divide-gray-100">
          {REST.map(e => (
            <div key={e.path} className="p-4 hover:bg-gray-50/60">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-[10px] font-bold uppercase tracking-wider text-gray-400">{e.method}</span>
                <code className="font-mono text-sm text-indigo-700">{e.path}</code>
              </div>
              <p className="text-sm text-gray-600 mt-1">{e.desc}</p>
              <pre className="mt-2 bg-gray-50 border border-gray-200 rounded-md p-2 overflow-x-auto text-[11px] font-mono text-gray-700">
                <code>{e.example.replace('%ORIGIN%', typeof window !== 'undefined' ? window.location.origin : '')}</code>
              </pre>
            </div>
          ))}
        </div>
      </Section>

      {/* Migration */}
      <Section id="migration" title="2. Coming from AberOWL 1">
        <p className="text-sm text-gray-500 mb-4">
          The AberOWL&nbsp;1 paths are served at their original URLs, so existing clients need no
          change. Only the rows below behave differently from v1.
        </p>
        <div className="border border-gray-200 rounded-xl overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                <th className="text-left font-semibold px-4 py-2">AberOWL 1</th>
                <th className="text-left font-semibold px-4 py-2">In AberOWL 2</th>
                <th className="text-left font-semibold px-4 py-2">Note</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {MIGRATION.map(m => (
                <tr key={m.v1} className="align-top">
                  <td className="px-4 py-2 font-mono text-xs text-gray-700 whitespace-nowrap">{m.v1}</td>
                  <td className={`px-4 py-2 text-xs whitespace-nowrap ${m.v2.startsWith('removed') ? 'text-amber-700' : 'text-emerald-700'}`}>{m.v2}</td>
                  <td className="px-4 py-2 text-xs text-gray-500">{m.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {/* Endpoint card */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
        <div className="text-xs text-gray-500 uppercase tracking-wider mb-2">MCP Endpoint (streamable HTTP)</div>
        <div className="flex items-center gap-3 flex-wrap">
          <code className="text-sm md:text-base font-mono text-indigo-700 break-all">{MCP_URL}</code>
          <CopyButton text={MCP_URL} label="Copy URL" />
        </div>
        <p className="text-xs text-gray-400 mt-2">No API key required. Transport: streamable HTTP (MCP spec 2024-11-05+).</p>
      </div>

      {/* Quick connect */}
      <Section id="connect" title="3. Connect an AI agent (MCP)">
        {/* Hosted service */}
        <div className="mb-8">
          <h3 className="text-base font-bold text-gray-900 mb-1">Hosted AberOWL</h3>
          <p className="text-sm text-gray-500 mb-4">
            The public repository at <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">{MCP_URL}</code>. Nothing to install.
          </p>
          <div className="space-y-6">
            <div>
              <h4 className="text-sm font-semibold text-gray-700 mb-2">Claude Code (CLI)</h4>
              <CodeBlock code={cliSnippet} lang="bash" />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-gray-700 mb-2">
                Claude Desktop / any client with native HTTP MCP
              </h4>
              <p className="text-sm text-gray-500 mb-2">Add to your client's MCP config (e.g. <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">claude_desktop_config.json</code>):</p>
              <CodeBlock code={jsonHttp} lang="json" />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-gray-700 mb-2">stdio-only clients (via mcp-remote)</h4>
              <p className="text-sm text-gray-500 mb-2">For clients that don't speak HTTP transport yet, bridge with <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">mcp-remote</code>:</p>
              <CodeBlock code={jsonStdio} lang="json" />
            </div>
          </div>
        </div>

        {/* Self-hosting */}
        <div className="border-t border-gray-200 pt-6">
          <h3 className="text-base font-bold text-gray-900 mb-1">If you are self-hosting</h3>
          <p className="text-sm text-gray-500 mb-4">
            A single-host instance (see <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">deploy/SELF_HOSTING.md</code>)
            publishes MCP directly at <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">{SELFHOST_MCP_URL}</code> —
            no proxy, so the path is <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">/mcp</code>, not <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">/mcp/ontology/mcp</code>.
            It uses the name <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">aberowl-local</code> so it doesn't
            collide with a connection to the hosted service — keep both, or drop whichever you aren't using.
          </p>
          <div className="space-y-6">
            <div>
              <h4 className="text-sm font-semibold text-gray-700 mb-2">Claude Code (CLI)</h4>
              <CodeBlock code={selfhostCli} lang="bash" />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-gray-700 mb-2">Native HTTP MCP config</h4>
              <CodeBlock code={selfhostJsonHttp} lang="json" />
            </div>
          </div>
        </div>
      </Section>

      {/* Tools */}
      <Section id="tools" title="4. MCP tools">
        <p className="text-sm text-gray-500 mb-4">
          Once connected, your agent can call these tools. A typical flow is{' '}
          <span className="font-mono text-xs bg-gray-100 px-1 py-0.5 rounded">list_ontologies</span> →{' '}
          <span className="font-mono text-xs bg-gray-100 px-1 py-0.5 rounded">search_classes</span> →{' '}
          <span className="font-mono text-xs bg-gray-100 px-1 py-0.5 rounded">run_dl_query</span>. Always run{' '}
          <span className="font-mono text-xs bg-gray-100 px-1 py-0.5 rounded">find_iri</span> to confirm an IRI
          before you query it — a wrong IRI returns nothing, with no error.
        </p>
        <div className="border border-gray-200 rounded-xl overflow-hidden divide-y divide-gray-100">
          {TOOLS.map(t => (
            <div key={t.name} className="p-4 hover:bg-gray-50/60">
              <div className="font-mono text-sm text-indigo-700">
                {t.name}<span className="text-gray-400">{t.sig}</span>
              </div>
              <p className="text-sm text-gray-600 mt-1">{t.desc}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* DL queries */}
      <Section id="dl" title="5. Description Logic queries">
        <p className="text-sm text-gray-500 mb-3">
          The core capability: query classes by <strong>logical structure</strong> using OWL reasoning,
          expressed in <a className="text-indigo-600 hover:underline" href="https://www.w3.org/TR/owl2-manchester-syntax/" target="_blank" rel="noreferrer">Manchester OWL Syntax</a>.
        </p>
        <CodeBlock code={dlExample} lang="python" />
        <div className="grid md:grid-cols-2 gap-4 mt-4">
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <div className="text-xs font-semibold text-gray-700 uppercase tracking-wider mb-2">Syntax</div>
            <ul className="text-sm text-gray-600 space-y-1 font-mono">
              <li><span className="text-gray-400">label:</span> cell <span className="text-gray-400">(quote only if multi-word: 'cell death')</span></li>
              <li><span className="text-gray-400">IRI:</span> &lt;http://…/GO_0005623&gt;</li>
              <li><span className="text-gray-400">existential:</span> 'part of' some cell</li>
              <li><span className="text-gray-400">intersection:</span> cell and 'part of' some organism</li>
            </ul>
            <p className="text-xs text-gray-400 mt-2 font-sans">Not supported: <span className="font-mono">or</span>, <span className="font-mono">only</span>, <span className="font-mono">not</span> — AberOWL reasons in the OWL EL profile (ELK).</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <div className="text-xs font-semibold text-gray-700 uppercase tracking-wider mb-2">Query types</div>
            <ul className="text-sm text-gray-600 space-y-1">
              <li><span className="font-mono text-xs">subclass</span> — direct subclasses</li>
              <li><span className="font-mono text-xs">subeq</span> — subclasses + equivalent (most common)</li>
              <li><span className="font-mono text-xs">superclass</span> — direct superclasses</li>
              <li><span className="font-mono text-xs">supeq</span> — superclasses + equivalent</li>
              <li><span className="font-mono text-xs">equivalent</span> — equivalent classes only</li>
            </ul>
          </div>
        </div>
        <p className="text-sm text-gray-500 mt-4">
          Prefer a UI? Try the <Link to="/dlquery" className="text-indigo-600 hover:underline">DL Query</Link> page.
        </p>
      </Section>

      {/* SPARQL + OWL */}
      <Section id="sparql" title="6. Ontology-aware SPARQL">
        <p className="text-sm text-gray-500 mb-3">
          Embed OWL DL frames in a SPARQL query; AberOWL resolves them to concrete IRIs and (optionally)
          runs the query against any endpoint — UniProt, Wikidata, Ontobee, and more. AberOWL itself
          stores no triples; it only rewrites.
        </p>
        <CodeBlock code={sparqlExample} lang="python" />
        <p className="text-sm text-gray-500 mt-3">
          Call <span className="font-mono text-xs bg-gray-100 px-1 py-0.5 rounded">list_sparql_examples</span> for
          ready-made templates, or use the <Link to="/sparql" className="text-indigo-600 hover:underline">SPARQL + OWL</Link> page.
        </p>
      </Section>

      {/* Footer note */}
      <div className="bg-indigo-50 border border-indigo-100 rounded-xl p-5 text-sm text-gray-600">
        <strong className="text-gray-800">Building something?</strong> AberOWL is developed by the{' '}
        <a href="https://cemse.kaust.edu.sa/borg" target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline">Bio-Ontology Research Group</a> at KAUST.
        The MCP server is open and unauthenticated for now — please be considerate with query volume.
      </div>
    </div>
  )
}
