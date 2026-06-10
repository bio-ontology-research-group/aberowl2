import { useState } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { sql } from '@codemirror/lang-sql'
import { oneDark } from '@codemirror/theme-one-dark'
import { rewriteSparql } from '../api/client'

const KNOWN_ENDPOINTS = [
  { label: 'Ontobee', value: 'https://sparql.hegroup.org/sparql' },
  { label: 'UniProt', value: 'https://sparql.uniprot.org/sparql' },
  { label: 'Wikidata', value: 'https://query.wikidata.org/sparql' },
  { label: 'DBpedia', value: 'https://dbpedia.org/sparql' },
  { label: 'Bio2RDF', value: 'https://bio2rdf.org/sparql' },
]

const EXAMPLE_VALUES = `SELECT ?class ?label WHERE {
  VALUES ?class { OWL subeq go-plus { 'cell death' } }
  ?class <http://www.w3.org/2000/01/rdf-schema#label> ?label .
}`

const EXAMPLE_FILTER = `SELECT ?class ?label WHERE {
  ?class <http://www.w3.org/2000/01/rdf-schema#label> ?label .
  FILTER OWL(?class, subeq, go-plus, "'cell death'")
}`

const EXAMPLE_UNIPROT = `# Run this against https://sparql.uniprot.org/sparql after rewriting.
# Find UniProt proteins classified under GO subclasses of 'cell death'.
SELECT ?protein ?proteinLabel ?goClass ?goLabel WHERE {
  VALUES ?goClass { OWL subeq go-plus { 'cell death' } }
  ?protein a <http://purl.uniprot.org/core/Protein> ;
           <http://purl.uniprot.org/core/classifiedWith> ?goClass ;
           <http://purl.uniprot.org/core/mnemonic> ?proteinLabel .
  ?goClass <http://www.w3.org/2000/01/rdf-schema#label> ?goLabel .
} LIMIT 50`

interface Expansion {
  pattern: string
  variable: string
  ontology: string
  type: string
  dl_query: string
  result_count: number
}

interface FrameError {
  pattern: string
  variable: string
  ontology: string
  type: string
  dl_query: string
  error: string
}

interface SparqlResults {
  vars: string[]
  bindings: Array<Record<string, { value: string; type: string }>>
}

export default function SparqlPage() {
  const [query, setQuery] = useState(EXAMPLE_VALUES)
  const [rewritten, setRewritten] = useState<string>('')
  const [expansions, setExpansions] = useState<Expansion[]>([])
  const [errors, setErrors] = useState<FrameError[]>([])
  const [loading, setLoading] = useState(false)
  const [requestError, setRequestError] = useState('')
  const [copied, setCopied] = useState(false)
  const [selectedEndpoint, setSelectedEndpoint] = useState(KNOWN_ENDPOINTS[0].value)
  const [executing, setExecuting] = useState(false)
  const [results, setResults] = useState<SparqlResults | null>(null)
  const [executeError, setExecuteError] = useState('')

  async function rewrite(): Promise<string | null> {
    setLoading(true)
    setRequestError('')
    setRewritten('')
    setExpansions([])
    setErrors([])
    setCopied(false)
    setResults(null)
    setExecuteError('')
    try {
      const data = await rewriteSparql(query) as {
        rewritten_query?: string
        expansions?: Expansion[]
        errors?: FrameError[]
      }
      const text = data.rewritten_query || ''
      setRewritten(text)
      setExpansions(data.expansions || [])
      setErrors(data.errors || [])
      return text
    } catch (e) {
      setRequestError(e instanceof Error ? e.message : 'Rewrite failed')
      return null
    } finally {
      setLoading(false)
    }
  }

  async function rewriteAndExecute() {
    const text = await rewrite()
    if (text) await executeRewritten(text)
  }

  async function copyRewritten() {
    if (!rewritten) return
    await navigator.clipboard.writeText(rewritten)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  function endpointLink(endpoint: string) {
    return `${endpoint}?query=${encodeURIComponent(rewritten)}`
  }

  async function executeRewritten(text?: string) {
    const queryText = text ?? rewritten
    if (!queryText) return
    setExecuting(true)
    setResults(null)
    setExecuteError('')
    try {
      const url = new URL(selectedEndpoint)
      url.searchParams.set('query', queryText)
      url.searchParams.set('format', 'json')
      const resp = await fetch(url.toString(), {
        headers: { Accept: 'application/sparql-results+json' },
      })
      if (!resp.ok) {
        const body = await resp.text().catch(() => '')
        throw new Error(`HTTP ${resp.status} ${resp.statusText}${body ? `: ${body.slice(0, 200)}` : ''}`)
      }
      const json = await resp.json()
      setResults({
        vars: json.head?.vars ?? [],
        bindings: json.results?.bindings ?? [],
      })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Execute failed'
      const isCors = msg === 'Failed to fetch' || msg.includes('NetworkError')
      setExecuteError(
        isCors
          ? 'Could not reach the endpoint from your browser (likely CORS). Use "Open in" to run it in the endpoint’s own UI.'
          : msg,
      )
    } finally {
      setExecuting(false)
    }
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <h1 className="text-2xl font-bold text-gray-900 mb-1">SPARQL Rewriter</h1>
      <p className="text-sm text-gray-500 mb-4">
        Write SPARQL with embedded OWL DL frames. AberOWL resolves the frames against its
        reasoners and gives you back plain SPARQL with concrete IRIs spliced in. You can then
        execute the rewritten query against any endpoint you choose — AberOWL never executes it
        (your browser does, directly to the selected endpoint).
      </p>

      {/* Examples */}
      <div className="flex gap-2 mb-3 text-xs flex-wrap">
        <span className="text-gray-500">Examples:</span>
        <button onClick={() => setQuery(EXAMPLE_VALUES)} className="text-indigo-600 hover:underline">VALUES pattern</button>
        <button onClick={() => setQuery(EXAMPLE_FILTER)} className="text-indigo-600 hover:underline">FILTER pattern</button>
        <button onClick={() => setQuery(EXAMPLE_UNIPROT)} className="text-indigo-600 hover:underline">UniProt federation</button>
      </div>

      {/* Editor */}
      <div className="border border-gray-300 rounded-xl overflow-hidden mb-3 shadow-sm">
        <CodeMirror
          value={query}
          onChange={setQuery}
          height="260px"
          extensions={[sql()]}
          theme={oneDark}
        />
      </div>

      {/* Action bar */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <button
          onClick={rewriteAndExecute}
          disabled={loading || executing}
          className="px-6 py-2.5 bg-indigo-600 text-white rounded-lg text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50 transition-colors"
        >
          {loading ? 'Rewriting…' : executing ? 'Executing…' : 'Rewrite & Execute'}
        </button>
        <button
          onClick={rewrite}
          disabled={loading || executing}
          className="px-4 py-2.5 bg-white border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:border-indigo-400 hover:text-indigo-700 disabled:opacity-50 transition-colors"
        >
          Rewrite only
        </button>
        <span className="text-xs text-gray-500 ml-2">Endpoint:</span>
        <select
          value={selectedEndpoint}
          onChange={e => setSelectedEndpoint(e.target.value)}
          className="border border-gray-300 rounded-lg px-2 py-1 text-sm bg-white text-gray-800 hover:border-indigo-400 focus:border-indigo-500 focus:outline-none"
        >
          {KNOWN_ENDPOINTS.map(ep => (
            <option key={ep.value} value={ep.value}>{ep.label}</option>
          ))}
        </select>
      </div>

      {requestError && (
        <div className="text-red-600 text-sm mb-4 bg-red-50 p-3 rounded-lg border border-red-200">
          {requestError}
        </div>
      )}

      {/* Per-frame errors */}
      {errors.length > 0 && (
        <div className="bg-amber-50 border border-amber-300 rounded-lg p-3 mb-4 text-sm">
          <strong className="text-amber-800">Frames that could not be resolved:</strong>
          <ul className="mt-1 space-y-0.5">
            {errors.map((err, i) => (
              <li key={i} className="text-amber-800">
                {err.pattern} {err.variable} ({err.type} on {err.ontology}): {err.error}
              </li>
            ))}
          </ul>
          <p className="text-xs text-amber-700 mt-2">
            These frames were replaced with an empty match in the rewritten query.
          </p>
        </div>
      )}

      {/* Successful expansions */}
      {expansions.length > 0 && (
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-3 mb-4 text-sm">
          <strong className="text-indigo-700">OWL frames resolved:</strong>
          <ul className="mt-1 space-y-0.5">
            {expansions.map((exp, i) => (
              <li key={i} className="text-indigo-600">
                {exp.pattern} {exp.variable}: {exp.type} on {exp.ontology} &rarr; {exp.result_count} classes
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Rewritten query */}
      {rewritten && (
        <div className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-gray-700">Rewritten query</h2>
            <button onClick={copyRewritten}
              className="text-xs px-3 py-1 rounded border border-gray-300 hover:border-indigo-400 hover:text-indigo-600">
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
          <pre className="bg-gray-900 text-gray-100 p-3 rounded-lg text-xs overflow-x-auto whitespace-pre-wrap">{rewritten}</pre>

          <div className="mt-3 flex gap-2 items-center flex-wrap text-xs">
            <button
              onClick={() => executeRewritten()}
              disabled={executing}
              className="px-3 py-1 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 transition-colors"
            >
              {executing ? 'Executing…' : `Execute on ${KNOWN_ENDPOINTS.find(e => e.value === selectedEndpoint)?.label ?? 'endpoint'}`}
            </button>
            <span className="self-center text-gray-500 ml-2">or open in:</span>
            {KNOWN_ENDPOINTS.map(ep => (
              <a key={ep.value} href={endpointLink(ep.value)} target="_blank" rel="noreferrer"
                className="px-3 py-1 rounded-lg border border-gray-300 bg-white text-gray-700 hover:border-indigo-400 hover:text-indigo-600">
                {ep.label}
              </a>
            ))}
          </div>

          {executeError && (
            <div className="mt-3 text-red-700 text-sm bg-red-50 p-3 rounded-lg border border-red-200">
              {executeError}
            </div>
          )}

          {results && (
            <div className="mt-3">
              <h3 className="text-sm font-semibold text-gray-700 mb-2">
                Results — {results.bindings.length} {results.bindings.length === 1 ? 'row' : 'rows'}
              </h3>
              {results.bindings.length === 0 ? (
                <div className="text-sm text-gray-500 bg-gray-50 p-3 rounded-lg border border-gray-200">
                  No bindings returned.
                </div>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-gray-200">
                  <table className="min-w-full text-xs">
                    <thead className="bg-gray-50">
                      <tr>
                        {results.vars.map(v => (
                          <th key={v} className="text-left px-3 py-2 font-medium text-gray-700 border-b border-gray-200">{v}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {results.bindings.map((b, i) => (
                        <tr key={i} className={i % 2 ? 'bg-white' : 'bg-gray-50/40'}>
                          {results.vars.map(v => {
                            const cell = b[v]
                            if (!cell) return <td key={v} className="px-3 py-2 text-gray-400 align-top">—</td>
                            const isUri = cell.type === 'uri'
                            return (
                              <td key={v} className="px-3 py-2 align-top text-gray-800 break-all">
                                {isUri ? (
                                  <a href={cell.value} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline">
                                    {cell.value}
                                  </a>
                                ) : (
                                  cell.value
                                )}
                              </td>
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Syntax reference */}
      <div className="mt-8 bg-gray-50 border border-gray-200 rounded-xl p-4 text-sm text-gray-600">
        <h3 className="font-semibold text-gray-800 mb-2">OWL frame syntax</h3>
        <div className="grid md:grid-cols-2 gap-4">
          <div>
            <h4 className="font-medium text-gray-700">VALUES pattern</h4>
            <code className="text-xs block mt-1 bg-white p-2 rounded border font-mono">
              VALUES ?var {'{'} OWL type ONTOLOGY {'{'} dl_query {'}'} {'}'}
            </code>
          </div>
          <div>
            <h4 className="font-medium text-gray-700">FILTER pattern</h4>
            <code className="text-xs block mt-1 bg-white p-2 rounded border font-mono">
              FILTER OWL(?var, type, ONTOLOGY, "dl_query")
            </code>
          </div>
        </div>
        <p className="text-xs mt-3">
          <strong>Types:</strong> subclass, subeq, superclass, supeq, equivalent<br />
          <strong>Ontology id:</strong> a registered AberOWL ontology id (case-insensitive; <code>go-plus</code>, <code>chebi</code>, …)<br />
          <strong>DL query:</strong> Manchester OWL Syntax (e.g. <code className="font-mono">'part of' some 'cell'</code>)
        </p>
      </div>
    </div>
  )
}
