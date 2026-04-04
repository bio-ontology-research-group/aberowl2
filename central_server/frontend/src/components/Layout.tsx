import { Link, Outlet, useNavigate } from 'react-router-dom'
import { useState } from 'react'

export default function Layout() {
  const [q, setQ] = useState('')
  const nav = useNavigate()

  function onSearch(e: React.FormEvent) {
    e.preventDefault()
    if (q.trim()) nav(`/search?q=${encodeURIComponent(q.trim())}`)
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-6">
          <Link to="/" className="text-xl font-bold text-indigo-700 shrink-0">AberOWL</Link>
          <nav className="hidden md:flex gap-4 text-sm text-gray-600">
            <Link to="/" className="hover:text-gray-900">Home</Link>
            <Link to="/dlquery" className="hover:text-gray-900">DL Query</Link>
            <Link to="/sparql" className="hover:text-gray-900">SPARQL</Link>
          </nav>
          <form onSubmit={onSearch} className="flex-1 max-w-xl">
            <input
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="Search classes, ontologies..."
              className="w-full px-3 py-1.5 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
          </form>
        </div>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
      <footer className="border-t border-gray-200 bg-white py-4 text-center text-xs text-gray-400">
        AberOWL &mdash; Bio-Ontology Research Group, KAUST
      </footer>
    </div>
  )
}
