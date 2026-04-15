import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { searchClasses } from '../api/client'
import type { ClassResult } from '../api/types'
import ClassCard from '../components/ClassCard'

export default function SearchResults() {
  const [params] = useSearchParams()
  const q = params.get('q') || ''
  const [results, setResults] = useState<ClassResult[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!q) return
    setLoading(true)
    searchClasses(q, undefined, 200)
      .then(setResults)
      .catch(() => setResults([]))
      .finally(() => setLoading(false))
  }, [q])

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

      {!loading && results.length === 0 && q && (
        <p className="text-gray-500">No results found.</p>
      )}

      {[...byOnt.entries()].map(([ont, items]) => (
        <div key={ont} className="mb-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-2 uppercase">{ont} ({items.length})</h2>
          <div className="grid gap-2 md:grid-cols-2">
            {items.slice(0, 20).map((c, i) => (
              <ClassCard key={c.class || i} c={c} />
            ))}
          </div>
          {items.length > 20 && (
            <p className="text-xs text-gray-400 mt-1">...and {items.length - 20} more in {ont}</p>
          )}
        </div>
      ))}
    </div>
  )
}
