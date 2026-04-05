import { useEffect, useState, useCallback } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { getOntology, getClass, dlQuery } from '../api/client'
import type { OntologyDetail, ClassResult } from '../api/types'
import TreeNode from '../components/TreeNode'
import OWLExpression from '../components/OWLExpression'

type Tab = 'browse' | 'query' | 'info'

function asList(v: unknown): string[] {
  if (!v) return []
  if (Array.isArray(v)) return v
  return [String(v)]
}

export default function OntologyPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [ont, setOnt] = useState<OntologyDetail | null>(null)
  const [tab, setTab] = useState<Tab>('browse')
  const [rootClasses, setRootClasses] = useState<ClassResult[] | null>(null)
  const [err, setErr] = useState('')

  // Selected class detail (OLS-style: click tree node → show details on right)
  const [selectedIri, setSelectedIri] = useState<string | null>(null)
  const [selectedClass, setSelectedClass] = useState<Record<string, unknown> | null>(null)
  const [selectedSubs, setSelectedSubs] = useState<ClassResult[]>([])
  const [selectedSupers, setSelectedSupers] = useState<ClassResult[]>([])
  const [detailLoading, setDetailLoading] = useState(false)

  // DL query state
  const [queryText, setQueryText] = useState('')
  const [queryType, setQueryType] = useState('subeq')
  const [queryResults, setQueryResults] = useState<ClassResult[]>([])
  const [queryTime, setQueryTime] = useState<number | null>(null)
  const [queryLoading, setQueryLoading] = useState(false)

  useEffect(() => {
    if (!id) return
    getOntology(id).then(setOnt).catch(() => setErr('Ontology not found'))
  }, [id])

  useEffect(() => {
    if (!id || tab !== 'browse' || rootClasses !== null) return
    dlQuery('<http://www.w3.org/2002/07/owl#Thing>', 'subclass', id, true)
      .then(d => {
        const results = d.result || []
        // Sort: non-deprecated first, deprecated last, alphabetical within each group
        results.sort((a: ClassResult, b: ClassResult) => {
          const aObs = a.deprecated || /^obsolete /i.test(Array.isArray(a.label) ? a.label[0] || '' : a.label || '') ? 1 : 0
          const bObs = b.deprecated || /^obsolete /i.test(Array.isArray(b.label) ? b.label[0] || '' : b.label || '') ? 1 : 0
          if (aObs !== bObs) return aObs - bObs
          const aL = (Array.isArray(a.label) ? a.label[0] : a.label) || ''
          const bL = (Array.isArray(b.label) ? b.label[0] : b.label) || ''
          return aL.localeCompare(bL)
        })
        setRootClasses(results)
      })
      .catch(() => setRootClasses([]))
  }, [id, tab, rootClasses])

  // Load class details when a tree node is selected
  const onSelectClass = useCallback((iri: string) => {
    if (!id || iri === selectedIri) return
    setSelectedIri(iri)
    setSelectedClass(null)
    setSelectedSubs([])
    setSelectedSupers([])
    setDetailLoading(true)

    const q = iri.startsWith('http') ? `<${iri}>` : iri

    Promise.all([
      getClass(iri, id).catch(() => null),
      dlQuery(q, 'subclass', id, true).then(d => d.result?.slice(0, 50) || []).catch(() => []),
      dlQuery(q, 'superclass', id, true).then(d => d.result?.slice(0, 50) || []).catch(() => []),
    ]).then(([cls, subs, supers]) => {
      setSelectedClass(cls as Record<string, unknown> | null)
      setSelectedSubs(subs)
      setSelectedSupers(supers)
      setDetailLoading(false)
    })
  }, [id, selectedIri])

  function runQuery(e: React.FormEvent) {
    e.preventDefault()
    if (!queryText.trim() || !id) return
    setQueryLoading(true)
    dlQuery(queryText, queryType, id)
      .then(d => { setQueryResults(d.result || []); setQueryTime(d.time) })
      .catch(() => setQueryResults([]))
      .finally(() => setQueryLoading(false))
  }

  if (err) return <div className="max-w-5xl mx-auto px-4 py-8 text-red-500">{err}</div>
  if (!ont) return (
    <div className="max-w-5xl mx-auto px-4 py-12 flex justify-center">
      <div className="flex items-center gap-3 text-gray-400">
        <Spinner /> Loading ontology...
      </div>
    </div>
  )

  const title = ont.title || ont.ontology?.toUpperCase() || id?.toUpperCase()
  const selLabel = selectedClass
    ? (asList(selectedClass.label)[0] || selectedIri?.split(/[#/]/).pop() || '')
    : ''

  return (
    <div className="max-w-[1400px] mx-auto px-4 py-5">
      {/* Header */}
      <div className="mb-5">
        <div className="flex items-center gap-3 mb-1">
          <span className="text-xs font-mono bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded font-semibold">
            {ont.ontology?.toUpperCase() || id?.toUpperCase()}
          </span>
          <StatusBadge status={ont.status} />
        </div>
        <h1 className="text-xl font-bold text-gray-900 mt-1">{title}</h1>
        {ont.description && <p className="text-sm text-gray-500 mt-1 max-w-3xl">{ont.description}</p>}
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 md:grid-cols-5 gap-2 mb-5">
        {[
          { l: 'Classes', v: ont.class_count },
          { l: 'Properties', v: ont.property_count },
          { l: 'Object Props', v: ont.object_property_count },
          { l: 'Data Props', v: ont.data_property_count },
          { l: 'Individuals', v: ont.individual_count },
        ].map(s => (
          <div key={s.l} className="bg-white border border-gray-200 rounded-lg p-2.5 text-center shadow-sm">
            <div className="text-lg font-semibold text-gray-900">{(s.v ?? 0).toLocaleString()}</div>
            <div className="text-[10px] text-gray-500 uppercase tracking-wider">{s.l}</div>
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-gray-200">
        {([
          { key: 'browse' as Tab, label: 'Class Hierarchy' },
          { key: 'query' as Tab, label: 'DL Query' },
          { key: 'info' as Tab, label: 'Metadata' },
        ]).map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t.key
                ? 'border-indigo-600 text-indigo-700'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Browse tab — OLS-style: tree left, details right */}
      {tab === 'browse' && (
        <div className="flex gap-4" style={{ minHeight: '60vh' }}>
          {/* Left: Tree */}
          <div className="w-[380px] shrink-0 bg-white border border-gray-200 rounded-lg shadow-sm flex flex-col">
            <div className="px-3 py-2.5 border-b border-gray-100 bg-gray-50/60 rounded-t-lg">
              <h3 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
                Class Hierarchy
              </h3>
            </div>
            <div className="p-2 flex-1 overflow-y-auto text-[13px] font-mono max-h-[70vh]">
              {rootClasses === null ? (
                <div className="flex items-center gap-2 text-gray-400 py-4 justify-center">
                  <Spinner /> <span className="text-sm font-sans">Loading...</span>
                </div>
              ) : rootClasses.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-4 font-sans">No root classes</p>
              ) : (
                rootClasses.map((c, i) => (
                  <TreeNode
                    key={c.class || i}
                    node={c}
                    ontologyId={id!}
                    onSelect={onSelectClass}
                    selectedIri={selectedIri}
                  />
                ))
              )}
            </div>
          </div>

          {/* Right: Class detail panel */}
          <div className="flex-1 min-w-0">
            {!selectedIri ? (
              <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-8 text-center text-gray-400 text-sm h-full flex items-center justify-center">
                <div>
                  <div className="text-3xl mb-2 opacity-30">◆</div>
                  Select a class from the hierarchy to view details
                </div>
              </div>
            ) : detailLoading ? (
              <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-8 flex items-center justify-center h-full">
                <div className="flex items-center gap-3 text-gray-400">
                  <Spinner /> Loading class details...
                </div>
              </div>
            ) : selectedClass ? (
              <ClassDetailPanel
                cls={selectedClass}
                iri={selectedIri}
                label={selLabel}
                subs={selectedSubs}
                supers={selectedSupers}
                ontologyId={id!}
                onNavigate={(iri) => {
                  onSelectClass(iri)
                }}
                onOpenFull={() => navigate(`/ontology/${id}/class/${encodeURIComponent(selectedIri!)}`)}
              />
            ) : (
              <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-8 text-center text-gray-400 text-sm">
                Class details not available. The class may not be indexed yet.
              </div>
            )}
          </div>
        </div>
      )}

      {/* DL Query tab */}
      {tab === 'query' && (
        <div className="bg-white border border-gray-200 rounded-lg p-5 shadow-sm">
          <form onSubmit={runQuery} className="flex flex-col gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Manchester OWL Syntax Query</label>
              <textarea
                value={queryText}
                onChange={e => setQueryText(e.target.value)}
                placeholder="e.g. 'part of' some 'cell'"
                rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-400 focus:border-indigo-400 focus:outline-none resize-none"
              />
              {queryText.trim() && (
                <div className="mt-2 px-3 py-2 bg-gray-50 rounded-lg border border-gray-100">
                  <span className="text-[10px] text-gray-400 uppercase tracking-wider">Preview: </span>
                  <OWLExpression expression={queryText} />
                </div>
              )}
            </div>
            <div className="flex gap-3 items-end">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Query type</label>
                <select value={queryType} onChange={e => setQueryType(e.target.value)}
                  className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white">
                  <option value="subeq">Subclass + Equiv</option>
                  <option value="subclass">Subclass</option>
                  <option value="superclass">Superclass</option>
                  <option value="supeq">Superclass + Equiv</option>
                  <option value="equivalent">Equivalent</option>
                </select>
              </div>
              <button type="submit" disabled={queryLoading}
                className="px-5 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors">
                {queryLoading ? 'Running...' : 'Run Query'}
              </button>
            </div>
          </form>

          {queryTime !== null && (
            <p className="text-sm text-gray-500 mt-4 mb-2">
              <span className="font-medium text-gray-700">{queryResults.length}</span> results in{' '}
              <span className="font-medium text-gray-700">{queryTime}</span> ms
            </p>
          )}
          {queryResults.length > 0 && (
            <div className="mt-2 max-h-96 overflow-y-auto divide-y divide-gray-100 border border-gray-200 rounded-lg">
              {queryResults.map((c, i) => (
                <div key={c.class || i} className="py-2 px-3 flex items-center gap-2 text-sm hover:bg-gray-50">
                  <Link to={`/ontology/${id}/class/${encodeURIComponent(c.class)}`}
                    className="text-indigo-600 hover:underline truncate font-medium">
                    {Array.isArray(c.label) ? c.label[0] : c.label}
                  </Link>
                  <span className="text-xs text-gray-400 truncate">{c.class}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Info tab */}
      {tab === 'info' && (
        <div className="bg-white border border-gray-200 rounded-lg p-5 shadow-sm">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">Ontology Metadata</h3>
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-sm">
            {[
              ['Ontology ID', ont.ontology],
              ['Title', ont.title],
              ['Version', ont.version_info],
              ['License', ont.license],
              ['Home Page', ont.home_page],
            ].filter(([, v]) => v).map(([k, v]) => (
              <div key={k as string} className="border-b border-gray-100 pb-2">
                <dt className="text-gray-500 text-xs uppercase tracking-wider mb-0.5">{k}</dt>
                <dd className="text-gray-800">
                  {String(v).startsWith('http') ? (
                    <a href={v as string} target="_blank" rel="noreferrer"
                      className="text-indigo-600 hover:underline break-all">{v}</a>
                  ) : v}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  )
}

/* ---- Sub-components ---- */

function Spinner() {
  return (
    <svg className="w-4 h-4 animate-spin text-indigo-500" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  )
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
      status === 'online' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'
    }`}>
      {status === 'online' ? 'Online' : 'Offline'}
    </span>
  )
}

interface ClassDetailPanelProps {
  cls: Record<string, unknown>
  iri: string
  label: string
  subs: ClassResult[]
  supers: ClassResult[]
  ontologyId: string
  onNavigate: (iri: string) => void
  onOpenFull: () => void
}

function ClassDetailPanel({ cls, iri, label, subs, supers, onNavigate, onOpenFull }: ClassDetailPanelProps) {
  const definitions = asList(cls.definition)
  const synonyms = asList(cls.synonyms)
  const subClassOf = asList(cls.SubClassOf)
  const equivalent = asList(cls.Equivalent)
  const disjoint = asList(cls.Disjoint)

  const skip = new Set(['class', 'owlClass', 'ontology', 'label', 'definition', 'synonyms',
    'deprecated', 'SubClassOf', 'Equivalent', 'Disjoint', 'identifier', 'oboid', 'embedding_vector'])
  const otherAnnotations = Object.entries(cls).filter(([k, v]) => !skip.has(k) && v && !(Array.isArray(v) && v.length === 0))

  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-y-auto max-h-[75vh]">
      {/* Header */}
      <div className="px-5 py-4 border-b border-gray-100 bg-gray-50/50">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-lg font-bold text-gray-900 break-words">{label}</h2>
            <p className="text-xs text-gray-400 font-mono break-all mt-0.5">{iri}</p>
            {cls.oboid ? (
              <span className="text-xs text-gray-500 font-mono">{String(cls.oboid as string)}</span>
            ) : null}
          </div>
          <button onClick={onOpenFull}
            className="text-xs text-indigo-600 hover:text-indigo-800 shrink-0 px-2 py-1 border border-indigo-200 rounded hover:bg-indigo-50 transition-colors"
            title="Open full class page">
            Full page →
          </button>
        </div>
      </div>

      <div className="p-5 space-y-5">
        {/* Description */}
        {definitions.length > 0 && (
          <Section title="Description">
            {definitions.map((d, i) => <p key={i} className="text-sm text-gray-700 leading-relaxed">{d}</p>)}
          </Section>
        )}

        {/* Synonyms */}
        {synonyms.length > 0 && (
          <Section title="Synonyms">
            <div className="flex flex-wrap gap-1.5">
              {synonyms.map((s, i) => (
                <span key={i} className="text-xs bg-amber-50 text-amber-800 px-2 py-0.5 rounded-full border border-amber-200">{s}</span>
              ))}
            </div>
          </Section>
        )}

        {/* Hierarchy */}
        <Section title="Hierarchy">
          {supers.length > 0 && (
            <div className="mb-2">
              <span className="text-[10px] text-gray-400 uppercase tracking-wider">Superclasses</span>
              <ul className="mt-1 space-y-0.5">
                {supers.map((c, i) => (
                  <li key={i}>
                    <button onClick={() => onNavigate(c.class)}
                      className="text-sm text-indigo-600 hover:underline flex items-center gap-1.5 text-left">
                      <span className="text-gray-300 text-xs">▲</span>
                      {Array.isArray(c.label) ? c.label[0] : c.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {subs.length > 0 && (
            <div>
              <span className="text-[10px] text-gray-400 uppercase tracking-wider">Subclasses ({subs.length})</span>
              <ul className="mt-1 space-y-0.5 max-h-40 overflow-y-auto">
                {subs.map((c, i) => (
                  <li key={i}>
                    <button onClick={() => onNavigate(c.class)}
                      className="text-sm text-indigo-600 hover:underline flex items-center gap-1.5 text-left">
                      <span className="text-gray-300 text-xs">▼</span>
                      {Array.isArray(c.label) ? c.label[0] : c.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {supers.length === 0 && subs.length === 0 && (
            <p className="text-xs text-gray-400 italic">No direct super/subclasses</p>
          )}
        </Section>

        {/* Axioms with syntax highlighting */}
        {(subClassOf.length > 0 || equivalent.length > 0 || disjoint.length > 0) && (
          <Section title="Logical Axioms">
            {subClassOf.length > 0 && (
              <AxiomBlock label="SubClassOf" axioms={subClassOf} />
            )}
            {equivalent.length > 0 && (
              <AxiomBlock label="EquivalentTo" axioms={equivalent} />
            )}
            {disjoint.length > 0 && (
              <AxiomBlock label="DisjointWith" axioms={disjoint} />
            )}
          </Section>
        )}

        {/* Other annotations */}
        {otherAnnotations.length > 0 && (
          <Section title="Annotations">
            <dl className="text-sm space-y-1.5">
              {otherAnnotations.map(([k, v]) => (
                <div key={k} className="flex gap-3">
                  <dt className="text-gray-500 shrink-0 min-w-[100px] text-xs uppercase tracking-wider pt-0.5">{k}</dt>
                  <dd className="text-gray-700 break-all">{Array.isArray(v) ? (v as string[]).join(', ') : String(v)}</dd>
                </div>
              ))}
            </dl>
          </Section>
        )}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 border-b border-gray-100 pb-1">
        {title}
      </h3>
      {children}
    </div>
  )
}

function AxiomBlock({ label, axioms }: { label: string; axioms: string[] }) {
  return (
    <div className="mb-3">
      <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-1">{label}</div>
      <ul className="space-y-1">
        {axioms.map((a, i) => (
          <li key={i} className="py-1.5 px-3 bg-gray-50 rounded border border-gray-100">
            <OWLExpression expression={a} />
          </li>
        ))}
      </ul>
    </div>
  )
}
