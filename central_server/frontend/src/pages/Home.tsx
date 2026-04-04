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
        o.title.toLowerCase().includes(filter.toLowerCase()))
    : ontologies

  const online = ontologies.filter(o => o.status === 'online')

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      {/* Hero */}
      <div className="text-center mb-10">
        <h1 className="text-4xl font-bold text-gray-900 mb-2">AberOWL</h1>
        <p className="text-gray-500 mb-6">Ontology Repository with OWL Reasoning Services</p>
        <form onSubmit={onSearch} className="max-w-xl mx-auto flex gap-2">
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search for classes across all ontologies..."
            className="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
          <button type="submit" className="px-5 py-2.5 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700">
            Search
          </button>
        </form>
        <div className="flex justify-center gap-3 mt-4">
          <Link to="/dlquery" className="text-sm text-indigo-600 hover:underline">DL Query</Link>
          <span className="text-gray-300">|</span>
          <Link to="/sparql" className="text-sm text-indigo-600 hover:underline">SPARQL + OWL</Link>
        </div>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          {[
            { label: 'Ontologies', value: stats.total_ontologies },
            { label: 'Online', value: stats.online_ontologies },
            { label: 'Classes', value: stats.total_classes.toLocaleString() },
            { label: 'Properties', value: stats.total_properties.toLocaleString() },
          ].map(s => (
            <div key={s.label} className="bg-white border border-gray-200 rounded-lg p-4 text-center">
              <div className="text-2xl font-bold text-gray-900">{s.value}</div>
              <div className="text-xs text-gray-500">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Ontology list */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold text-gray-900">
          Ontologies <span className="text-sm font-normal text-gray-400">({online.length} online / {ontologies.length} total)</span>
        </h2>
        <input
          value={filter}
          onChange={e => setFilter(e.target.value)}
          placeholder="Filter..."
          className="px-3 py-1 border border-gray-300 rounded text-sm w-48"
        />
      </div>
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs text-gray-500 uppercase">
            <tr>
              <th className="px-4 py-2">ID</th>
              <th className="px-4 py-2">Title</th>
              <th className="px-4 py-2 text-center">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {filtered.slice(0, 200).map(o => (
              <tr key={o.id} className="hover:bg-gray-50">
                <td className="px-4 py-2">
                  <Link to={`/ontology/${o.id}`} className="text-indigo-600 font-medium hover:underline">
                    {o.id.toUpperCase()}
                  </Link>
                </td>
                <td className="px-4 py-2 text-gray-700">{o.title}</td>
                <td className="px-4 py-2 text-center">
                  <span className={`inline-block w-2 h-2 rounded-full ${o.status === 'online' ? 'bg-green-500' : 'bg-red-400'}`} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length > 200 && (
          <div className="px-4 py-2 text-xs text-gray-400 text-center">Showing 200 of {filtered.length}</div>
        )}
      </div>
    </div>
  )
}
