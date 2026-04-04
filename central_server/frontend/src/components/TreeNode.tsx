import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { ClassResult } from '../api/types'
import { dlQuery } from '../api/client'

interface Props {
  node: ClassResult
  ontologyId: string
}

export default function TreeNode({ node, ontologyId }: Props) {
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
        const data = await dlQuery(q, 'subclass', ontologyId)
        setChildren(data.result || [])
      } catch { setChildren([]) }
      setLoading(false)
    }
    setOpen(true)
  }

  return (
    <div className="ml-4">
      <div className="flex items-center gap-1 py-0.5">
        <button onClick={toggle} className="w-4 h-4 text-xs text-gray-400 hover:text-gray-700 shrink-0">
          {loading ? '...' : open ? '▼' : '▶'}
        </button>
        <Link
          to={`/ontology/${ontologyId}/class/${encodeURIComponent(iri)}`}
          className="text-sm text-gray-800 hover:text-indigo-700 hover:underline truncate"
        >
          {lbl || iri}
        </Link>
      </div>
      {open && children && children.length > 0 && (
        <div>
          {children.map((c, i) => (
            <TreeNode key={c.class || i} node={c} ontologyId={ontologyId} />
          ))}
        </div>
      )}
      {open && children && children.length === 0 && (
        <div className="ml-5 text-xs text-gray-400 italic">No subclasses</div>
      )}
    </div>
  )
}
