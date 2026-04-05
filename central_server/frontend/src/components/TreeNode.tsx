import { useState } from 'react'
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

export default function TreeNode({ node, ontologyId, depth = 0, onSelect, selectedIri }: Props) {
  const [children, setChildren] = useState<ClassResult[] | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)

  const lbl = Array.isArray(node.label) ? node.label[0] : node.label
  const iri = node.class || node.owlClass?.replace(/[<>]/g, '') || ''
  const isSelected = selectedIri === iri
  const obsolete = isObsolete(node)

  async function toggle() {
    if (open) { setOpen(false); return }
    if (children === null) {
      setLoading(true)
      try {
        const q = iri.startsWith('http') ? `<${iri}>` : iri
        const data = await dlQuery(q, 'subclass', ontologyId, true)
        setChildren(sortChildren(data.result || []))
      } catch { setChildren([]) }
      setLoading(false)
    }
    setOpen(true)
  }

  const isLeaf = children !== null && children.length === 0

  function handleClick(e: React.MouseEvent) {
    if (onSelect) {
      e.preventDefault()
      onSelect(iri)
    }
  }

  const labelClasses = obsolete
    ? `text-sm text-left truncate leading-tight line-through text-gray-400 italic ${
        isSelected ? 'font-semibold bg-red-100 px-1 -mx-1 rounded' : 'hover:text-gray-500'
      }`
    : `text-sm text-left truncate leading-tight transition-colors ${
        isSelected
          ? 'text-indigo-700 font-semibold bg-indigo-100 px-1 -mx-1 rounded'
          : 'text-gray-700 hover:text-indigo-700'
      }`

  const labelEl = onSelect ? (
    <button onClick={handleClick} className={labelClasses} title={iri}>
      {obsolete && <span className="text-[9px] text-red-400 font-normal not-italic mr-1">obsolete</span>}
      {lbl || iri}
    </button>
  ) : (
    <Link
      to={`/ontology/${ontologyId}/class/${encodeURIComponent(iri)}`}
      className={labelClasses}
      title={iri}
    >
      {obsolete && <span className="text-[9px] text-red-400 font-normal not-italic mr-1">obsolete</span>}
      {lbl || iri}
    </Link>
  )

  return (
    <div className={depth > 0 ? 'ml-4 border-l border-gray-200' : ''}>
      <div className={`flex items-center gap-1 py-[3px] pl-2 group rounded-r transition-colors ${
        isSelected ? (obsolete ? 'bg-red-50' : 'bg-indigo-50') : 'hover:bg-gray-50'
      }`}>
        <button
          onClick={toggle}
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
        <div>
          {children.map((c, i) => (
            <TreeNode
              key={c.class || i}
              node={c}
              ontologyId={ontologyId}
              depth={depth + 1}
              onSelect={onSelect}
              selectedIri={selectedIri}
            />
          ))}
        </div>
      )}
    </div>
  )
}
