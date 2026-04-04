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
    <div className="border border-gray-200 rounded-lg p-3.5 bg-white hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2 mb-1">
        <Link
          to={`/ontology/${ont}/class/${encodeURIComponent(iri)}`}
          className="font-medium text-indigo-700 hover:underline text-sm leading-tight"
        >
          {lbl}
        </Link>
        <Link to={`/ontology/${ont}`}
          className="text-[10px] bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded font-medium shrink-0 uppercase">
          {ont}
        </Link>
      </div>
      {def && def !== '?' && (
        <p className="text-xs text-gray-500 mt-1 line-clamp-2 leading-relaxed">{def}</p>
      )}
      <p className="text-[10px] text-gray-400 mt-1.5 truncate font-mono">{iri}</p>
    </div>
  )
}
