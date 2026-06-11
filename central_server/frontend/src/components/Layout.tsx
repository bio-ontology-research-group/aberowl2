import { Link, Outlet, useLocation } from 'react-router-dom'
import { useState } from 'react'
import SearchBox from './SearchBox'

export default function Layout() {
  const loc = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)

  const navItems = [
    { to: '/', label: 'Ontologies' },
    { to: '/dlquery', label: 'DL Query' },
    { to: '/sparql', label: 'SPARQL' },
  ]

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 sticky top-0 z-40 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-4">
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
          <SearchBox
            className="flex-1 max-w-md ml-auto"
            clearOnNavigate
            placeholder="Search classes…"
            inputClassName="w-full px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-indigo-400"
          />
          {/* Mobile menu button */}
          <button
            className="md:hidden p-2 -mr-1 text-gray-600 hover:text-gray-900 shrink-0"
            aria-label="Toggle navigation menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(o => !o)}
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              {menuOpen
                ? <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                : <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />}
            </svg>
          </button>
        </div>
        {/* Mobile nav drawer */}
        {menuOpen && (
          <nav className="md:hidden border-t border-gray-100 bg-white px-4 py-2 flex flex-col gap-1 text-sm">
            {navItems.map(n => (
              <Link key={n.to} to={n.to}
                onClick={() => setMenuOpen(false)}
                className={`px-3 py-2 rounded-md transition-colors ${
                  loc.pathname === n.to
                    ? 'bg-indigo-50 text-indigo-700 font-medium'
                    : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'
                }`}>
                {n.label}
              </Link>
            ))}
          </nav>
        )}
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
