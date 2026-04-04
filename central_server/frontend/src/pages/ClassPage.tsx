import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getClass, dlQuery } from '../api/client'
import type { ClassResult } from '../api/types'
import OWLExpression from '../components/OWLExpression'

function asList(v: unknown): string[] {
  if (!v) return []
  if (Array.isArray(v)) return v
  return [String(v)]
}

/** Strip HTML tags (the Groovy backend sometimes wraps axioms in <a> tags) */
function stripHtml(s: string): string {
  return s.replace(/<[^>]*>/g, '')
}

export default function ClassPage() {
  const { id, iri } = useParams<{ id: string; iri: string }>()
  const decodedIri = decodeURIComponent(iri || '')
  const [cls, setCls] = useState<Record<string, unknown> | null>(null)
  const [subs, setSubs] = useState<ClassResult[]>([])
  const [supers, setSupers] = useState<ClassResult[]>([])
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!id || !decodedIri) return
    getClass(decodedIri, id).then(d => setCls(d as unknown as Record<string, unknown>)).catch(() => setErr('Class not found'))
    const q = `<${decodedIri}>`
    dlQuery(q, 'subclass', id, true).then(d => setSubs(d.result?.slice(0, 100) || [])).catch(() => {})
    dlQuery(q, 'superclass', id, true).then(d => setSupers(d.result?.slice(0, 100) || [])).catch(() => {})
  }, [id, decodedIri])

  if (err) return <div className="max-w-4xl mx-auto px-4 py-8 text-red-500">{err}</div>
  if (!cls) return (
    <div className="max-w-4xl mx-auto px-4 py-12 flex justify-center">
      <div className="flex items-center gap-3 text-gray-400">
        <svg className="w-5 h-5 animate-spin" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        Loading class...
      </div>
    </div>
  )

  const label = asList(cls.label)[0] || decodedIri.split(/[#/]/).pop() || decodedIri
  const definitions = asList(cls.definition)
  const synonyms = asList(cls.synonyms)
  const subClassOf = asList(cls.SubClassOf).map(stripHtml)
  const equivalent = asList(cls.Equivalent).map(stripHtml)

  // Collect all other annotation properties
  const skip = new Set(['class', 'owlClass', 'ontology', 'label', 'definition', 'synonyms', 'deprecated', 'SubClassOf', 'Equivalent', 'Disjoint', 'identifier', 'oboid', 'embedding_vector'])
  const otherAnnotations = Object.entries(cls).filter(([k, v]) => !skip.has(k) && v && !(Array.isArray(v) && v.length === 0))

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      {/* Breadcrumb */}
      <nav className="text-xs text-gray-400 mb-4 flex items-center gap-1">
        <Link to="/" className="hover:text-indigo-600">Home</Link>
        <span>/</span>
        <Link to={`/ontology/${id}`} className="hover:text-indigo-600">{id?.toUpperCase()}</Link>
        <span>/</span>
        <span className="text-gray-600">{label}</span>
      </nav>

      {/* Header */}
      <div className="bg-white border border-gray-200 rounded-lg p-5 shadow-sm mb-4">
        <div className="flex items-start gap-3 mb-2">
          <h1 className="text-2xl font-bold text-gray-900 flex-1">{label}</h1>
          <Link to={`/ontology/${id}`}
            className="text-xs bg-indigo-100 text-indigo-700 px-2 py-1 rounded font-medium shrink-0">
            {id?.toUpperCase()}
          </Link>
        </div>
        <p className="text-xs text-gray-400 font-mono break-all mb-3">{decodedIri}</p>

        {/* OBO ID */}
        {cls.oboid ? (
          <div className="text-xs text-gray-500 mb-2">
            <span className="text-gray-400 uppercase tracking-wider mr-1">OBO ID:</span>
            <span className="font-mono">{String(cls.oboid as string)}</span>
          </div>
        ) : null}

        {/* Definitions */}
        {definitions.length > 0 && (
          <div className="mt-3 pt-3 border-t border-gray-100">
            <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Definition</h2>
            {definitions.map((d, i) => <p key={i} className="text-sm text-gray-700 leading-relaxed">{d}</p>)}
          </div>
        )}

        {/* Synonyms */}
        {synonyms.length > 0 && (
          <div className="mt-3 pt-3 border-t border-gray-100">
            <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Synonyms</h2>
            <div className="flex flex-wrap gap-1.5">
              {synonyms.map((s, i) => (
                <span key={i} className="text-xs bg-amber-50 text-amber-800 px-2 py-0.5 rounded-full border border-amber-200">{s}</span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Axioms */}
      {(subClassOf.length > 0 || equivalent.length > 0) && (
        <div className="grid md:grid-cols-2 gap-4 mb-4">
          {subClassOf.length > 0 && (
            <div className="bg-white border border-gray-200 rounded-lg shadow-sm">
              <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-100 rounded-t-lg">
                <h2 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
                  SubClassOf <span className="text-gray-400 font-normal">({subClassOf.length})</span>
                </h2>
              </div>
              <ul className="p-3 space-y-1.5">
                {subClassOf.map((a, i) => (
                  <li key={i} className="py-1 px-2 bg-gray-50/50 rounded">
                    <OWLExpression expression={a} />
                  </li>
                ))}
              </ul>
            </div>
          )}
          {equivalent.length > 0 && (
            <div className="bg-white border border-gray-200 rounded-lg shadow-sm">
              <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-100 rounded-t-lg">
                <h2 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
                  EquivalentTo <span className="text-gray-400 font-normal">({equivalent.length})</span>
                </h2>
              </div>
              <ul className="p-3 space-y-1.5">
                {equivalent.map((a, i) => (
                  <li key={i} className="py-1 px-2 bg-gray-50/50 rounded">
                    <OWLExpression expression={a} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Hierarchy */}
      <div className="grid md:grid-cols-2 gap-4 mb-4">
        <div className="bg-white border border-gray-200 rounded-lg shadow-sm">
          <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-100 rounded-t-lg">
            <h2 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
              Superclasses <span className="text-gray-400 font-normal">({supers.length})</span>
            </h2>
          </div>
          <div className="p-3">
            {supers.length === 0 ? (
              <p className="text-xs text-gray-400 italic">None or still loading</p>
            ) : (
              <ul className="space-y-1">
                {supers.map((c, i) => (
                  <li key={i}>
                    <Link to={`/ontology/${id}/class/${encodeURIComponent(c.class)}`}
                      className="text-sm text-indigo-600 hover:text-indigo-800 hover:underline flex items-center gap-1.5">
                      <span className="text-gray-300 text-xs">▲</span>
                      {Array.isArray(c.label) ? c.label[0] : c.label}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-lg shadow-sm">
          <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-100 rounded-t-lg">
            <h2 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
              Subclasses <span className="text-gray-400 font-normal">({subs.length})</span>
            </h2>
          </div>
          <div className="p-3">
            {subs.length === 0 ? (
              <p className="text-xs text-gray-400 italic">None or still loading</p>
            ) : (
              <ul className="space-y-1">
                {subs.map((c, i) => (
                  <li key={i}>
                    <Link to={`/ontology/${id}/class/${encodeURIComponent(c.class)}`}
                      className="text-sm text-indigo-600 hover:text-indigo-800 hover:underline flex items-center gap-1.5">
                      <span className="text-gray-300 text-xs">▼</span>
                      {Array.isArray(c.label) ? c.label[0] : c.label}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      {/* Other annotations */}
      {otherAnnotations.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg shadow-sm">
          <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-100 rounded-t-lg">
            <h2 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">Annotations</h2>
          </div>
          <dl className="p-4 text-sm space-y-2">
            {otherAnnotations.map(([k, v]) => (
              <div key={k} className="flex gap-3 py-1 border-b border-gray-50 last:border-0">
                <dt className="text-gray-500 shrink-0 min-w-[140px] text-xs uppercase tracking-wider pt-0.5">{k}</dt>
                <dd className="text-gray-700 break-all">{Array.isArray(v) ? (v as string[]).join(', ') : String(v)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  )
}
