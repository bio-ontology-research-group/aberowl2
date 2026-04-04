import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getClass, dlQuery } from '../api/client'
import type { ClassResult } from '../api/types'

function asList(v: unknown): string[] {
  if (!v) return []
  if (Array.isArray(v)) return v
  return [String(v)]
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
    dlQuery(q, 'subclass', id).then(d => setSubs(d.result?.slice(0, 50) || [])).catch(() => {})
    dlQuery(q, 'superclass', id).then(d => setSupers(d.result?.slice(0, 50) || [])).catch(() => {})
  }, [id, decodedIri])

  if (err) return <div className="max-w-4xl mx-auto px-4 py-8 text-red-500">{err}</div>
  if (!cls) return <div className="max-w-4xl mx-auto px-4 py-8 text-gray-500">Loading...</div>

  const label = asList(cls.label)[0] || decodedIri
  const definitions = asList(cls.definition)
  const synonyms = asList(cls.synonyms)
  const subClassOf = asList(cls.SubClassOf)
  const equivalent = asList(cls.Equivalent)

  // Collect all other annotation properties
  const skip = new Set(['class', 'owlClass', 'ontology', 'label', 'definition', 'synonyms', 'deprecated', 'SubClassOf', 'Equivalent', 'Disjoint', 'identifier'])
  const otherAnnotations = Object.entries(cls).filter(([k]) => !skip.has(k) && cls[k])

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <div className="text-xs text-gray-400 mb-2">
        <Link to={`/ontology/${id}`} className="hover:text-indigo-600">{id?.toUpperCase()}</Link>
        <span className="mx-1">/</span>
        <span>{label}</span>
      </div>

      <h1 className="text-2xl font-bold text-gray-900 mb-1">{label}</h1>
      <p className="text-xs text-gray-400 mb-4 break-all">{decodedIri}</p>

      {/* Definitions */}
      {definitions.length > 0 && (
        <div className="mb-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-1">Definition</h2>
          {definitions.map((d, i) => <p key={i} className="text-sm text-gray-600">{d}</p>)}
        </div>
      )}

      {/* Synonyms */}
      {synonyms.length > 0 && (
        <div className="mb-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-1">Synonyms</h2>
          <div className="flex flex-wrap gap-1">
            {synonyms.map((s, i) => (
              <span key={i} className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded">{s}</span>
            ))}
          </div>
        </div>
      )}

      {/* Axioms */}
      <div className="grid md:grid-cols-2 gap-4 mb-4">
        {subClassOf.length > 0 && (
          <div>
            <h2 className="text-sm font-semibold text-gray-700 mb-1">SubClassOf</h2>
            <ul className="text-sm text-gray-600 space-y-0.5">
              {subClassOf.map((a, i) => <li key={i} dangerouslySetInnerHTML={{ __html: a }} />)}
            </ul>
          </div>
        )}
        {equivalent.length > 0 && (
          <div>
            <h2 className="text-sm font-semibold text-gray-700 mb-1">Equivalent</h2>
            <ul className="text-sm text-gray-600 space-y-0.5">
              {equivalent.map((a, i) => <li key={i} dangerouslySetInnerHTML={{ __html: a }} />)}
            </ul>
          </div>
        )}
      </div>

      {/* Hierarchy */}
      <div className="grid md:grid-cols-2 gap-4 mb-4">
        {supers.length > 0 && (
          <div className="bg-white border border-gray-200 rounded-lg p-3">
            <h2 className="text-sm font-semibold text-gray-700 mb-2">Superclasses ({supers.length})</h2>
            <ul className="space-y-0.5">
              {supers.map((c, i) => (
                <li key={i}>
                  <Link to={`/ontology/${id}/class/${encodeURIComponent(c.class)}`}
                    className="text-sm text-indigo-600 hover:underline">
                    {Array.isArray(c.label) ? c.label[0] : c.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
        {subs.length > 0 && (
          <div className="bg-white border border-gray-200 rounded-lg p-3">
            <h2 className="text-sm font-semibold text-gray-700 mb-2">Subclasses ({subs.length})</h2>
            <ul className="space-y-0.5">
              {subs.map((c, i) => (
                <li key={i}>
                  <Link to={`/ontology/${id}/class/${encodeURIComponent(c.class)}`}
                    className="text-sm text-indigo-600 hover:underline">
                    {Array.isArray(c.label) ? c.label[0] : c.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Other annotations */}
      {otherAnnotations.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-3">
          <h2 className="text-sm font-semibold text-gray-700 mb-2">Annotations</h2>
          <dl className="text-sm space-y-1">
            {otherAnnotations.map(([k, v]) => (
              <div key={k} className="flex gap-2">
                <dt className="text-gray-500 shrink-0">{k}:</dt>
                <dd className="text-gray-700">{Array.isArray(v) ? v.join(', ') : String(v)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  )
}
