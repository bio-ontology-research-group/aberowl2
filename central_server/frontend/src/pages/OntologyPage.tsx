import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getOntology, dlQuery } from '../api/client'
import type { OntologyDetail, ClassResult } from '../api/types'
import TreeNode from '../components/TreeNode'

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
    dlQuery('<http://www.w3.org/2002/07/owl#Thing>', 'subclass', id)
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
  if (!ont) return <div className="max-w-5xl mx-auto px-4 py-8 text-gray-500">Loading...</div>

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-1">
          <h1 className="text-2xl font-bold text-gray-900">{ont.ontology?.toUpperCase()}</h1>
          <span className={`text-xs px-2 py-0.5 rounded-full ${ont.status === 'online' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
            {ont.status}
          </span>
        </div>
        {ont.title && <p className="text-gray-600">{ont.title}</p>}
        {ont.description && <p className="text-sm text-gray-500 mt-1">{ont.description}</p>}
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        {[
          { l: 'Classes', v: ont.class_count },
          { l: 'Properties', v: ont.property_count },
          { l: 'Obj Props', v: ont.object_property_count },
          { l: 'Data Props', v: ont.data_property_count },
          { l: 'Individuals', v: ont.individual_count },
        ].map(s => (
          <div key={s.l} className="bg-white border border-gray-200 rounded p-3 text-center">
            <div className="text-lg font-semibold text-gray-900">{(s.v ?? 0).toLocaleString()}</div>
            <div className="text-xs text-gray-500">{s.l}</div>
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-gray-200">
        {(['browse', 'query', 'info'] as Tab[]).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t === 'browse' ? 'Class Hierarchy' : t === 'query' ? 'DL Query' : 'Metadata'}
          </button>
        ))}
      </div>

      {/* Browse tab */}
      {tab === 'browse' && (
        <div className="bg-white border border-gray-200 rounded-lg p-4 max-h-[70vh] overflow-y-auto">
          {rootClasses === null ? (
            <p className="text-sm text-gray-500">Loading class hierarchy...</p>
          ) : rootClasses.length === 0 ? (
            <p className="text-sm text-gray-500">No root classes found</p>
          ) : (
            rootClasses.map((c, i) => (
              <TreeNode key={c.class || i} node={c} ontologyId={id!} />
            ))
          )}
        </div>
      )}

      {/* DL Query tab */}
      {tab === 'query' && (
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <form onSubmit={runQuery} className="flex flex-col gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Manchester OWL Syntax Query</label>
              <input
                value={queryText}
                onChange={e => setQueryText(e.target.value)}
                placeholder="e.g. 'part of' some 'cell'"
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-indigo-400 focus:outline-none"
              />
            </div>
            <div className="flex gap-3 items-end">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Query type</label>
                <select value={queryType} onChange={e => setQueryType(e.target.value)}
                  className="px-3 py-2 border border-gray-300 rounded text-sm">
                  <option value="subeq">Subclass + Equiv</option>
                  <option value="subclass">Subclass</option>
                  <option value="superclass">Superclass</option>
                  <option value="supeq">Superclass + Equiv</option>
                  <option value="equivalent">Equivalent</option>
                </select>
              </div>
              <button type="submit" disabled={queryLoading}
                className="px-4 py-2 bg-indigo-600 text-white rounded text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
                {queryLoading ? 'Running...' : 'Run Query'}
              </button>
            </div>
          </form>
          {queryTime !== null && (
            <p className="text-xs text-gray-500 mt-3">{queryResults.length} results in {queryTime} ms</p>
          )}
          {queryResults.length > 0 && (
            <div className="mt-3 max-h-96 overflow-y-auto divide-y divide-gray-100">
              {queryResults.map((c, i) => (
                <div key={c.class || i} className="py-1.5 flex items-center gap-2 text-sm">
                  <a href={`/ontology/${id}/class/${encodeURIComponent(c.class)}`}
                    className="text-indigo-600 hover:underline truncate">
                    {Array.isArray(c.label) ? c.label[0] : c.label}
                  </a>
                  <span className="text-xs text-gray-400 truncate">{c.class}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Info tab */}
      {tab === 'info' && (
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2 text-sm">
            {[
              ['Version', ont.version_info],
              ['License', ont.license],
              ['Home Page', ont.home_page],
            ].filter(([, v]) => v).map(([k, v]) => (
              <div key={k as string}>
                <dt className="text-gray-500 text-xs uppercase">{k}</dt>
                <dd className="text-gray-800">
                  {String(v).startsWith('http') ? <a href={v as string} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline">{v}</a> : v}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  )
}
