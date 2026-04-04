import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getOntology, dlQuery } from '../api/client'
import type { OntologyDetail, ClassResult } from '../api/types'
import TreeNode from '../components/TreeNode'
// OWLExpression available for future use in DL query results
// import OWLExpression from '../components/OWLExpression'

type Tab = 'browse' | 'query' | 'info'

export default function OntologyPage() {
  const { id } = useParams<{ id: string }>()
  const [ont, setOnt] = useState<OntologyDetail | null>(null)
  const [tab, setTab] = useState<Tab>('browse')
  const [rootClasses, setRootClasses] = useState<ClassResult[] | null>(null)
  const [err, setErr] = useState('')

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
      .then(d => setRootClasses(d.result || []))
      .catch(() => setRootClasses([]))
  }, [id, tab, rootClasses])

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
        <svg className="w-5 h-5 animate-spin" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        Loading ontology...
      </div>
    </div>
  )

  const title = ont.title || ont.ontology?.toUpperCase() || id?.toUpperCase()

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-1">
          <span className="text-xs font-mono bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded">
            {ont.ontology?.toUpperCase() || id?.toUpperCase()}
          </span>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            ont.status === 'online'
              ? 'bg-emerald-100 text-emerald-700'
              : 'bg-red-100 text-red-700'
          }`}>
            {ont.status === 'online' ? 'Online' : 'Offline'}
          </span>
        </div>
        <h1 className="text-2xl font-bold text-gray-900 mt-2">{title}</h1>
        {ont.description && <p className="text-sm text-gray-500 mt-1 max-w-3xl">{ont.description}</p>}
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 md:grid-cols-5 gap-3 mb-6">
        {[
          { l: 'Classes', v: ont.class_count, icon: '◆' },
          { l: 'Properties', v: ont.property_count, icon: '→' },
          { l: 'Object Props', v: ont.object_property_count, icon: '⟶' },
          { l: 'Data Props', v: ont.data_property_count, icon: '⊳' },
          { l: 'Individuals', v: ont.individual_count, icon: '●' },
        ].map(s => (
          <div key={s.l} className="bg-white border border-gray-200 rounded-lg p-3 text-center shadow-sm">
            <div className="text-lg font-semibold text-gray-900">{(s.v ?? 0).toLocaleString()}</div>
            <div className="text-xs text-gray-500">{s.l}</div>
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-gray-200">
        {([
          { key: 'browse' as Tab, label: 'Class Hierarchy', icon: '🌳' },
          { key: 'query' as Tab, label: 'DL Query', icon: '🔍' },
          { key: 'info' as Tab, label: 'Metadata', icon: 'ℹ' },
        ]).map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t.key
                ? 'border-indigo-600 text-indigo-700'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Browse tab */}
      {tab === 'browse' && (
        <div className="bg-white border border-gray-200 rounded-lg shadow-sm">
          <div className="px-4 py-3 border-b border-gray-100 bg-gray-50/50 rounded-t-lg">
            <h3 className="text-sm font-medium text-gray-700">
              Class Hierarchy
              {rootClasses !== null && (
                <span className="font-normal text-gray-400 ml-2">
                  {rootClasses.length} top-level {rootClasses.length === 1 ? 'class' : 'classes'}
                </span>
              )}
            </h3>
          </div>
          <div className="p-3 max-h-[70vh] overflow-y-auto font-mono text-[13px]">
            {rootClasses === null ? (
              <div className="flex items-center gap-2 text-gray-400 py-4 justify-center">
                <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                <span className="text-sm">Loading class hierarchy...</span>
              </div>
            ) : rootClasses.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-4">No root classes found</p>
            ) : (
              rootClasses.map((c, i) => (
                <TreeNode key={c.class || i} node={c} ontologyId={id!} />
              ))
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

          {/* Syntax guide */}
          <div className="mt-4 p-3 bg-gray-50 rounded-lg text-xs text-gray-500 border border-gray-100">
            <strong className="text-gray-700">Syntax:</strong>{' '}
            Named classes: <code className="bg-white px-1 rounded border text-indigo-600">'cell'</code>{' · '}
            Existential: <code className="bg-white px-1 rounded border text-indigo-600">'part of' <span className="text-amber-600 font-bold">some</span> 'cell'</code>{' · '}
            Intersection: <code className="bg-white px-1 rounded border"><span className="text-amber-600 font-bold">and</span></code>{' · '}
            Union: <code className="bg-white px-1 rounded border"><span className="text-amber-600 font-bold">or</span></code>{' · '}
            IRI: <code className="bg-white px-1 rounded border">&lt;http://...&gt;</code>
          </div>

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
