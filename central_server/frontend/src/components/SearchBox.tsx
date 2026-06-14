import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { searchClasses } from '../api/client'
import type { ClassResult } from '../api/types'

interface Props {
  /** Tailwind sizing/extra classes for the wrapper */
  className?: string
  inputClassName?: string
  placeholder?: string
  /** Clear the input after navigating (used in the header) */
  clearOnNavigate?: boolean
  autoFocus?: boolean
}

function label(c: ClassResult): string {
  return (Array.isArray(c.label) ? c.label[0] : c.label) || c.class
}

/** Search input with debounced class-name autocomplete across all ontologies. */
export default function SearchBox({
  className = '', inputClassName = '', placeholder = 'Search classes…', clearOnNavigate, autoFocus,
}: Props) {
  const [q, setQ] = useState('')
  const [suggestions, setSuggestions] = useState<ClassResult[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const nav = useNavigate()
  const rootRef = useRef<HTMLDivElement>(null)
  const seq = useRef(0)

  useEffect(() => {
    const term = q.trim()
    const mySeq = ++seq.current
    const t = setTimeout(() => {
      if (term.length < 2) { if (seq.current === mySeq) setSuggestions([]); return }
      searchClasses(term, undefined, 8, true)
        .then(r => { if (seq.current === mySeq) { setSuggestions(r); setOpen(true) } })
        .catch(() => { if (seq.current === mySeq) setSuggestions([]) })
    }, 200)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  function goToSearch() {
    const term = q.trim()
    if (!term) return
    nav(`/search?q=${encodeURIComponent(term)}`)
    setOpen(false)
    if (clearOnNavigate) setQ('')
  }

  function goToClass(c: ClassResult) {
    nav(`/ontology/${c.ontology}/class/${encodeURIComponent(c.class)}`)
    setOpen(false)
    if (clearOnNavigate) setQ('')
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive(a => Math.min(a + 1, suggestions.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, -1)) }
    else if (e.key === 'Enter') {
      e.preventDefault()
      if (open && active >= 0 && suggestions[active]) goToClass(suggestions[active])
      else goToSearch()
    } else if (e.key === 'Escape') { setOpen(false); setActive(-1) }
  }

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <form onSubmit={e => { e.preventDefault(); goToSearch() }}>
        <input
          value={q}
          autoFocus={autoFocus}
          onChange={e => { setQ(e.target.value); setActive(-1) }}
          onFocus={() => suggestions.length && setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
          className={inputClassName}
        />
      </form>
      {open && suggestions.length > 0 && (
        <ul
          role="listbox"
          className="absolute z-50 mt-1 w-full max-h-80 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg text-sm text-left"
        >
          {suggestions.map((c, i) => (
            <li
              key={c.class || i}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onMouseDown={e => { e.preventDefault(); goToClass(c) }}
              className={`px-3 py-2 cursor-pointer flex items-center gap-2 ${i === active ? 'bg-indigo-50' : ''}`}
            >
              <span className="flex-1 min-w-0 truncate text-gray-800">{label(c)}</span>
              <span className="text-[10px] uppercase bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded shrink-0">
                {c.ontology}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
