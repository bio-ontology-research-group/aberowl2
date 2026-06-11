import { useEffect, useMemo, useRef, useState } from 'react'

export interface ComboboxOption {
  value: string
  label: string
  /** Optional secondary text shown muted after the label */
  hint?: string
}

interface Props {
  options: ComboboxOption[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
  /** Label for the value === '' choice (e.g. "All ontologies"); omit to hide it */
  allLabel?: string
  className?: string
  id?: string
}

/**
 * Searchable, keyboard-navigable single-select combobox. Replaces a native
 * <select> when the option count is large (hundreds of ontologies).
 */
export default function Combobox({
  options, value, onChange, placeholder = 'Search…', allLabel, className = '', id,
}: Props) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const all: ComboboxOption[] = useMemo(
    () => (allLabel ? [{ value: '', label: allLabel }, ...options] : options),
    [options, allLabel],
  )

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return all
    return all.filter(o =>
      o.value.toLowerCase().includes(q) || o.label.toLowerCase().includes(q) ||
      (o.hint || '').toLowerCase().includes(q))
  }, [all, query])

  const selected = all.find(o => o.value === value)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  // Keep active item in view
  useEffect(() => {
    if (!open || !listRef.current) return
    const el = listRef.current.children[active] as HTMLElement | undefined
    el?.scrollIntoView({ block: 'nearest' })
  }, [active, open])

  function commit(opt: ComboboxOption) {
    onChange(opt.value)
    setOpen(false)
    setQuery('')
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, filtered.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (filtered[active]) commit(filtered[active]) }
    else if (e.key === 'Escape') { setOpen(false); setQuery('') }
  }

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={id ? `${id}-listbox` : undefined}
        aria-autocomplete="list"
        value={open ? query : (selected?.label ?? '')}
        placeholder={selected ? selected.label : placeholder}
        onChange={e => { setQuery(e.target.value); setActive(0); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-indigo-400 focus:border-indigo-400 focus:outline-none"
      />
      {open && (
        <ul
          ref={listRef}
          id={id ? `${id}-listbox` : undefined}
          role="listbox"
          className="absolute z-50 mt-1 w-full max-h-72 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg text-sm"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-gray-400">No matches</li>
          ) : (
            filtered.map((o, i) => (
              <li
                key={o.value || '__all__'}
                role="option"
                aria-selected={o.value === value}
                onMouseDown={e => { e.preventDefault(); commit(o) }}
                onMouseEnter={() => setActive(i)}
                className={`px-3 py-1.5 cursor-pointer flex items-center gap-2 ${
                  i === active ? 'bg-indigo-50' : ''
                } ${o.value === value ? 'font-semibold text-indigo-700' : 'text-gray-700'}`}
              >
                <span className="truncate">{o.label}</span>
                {o.hint && <span className="text-xs text-gray-400 truncate">{o.hint}</span>}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}
