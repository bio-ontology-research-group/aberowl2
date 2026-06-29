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

const dlExample = `run_dl_query(
  query="'part of' some 'cell'",
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
  useDocumentTitle('Docs — MCP for Agents')

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-12">
      {/* Hero */}
      <div className="text-center">
        <span className="inline-block text-xs font-semibold uppercase tracking-wider text-indigo-600 bg-indigo-50 px-3 py-1 rounded-full mb-3">
          For AI Agents
        </span>
        <h1 className="text-3xl font-extrabold text-gray-900 mb-2 tracking-tight">Connect to AberOWL over MCP</h1>
        <p className="text-gray-500 max-w-2xl mx-auto">
          AberOWL exposes OWL reasoning over <strong>900+ biomedical ontologies</strong> through the
          Model Context Protocol. Point your agent at one endpoint and it can search classes, run
          Description&nbsp;Logic queries, browse hierarchies, and build ontology-aware SPARQL.
        </p>
      </div>

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
      <Section id="connect" title="1. Connect your agent">
        <div className="space-y-6">
          <div>
            <h3 className="text-sm font-semibold text-gray-700 mb-2">Claude Code (CLI)</h3>
            <CodeBlock code={cliSnippet} lang="bash" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-gray-700 mb-2">
              Claude Desktop / any client with native HTTP MCP
            </h3>
            <p className="text-sm text-gray-500 mb-2">Add to your client's MCP config (e.g. <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">claude_desktop_config.json</code>):</p>
            <CodeBlock code={jsonHttp} lang="json" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-gray-700 mb-2">stdio-only clients (via mcp-remote)</h3>
            <p className="text-sm text-gray-500 mb-2">For clients that don't speak HTTP transport yet, bridge with <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">mcp-remote</code>:</p>
            <CodeBlock code={jsonStdio} lang="json" />
          </div>
        </div>
      </Section>

      {/* Tools */}
      <Section id="tools" title="2. Available tools">
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
      <Section id="dl" title="3. Description Logic queries">
        <p className="text-sm text-gray-500 mb-3">
          The core capability: query classes by <strong>logical structure</strong> using OWL reasoning,
          expressed in <a className="text-indigo-600 hover:underline" href="https://www.w3.org/TR/owl2-manchester-syntax/" target="_blank" rel="noreferrer">Manchester OWL Syntax</a>.
        </p>
        <CodeBlock code={dlExample} lang="python" />
        <div className="grid md:grid-cols-2 gap-4 mt-4">
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <div className="text-xs font-semibold text-gray-700 uppercase tracking-wider mb-2">Syntax</div>
            <ul className="text-sm text-gray-600 space-y-1 font-mono">
              <li><span className="text-gray-400">label:</span> 'cell'</li>
              <li><span className="text-gray-400">IRI:</span> &lt;http://…/GO_0005623&gt;</li>
              <li><span className="text-gray-400">existential:</span> 'part of' some 'cell'</li>
              <li><span className="text-gray-400">intersection:</span> 'cell' and 'part of' some 'organism'</li>
              <li><span className="text-gray-400">union:</span> 'cell' or 'tissue'</li>
              <li><span className="text-gray-400">negation:</span> not 'cell'</li>
            </ul>
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
      <Section id="sparql" title="4. Ontology-aware SPARQL">
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
