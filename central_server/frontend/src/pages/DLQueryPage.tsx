import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { dlQuery, listOntologies } from '../api/client'
import type { ClassResult, OntologySummary } from '../api/types'
import OWLExpression from '../components/OWLExpression'

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
      <p className="text-sm text-gray-500 mb-5">
        Query ontologies using Manchester OWL Syntax with OWL DL reasoning
      </p>

      <form onSubmit={run} className="bg-white border border-gray-200 rounded-xl p-5 mb-6 shadow-sm">
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Manchester OWL Syntax Query</label>
          <textarea
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="e.g. 'part of' some 'cell'"
            rows={3}
            className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-400 focus:border-indigo-400 focus:outline-none resize-none"
          />
          {/* Live preview */}
          {query.trim() && (
            <div className="mt-2 px-3 py-2 bg-gray-50 rounded-lg border border-gray-100">
              <span className="text-[10px] text-gray-400 uppercase tracking-wider">Preview: </span>
              <OWLExpression expression={query} />
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Query type</label>
            <select value={type} onChange={e => setType(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white">
              <option value="subeq">Subclass + Equivalent</option>
              <option value="subclass">Subclass</option>
              <option value="superclass">Superclass</option>
              <option value="supeq">Superclass + Equivalent</option>
              <option value="equivalent">Equivalent</option>
            </select>
          </div>
          <div className="min-w-[200px]">
            <label className="block text-xs text-gray-500 mb-1">Ontology (optional)</label>
            <select value={ontology} onChange={e => setOntology(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white w-full">
              <option value="">All ontologies</option>
              {ontologies.map(o => (
                <option key={o.id} value={o.id}>{o.id.toUpperCase()}{o.title ? ` — ${o.title}` : ''}</option>
              ))}
            </select>
          </div>
          <button type="submit" disabled={loading}
            className="px-6 py-2 bg-indigo-600 text-white rounded-lg text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50 transition-colors">
            {loading ? 'Running...' : 'Run Query'}
          </button>
        </div>

        <div className="mt-4 p-3 bg-gray-50 rounded-lg text-xs text-gray-500 border border-gray-100">
          <strong className="text-gray-700">Syntax guide:</strong>{' '}
          Named classes: <code className="bg-white px-1 rounded border text-emerald-700">'cell'</code>{' · '}
          Existential: <code className="bg-white px-1 rounded border"><span className="text-emerald-700">'part of'</span> <span className="text-amber-600 font-bold">some</span> <span className="text-emerald-700">'cell'</span></code>{' · '}
          Intersection: <code className="bg-white px-1 rounded border"><span className="text-amber-600 font-bold">and</span></code>{' · '}
          Union: <code className="bg-white px-1 rounded border"><span className="text-amber-600 font-bold">or</span></code>{' · '}
          Universal: <code className="bg-white px-1 rounded border"><span className="text-amber-600 font-bold">only</span></code>{' · '}
          IRI: <code className="bg-white px-1 rounded border text-blue-600">&lt;http://...&gt;</code>
        </div>
      </form>

      {error && <div className="text-red-600 text-sm mb-4 bg-red-50 p-3 rounded-lg border border-red-200">{error}</div>}

      {time !== null && (
        <p className="text-sm text-gray-500 mb-3">
          <span className="font-semibold text-gray-700">{results.length}</span> results in{' '}
          <span className="font-semibold text-gray-700">{time}</span> ms
        </p>
      )}

      {results.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden divide-y divide-gray-100">
          {results.map((c, i) => {
            const lbl = Array.isArray(c.label) ? c.label[0] : c.label
            const ont = c.ontology || '?'
            return (
              <div key={c.class || i} className="px-4 py-3 hover:bg-indigo-50/30 transition-colors flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <Link to={`/ontology/${ont}/class/${encodeURIComponent(c.class)}`}
                    className="text-sm text-indigo-600 hover:underline font-medium">
                    {lbl}
                  </Link>
                  <p className="text-xs text-gray-400 font-mono truncate mt-0.5">{c.class}</p>
                </div>
                <Link to={`/ontology/${ont}`}
                  className="text-[10px] bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded font-medium shrink-0 uppercase">
                  {ont}
                </Link>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
