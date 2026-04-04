import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { ClassResult } from '../api/types'
import { dlQuery } from '../api/client'

interface Props {
  node: ClassResult
  ontologyId: string
  depth?: number
}

export default function TreeNode({ node, ontologyId, depth = 0 }: Props) {
  const [children, setChildren] = useState<ClassResult[] | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)

  const lbl = Array.isArray(node.label) ? node.label[0] : node.label
  const iri = node.class || node.owlClass?.replace(/[<>]/g, '') || ''

  async function toggle() {
    if (open) { setOpen(false); return }
    if (children === null) {
      setLoading(true)
      try {
        const q = iri.startsWith('http') ? `<${iri}>` : iri
        const data = await dlQuery(q, 'subclass', ontologyId, true)
        setChildren(data.result || [])
      } catch { setChildren([]) }
      setLoading(false)
    }
    setOpen(true)
  }

  const isLeaf = children !== null && children.length === 0

  return (
    <div className={depth > 0 ? 'ml-5 border-l border-gray-200' : ''}>
      <div className="flex items-center gap-1 py-[3px] pl-2 group hover:bg-indigo-50/50 rounded-r transition-colors">
        <button
          onClick={toggle}
          className={`w-5 h-5 flex items-center justify-center rounded text-xs shrink-0 transition-colors ${
            isLeaf ? 'text-gray-300' : 'text-gray-400 hover:text-indigo-600 hover:bg-indigo-100'
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
        <Link
          to={`/ontology/${ontologyId}/class/${encodeURIComponent(iri)}`}
          className="text-sm text-gray-700 hover:text-indigo-700 truncate leading-tight"
          title={iri}
        >
          {lbl || iri}
        </Link>
      </div>
      {open && children && children.length > 0 && (
        <div>
          {children.map((c, i) => (
            <TreeNode key={c.class || i} node={c} ontologyId={ontologyId} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}
