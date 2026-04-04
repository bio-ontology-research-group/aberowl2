import { useState } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { sql } from '@codemirror/lang-sql'
import { oneDark } from '@codemirror/theme-one-dark'
import { runSparql } from '../api/client'

const EXAMPLE_VALUES = `SELECT ?class ?label WHERE {
  VALUES ?class { OWL subeq GO { 'part of' some 'cell' } }
  ?class <http://www.w3.org/2000/01/rdf-schema#label> ?label .
}`

const EXAMPLE_FILTER = `SELECT ?class ?label WHERE {
  ?class <http://www.w3.org/2000/01/rdf-schema#label> ?label .
  FILTER OWL(?class, subeq, GO, "'part of' some 'cell'")
}`

const EXAMPLE_PLAIN = `SELECT ?s ?p ?o WHERE {
  GRAPH <http://aberowl.net/ontology/go> {
    ?s ?p ?o .
  }
} LIMIT 20`

export default function SparqlPage() {
  const [query, setQuery] = useState(EXAMPLE_VALUES)
  const [results, setResults] = useState<Record<string, unknown> | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [expansions, setExpansions] = useState<unknown[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function run() {
    setLoading(true)
    setError('')
    setResults(null)
    setExpanded(null)
    setExpansions(null)
    try {
      const data = await runSparql(query) as Record<string, unknown>
      if ((data.results as Record<string, unknown>)?.error) {
        setError(String((data.results as Record<string, unknown>).error))
      } else {
        setResults(data.results as Record<string, unknown>)
      }
      if (data.expanded_query) setExpanded(data.expanded_query as string)
      if (data.expansions) setExpansions(data.expansions as unknown[])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Query failed')
    }
    setLoading(false)
  }

  const bindings = (results as Record<string, Record<string, unknown>>)?.results as Record<string, unknown>
  const vars = ((results as Record<string, Record<string, unknown>>)?.head as Record<string, string[]>)?.vars || []
  const rows = (bindings?.bindings || []) as Record<string, { value: string }>[]

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <h1 className="text-2xl font-bold text-gray-900 mb-1">SPARQL + OWL Expansion</h1>
      <p className="text-sm text-gray-500 mb-4">
        Execute SPARQL queries with embedded OWL Description Logic expansion
      </p>

      {/* Examples */}
      <div className="flex gap-2 mb-3 text-xs">
        <span className="text-gray-500">Examples:</span>
        <button onClick={() => setQuery(EXAMPLE_VALUES)} className="text-indigo-600 hover:underline">VALUES pattern</button>
        <button onClick={() => setQuery(EXAMPLE_FILTER)} className="text-indigo-600 hover:underline">FILTER pattern</button>
        <button onClick={() => setQuery(EXAMPLE_PLAIN)} className="text-indigo-600 hover:underline">Plain SPARQL</button>
      </div>

      {/* Editor */}
      <div className="border border-gray-300 rounded-lg overflow-hidden mb-3">
        <CodeMirror
          value={query}
          onChange={setQuery}
          height="240px"
          extensions={[sql()]}
          theme={oneDark}
        />
      </div>

      <button onClick={run} disabled={loading}
        className="px-5 py-2 bg-indigo-600 text-white rounded text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 mb-4">
        {loading ? 'Executing...' : 'Execute Query'}
      </button>

      {error && <div className="text-red-500 text-sm mb-4 bg-red-50 p-3 rounded">{error}</div>}

      {/* Expansion info */}
      {expansions && expansions.length > 0 && (
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-3 mb-4 text-sm">
          <strong className="text-indigo-700">OWL Expansions Applied:</strong>
          <ul className="mt-1 space-y-0.5">
            {(expansions as Record<string, unknown>[]).map((exp, i) => (
              <li key={i} className="text-indigo-600">
                {String(exp.pattern)} {String(exp.variable)}: {String(exp.type)} on {String(exp.ontology)} &rarr; {String(exp.result_count)} classes
              </li>
            ))}
          </ul>
        </div>
      )}

      {expanded && (
        <details className="mb-4">
          <summary className="text-sm text-gray-500 cursor-pointer hover:text-gray-700">Show expanded query</summary>
          <pre className="mt-2 bg-gray-900 text-gray-100 p-3 rounded text-xs overflow-x-auto">{expanded}</pre>
        </details>
      )}

      {/* Results table */}
      {rows.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
          <div className="text-xs text-gray-500 p-2 border-b">{rows.length} results</div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
              <tr>
                {vars.map(v => <th key={v} className="px-3 py-2 text-left">{v}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.slice(0, 500).map((row, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  {vars.map(v => (
                    <td key={v} className="px-3 py-1.5 text-gray-700 truncate max-w-xs">
                      {row[v]?.value || ''}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > 500 && (
            <div className="text-xs text-gray-400 p-2 text-center">Showing 500 of {rows.length}</div>
          )}
        </div>
      )}

      {results && rows.length === 0 && !error && (
        <p className="text-sm text-gray-500">Query returned no results.</p>
      )}

      {/* Syntax reference */}
      <div className="mt-8 bg-gray-50 border border-gray-200 rounded-lg p-4 text-sm text-gray-600">
        <h3 className="font-semibold text-gray-800 mb-2">OWL Expansion Syntax Reference</h3>
        <div className="grid md:grid-cols-2 gap-4">
          <div>
            <h4 className="font-medium text-gray-700">VALUES pattern</h4>
            <code className="text-xs block mt-1 bg-white p-2 rounded border">
              VALUES ?var {'{'} OWL type ONTOLOGY {'{'} dl_query {'}'} {'}'}
            </code>
            <p className="text-xs mt-1">Binds ?var to the IRIs returned by the DL query</p>
          </div>
          <div>
            <h4 className="font-medium text-gray-700">FILTER pattern</h4>
            <code className="text-xs block mt-1 bg-white p-2 rounded border">
              FILTER OWL(?var, type, ONTOLOGY, "dl_query")
            </code>
            <p className="text-xs mt-1">Restricts ?var to IRIs matching the DL query</p>
          </div>
        </div>
        <p className="text-xs mt-3">
          <strong>Types:</strong> subclass, subeq, superclass, supeq, equivalent<br />
          <strong>DL query:</strong> Manchester OWL Syntax (e.g. <code>'part of' some 'cell'</code>)
        </p>
      </div>
    </div>
  )
}
