import { Link } from 'react-router-dom'
import type { ClassResult } from '../api/types'

function label(v: string | string[] | undefined): string {
  if (!v) return '?'
  return Array.isArray(v) ? v[0] || '?' : v
}

export default function ClassCard({ c }: { c: ClassResult }) {
  const ont = c.ontology || '?'
  const lbl = label(c.label)
  const def = label(c.definition)
  const iri = c.class || ''

  return (
    <div className="border border-gray-200 rounded-lg p-3 bg-white hover:shadow transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <Link
          to={`/ontology/${ont}/class/${encodeURIComponent(iri)}`}
          className="font-medium text-indigo-700 hover:underline text-sm"
        >
          {lbl}
        </Link>
        <Link to={`/ontology/${ont}`} className="text-xs bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded shrink-0">
          {ont.toUpperCase()}
        </Link>
      </div>
      {def && def !== '?' && (
        <p className="text-xs text-gray-500 mt-1 line-clamp-2">{def}</p>
      )}
      <p className="text-[10px] text-gray-400 mt-1 truncate">{iri}</p>
    </div>
  )
}
