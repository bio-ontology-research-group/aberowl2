import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { ClassResult } from '../api/types'
import { dlQuery } from '../api/client'

interface Props {
  node: ClassResult
  ontologyId: string
  depth?: number
  /** If provided, clicking the label calls onSelect instead of navigating */
  onSelect?: (iri: string) => void
  /** Currently selected IRI (for highlighting) */
  selectedIri?: string | null
  /** Ancestor IRIs to auto-expand (deep-link restore) */
  expandPath?: Set<string>
}

function isObsolete(node: ClassResult): boolean {
  return !!(node.deprecated) || /^obsolete /i.test(
    Array.isArray(node.label) ? node.label[0] || '' : node.label || ''
  )
}

/** Sort children: non-deprecated first (alphabetical), deprecated last (alphabetical) */
function sortChildren(children: ClassResult[]): ClassResult[] {
  return [...children].sort((a, b) => {
    const aObs = isObsolete(a) ? 1 : 0
    const bObs = isObsolete(b) ? 1 : 0
    if (aObs !== bObs) return aObs - bObs
    const aLbl = (Array.isArray(a.label) ? a.label[0] : a.label) || ''
    const bLbl = (Array.isArray(b.label) ? b.label[0] : b.label) || ''
    return aLbl.localeCompare(bLbl)
  })
}

/** Move keyboard focus to the previous/next visible treeitem in the whole tree. */
function moveFocus(current: HTMLElement, dir: 1 | -1) {
  const tree = current.closest('[role="tree"]')
  if (!tree) return
  const items = Array.from(tree.querySelectorAll<HTMLElement>('[role="treeitem"] > [data-tree-focusable]'))
  const idx = items.indexOf(current)
  const next = items[idx + dir]
  next?.focus()
}

export default function TreeNode({ node, ontologyId, depth = 0, onSelect, selectedIri, expandPath }: Props) {
  const [children, setChildren] = useState<ClassResult[] | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)

  const lbl = Array.isArray(node.label) ? node.label[0] : node.label
  const iri = node.class || node.owlClass?.replace(/[<>]/g, '') || ''
  const isSelected = selectedIri === iri
  const obsolete = isObsolete(node)

  async function loadChildren(): Promise<ClassResult[]> {
    if (children !== null) return children
    setLoading(true)
    try {
      const q = iri.startsWith('http') ? `<${iri}>` : iri
      const data = await dlQuery(q, 'subclass', ontologyId, true)
      const sorted = sortChildren(data.result || [])
      setChildren(sorted)
      return sorted
    } catch {
      setChildren([])
      return []
    } finally {
      setLoading(false)
    }
  }

  async function toggle() {
    if (open) { setOpen(false); return }
    await loadChildren()
    setOpen(true)
  }

  // Auto-expand along a deep-link path
  useEffect(() => {
    if (open || !expandPath || !expandPath.has(iri)) return
    loadChildren().then(() => setOpen(true))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandPath])

  const isLeaf = children !== null && children.length === 0

  function handleClick(e: React.MouseEvent) {
    if (onSelect) {
      e.preventDefault()
      onSelect(iri)
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLElement>) {
    const el = e.currentTarget
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); moveFocus(el, 1); break
      case 'ArrowUp': e.preventDefault(); moveFocus(el, -1); break
      case 'ArrowRight': e.preventDefault(); if (!open && !isLeaf) toggle(); break
      case 'ArrowLeft': e.preventDefault(); if (open) setOpen(false); break
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (onSelect) onSelect(iri)
        break
    }
  }

  const labelClasses = obsolete
    ? `text-sm text-left truncate leading-tight line-through text-gray-400 italic outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 rounded ${
        isSelected ? 'font-semibold bg-red-100 px-1 -mx-1' : 'hover:text-gray-500'
      }`
    : `text-sm text-left truncate leading-tight transition-colors outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 rounded ${
        isSelected
          ? 'text-indigo-700 font-semibold bg-indigo-100 px-1 -mx-1'
          : 'text-gray-700 hover:text-indigo-700'
      }`

  const inner = (
    <>
      {obsolete && <span className="text-[9px] text-red-400 font-normal not-italic mr-1">obsolete</span>}
      {lbl || iri}
    </>
  )

  const labelEl = onSelect ? (
    <button data-tree-focusable onClick={handleClick} onKeyDown={onKeyDown}
      className={labelClasses} title={iri} tabIndex={0}>
      {inner}
    </button>
  ) : (
    <Link data-tree-focusable to={`/ontology/${ontologyId}/class/${encodeURIComponent(iri)}`}
      onKeyDown={onKeyDown} className={labelClasses} title={iri} tabIndex={0}>
      {inner}
    </Link>
  )

  return (
    <div
      role="treeitem"
      aria-expanded={isLeaf ? undefined : open}
      aria-selected={isSelected}
      aria-level={depth + 1}
      className={depth > 0 ? 'ml-4 border-l border-gray-200' : ''}
    >
      <div className={`flex items-center gap-1 py-[3px] pl-2 group rounded-r transition-colors ${
        isSelected ? (obsolete ? 'bg-red-50' : 'bg-indigo-50') : 'hover:bg-gray-50'
      }`}>
        <button
          onClick={toggle}
          aria-label={open ? 'Collapse' : 'Expand'}
          tabIndex={-1}
          className={`w-5 h-5 flex items-center justify-center rounded text-xs shrink-0 transition-colors ${
            isLeaf ? 'text-gray-300' : obsolete ? 'text-gray-300 hover:text-gray-500' : 'text-gray-400 hover:text-indigo-600 hover:bg-indigo-100'
          }`}
          disabled={isLeaf}
        >
          {loading ? (
            <svg className="w-3 h-3 animate-spin text-indigo-500" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          ) : isLeaf ? (
            <span className="text-[8px]">&#9679;</span>
          ) : open ? '▾' : '▸'}
        </button>
        {labelEl}
      </div>
      {open && children && children.length > 0 && (
        <div role="group">
          {children.map((c, i) => (
            <TreeNode
              key={c.class || i}
              node={c}
              ontologyId={ontologyId}
              depth={depth + 1}
              onSelect={onSelect}
              selectedIri={selectedIri}
              expandPath={expandPath}
            />
          ))}
        </div>
      )}
    </div>
  )
}
