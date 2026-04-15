import { Link, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useState } from 'react'

export default function Layout() {
  const [q, setQ] = useState('')
  const nav = useNavigate()
  const loc = useLocation()

  function onSearch(e: React.FormEvent) {
    e.preventDefault()
    if (q.trim()) { nav(`/search?q=${encodeURIComponent(q.trim())}`); setQ('') }
  }

  const navItems = [
    { to: '/', label: 'Ontologies' },
    { to: '/dlquery', label: 'DL Query' },
    { to: '/sparql', label: 'SPARQL' },
  ]

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 sticky top-0 z-40 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-6">
          <Link to="/" className="text-xl font-extrabold text-indigo-700 shrink-0 tracking-tight">
            AberOWL
          </Link>
          <nav className="hidden md:flex gap-1 text-sm">
            {navItems.map(n => (
              <Link key={n.to} to={n.to}
                className={`px-3 py-1.5 rounded-md transition-colors ${
                  loc.pathname === n.to
                    ? 'bg-indigo-50 text-indigo-700 font-medium'
                    : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'
                }`}>
                {n.label}
              </Link>
            ))}
          </nav>
          <form onSubmit={onSearch} className="flex-1 max-w-md ml-auto">
            <input
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="Search classes..."
              className="w-full px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-indigo-400"
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
