import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { searchClasses } from '../api/client'
import type { ClassResult } from '../api/types'
import ClassCard from '../components/ClassCard'
import StateMessage from '../components/StateMessage'
import { useDocumentTitle } from '../hooks/useDocumentTitle'

export default function SearchResults() {
  const [params] = useSearchParams()
  const q = params.get('q') || ''
  const [results, setResults] = useState<ClassResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useDocumentTitle(q ? `Search: ${q}` : 'Search')

  const load = useCallback(() => {
    if (!q) return
    setLoading(true)
    setError('')
    setExpanded(new Set())
    searchClasses(q, undefined, 200)
      .then(setResults)
      .catch(e => { setError(e instanceof Error ? e.message : 'Search failed'); setResults([]) })
      .finally(() => setLoading(false))
  }, [q])

  // eslint-disable-next-line react-hooks/set-state-in-effect -- load() toggles loading before fetching; intentional
  useEffect(() => { load() }, [load])

  // Group by ontology
  const byOnt = new Map<string, ClassResult[]>()
  for (const r of results) {
    const ont = r.ontology || '?'
    if (!byOnt.has(ont)) byOnt.set(ont, [])
    byOnt.get(ont)!.push(r)
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <h1 className="text-xl font-semibold text-gray-900 mb-1">
        Search results for &ldquo;{q}&rdquo;
      </h1>
      <p className="text-sm text-gray-500 mb-4">
        {loading ? 'Searching...' : `${results.length} results across ${byOnt.size} ontologies`}
      </p>

      {error && <StateMessage kind="error" title="Search failed" detail={error} onRetry={load} />}

      {!loading && !error && results.length === 0 && q && (
        <StateMessage title="No results found" detail={`Nothing matched "${q}".`} />
      )}

      {[...byOnt.entries()].map(([ont, items]) => {
        const isOpen = expanded.has(ont)
        const visible = isOpen ? items : items.slice(0, 20)
        return (
          <div key={ont} className="mb-6">
            <h2 className="text-sm font-semibold text-gray-700 mb-2 uppercase">{ont} ({items.length})</h2>
            <div className="grid gap-2 md:grid-cols-2">
              {visible.map((c, i) => <ClassCard key={c.class || i} c={c} />)}
            </div>
            {items.length > 20 && (
              <button
                onClick={() => setExpanded(prev => {
                  const next = new Set(prev)
                  if (next.has(ont)) next.delete(ont); else next.add(ont)
                  return next
                })}
                className="text-xs text-indigo-600 hover:underline mt-1"
              >
                {isOpen ? 'Show fewer' : `Show all ${items.length} in ${ont}`}
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
