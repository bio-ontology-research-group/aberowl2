import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { listOntologies, getStats } from '../api/client'
import type { OntologySummary, StatsAggregate } from '../api/types'

export default function Home() {
  const [ontologies, setOntologies] = useState<OntologySummary[]>([])
  const [stats, setStats] = useState<StatsAggregate | null>(null)
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState('')
  const nav = useNavigate()

  useEffect(() => {
    listOntologies().then(setOntologies).catch(() => {})
    getStats().then(setStats).catch(() => {})
  }, [])

  function onSearch(e: React.FormEvent) {
    e.preventDefault()
    if (q.trim()) nav(`/search?q=${encodeURIComponent(q.trim())}`)
  }

  const filtered = filter
    ? ontologies.filter(o =>
        o.id.toLowerCase().includes(filter.toLowerCase()) ||
        (o.title || '').toLowerCase().includes(filter.toLowerCase()))
    : ontologies

  const online = ontologies.filter(o => o.status === 'online')

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      {/* Hero */}
      <div className="text-center mb-10">
        <h1 className="text-4xl font-extrabold text-gray-900 mb-2 tracking-tight">AberOWL</h1>
        <p className="text-gray-500 mb-6 text-lg">Ontology Repository &amp; OWL Reasoning Services</p>
        <form onSubmit={onSearch} className="max-w-xl mx-auto flex gap-2">
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search for classes across all ontologies..."
            className="flex-1 px-4 py-3 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-indigo-400 shadow-sm"
          />
          <button type="submit"
            className="px-6 py-3 bg-indigo-600 text-white rounded-xl text-sm font-semibold hover:bg-indigo-700 shadow-sm transition-colors">
            Search
          </button>
        </form>
        <div className="flex justify-center gap-4 mt-4">
          <Link to="/dlquery" className="text-sm text-indigo-600 hover:text-indigo-800 font-medium">DL Query</Link>
          <span className="text-gray-300">|</span>
          <Link to="/sparql" className="text-sm text-indigo-600 hover:text-indigo-800 font-medium">SPARQL + OWL</Link>
        </div>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          {[
            { label: 'Ontologies', value: stats.total_ontologies, color: 'text-indigo-700' },
            { label: 'Online', value: stats.online_ontologies, color: 'text-emerald-700' },
            { label: 'Classes', value: stats.total_classes?.toLocaleString() ?? '0', color: 'text-gray-900' },
            { label: 'Properties', value: stats.total_properties?.toLocaleString() ?? '0', color: 'text-gray-900' },
          ].map(s => (
            <div key={s.label} className="bg-white border border-gray-200 rounded-xl p-5 text-center shadow-sm">
              <div className={`text-3xl font-bold ${s.color}`}>{s.value}</div>
              <div className="text-xs text-gray-500 mt-1 uppercase tracking-wider">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Ontology list */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold text-gray-900">
          Ontologies{' '}
          <span className="text-sm font-normal text-gray-400">
            ({online.length} online / {ontologies.length} total)
          </span>
        </h2>
        <input
          value={filter}
          onChange={e => setFilter(e.target.value)}
          placeholder="Filter ontologies..."
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm w-56 focus:ring-2 focus:ring-indigo-400 focus:outline-none"
        />
      </div>
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs text-gray-500 uppercase tracking-wider">
            <tr>
              <th className="px-4 py-3">ID</th>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3 text-center w-20">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {filtered.slice(0, 200).map(o => (
              <tr key={o.id} className="hover:bg-indigo-50/30 transition-colors">
                <td className="px-4 py-2.5">
                  <Link to={`/ontology/${o.id}`} className="text-indigo-600 font-semibold hover:underline">
                    {o.id.toUpperCase()}
                  </Link>
                </td>
                <td className="px-4 py-2.5 text-gray-700">{o.title || <span className="text-gray-400 italic">—</span>}</td>
                <td className="px-4 py-2.5 text-center">
                  <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full ${
                    o.status === 'online'
                      ? 'bg-emerald-100 text-emerald-700'
                      : 'bg-gray-100 text-gray-500'
                  }`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${
                      o.status === 'online' ? 'bg-emerald-500' : 'bg-gray-400'
                    }`} />
                    {o.status === 'online' ? 'Online' : 'Offline'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div className="px-4 py-8 text-center text-sm text-gray-400">No ontologies match your filter.</div>
        )}
        {filtered.length > 200 && (
          <div className="px-4 py-2 text-xs text-gray-400 text-center border-t">
            Showing 200 of {filtered.length}
          </div>
        )}
      </div>
    </div>
  )
}
