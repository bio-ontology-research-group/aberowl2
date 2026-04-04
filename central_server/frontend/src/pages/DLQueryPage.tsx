import { useState, useEffect } from 'react'
import { dlQuery, listOntologies } from '../api/client'
import type { ClassResult, OntologySummary } from '../api/types'
import ClassCard from '../components/ClassCard'

export default function DLQueryPage() {
  const [query, setQuery] = useState('')
  const [type, setType] = useState('subeq')
  const [ontology, setOntology] = useState('')
  const [results, setResults] = useState<ClassResult[]>([])
  const [time, setTime] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [ontologies, setOntologies] = useState<OntologySummary[]>([])

  useEffect(() => {
    listOntologies().then(o => setOntologies(o.filter(x => x.status === 'online'))).catch(() => {})
  }, [])

  function run(e: React.FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError('')
    dlQuery(query, type, ontology || undefined)
      .then(d => { setResults(d.result || []); setTime(d.time) })
      .catch(err => { setError(err.message); setResults([]) })
      .finally(() => setLoading(false))
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <h1 className="text-2xl font-bold text-gray-900 mb-1">DL Query</h1>
      <p className="text-sm text-gray-500 mb-4">
        Query ontologies using Manchester OWL Syntax with OWL DL reasoning
      </p>

      <form onSubmit={run} className="bg-white border border-gray-200 rounded-lg p-4 mb-6">
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Manchester OWL Syntax Query</label>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="e.g. 'part of' some 'cell'"
            className="w-full px-3 py-2 border border-gray-300 rounded text-sm font-mono focus:ring-2 focus:ring-indigo-400 focus:outline-none"
          />
        </div>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Query type</label>
            <select value={type} onChange={e => setType(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded text-sm">
              <option value="subeq">Subclass + Equivalent</option>
              <option value="subclass">Subclass</option>
              <option value="superclass">Superclass</option>
              <option value="supeq">Superclass + Equivalent</option>
              <option value="equivalent">Equivalent</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Ontology (optional)</label>
            <select value={ontology} onChange={e => setOntology(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded text-sm">
              <option value="">All ontologies</option>
              {ontologies.map(o => (
                <option key={o.id} value={o.id}>{o.id.toUpperCase()} - {o.title}</option>
              ))}
            </select>
          </div>
          <button type="submit" disabled={loading}
            className="px-5 py-2 bg-indigo-600 text-white rounded text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
            {loading ? 'Running...' : 'Run Query'}
          </button>
        </div>

        <div className="mt-4 p-3 bg-gray-50 rounded text-xs text-gray-500">
          <strong>Syntax guide:</strong> Named classes in single quotes (<code>'cell'</code>),
          existential restriction (<code>'part of' some 'cell'</code>),
          intersection (<code>'cell' and 'part of' some 'organism'</code>),
          IRI (<code>&lt;http://purl.obolibrary.org/obo/GO_0005623&gt;</code>)
        </div>
      </form>

      {error && <p className="text-red-500 text-sm mb-4">{error}</p>}

      {time !== null && (
        <p className="text-sm text-gray-500 mb-3">{results.length} results in {time} ms</p>
      )}

      <div className="grid gap-2 md:grid-cols-2">
        {results.map((c, i) => (
          <ClassCard key={c.class || i} c={c} />
        ))}
      </div>
    </div>
  )
}
