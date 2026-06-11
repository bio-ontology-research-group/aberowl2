import { useEffect } from 'react'

const SUFFIX = 'AberOWL'

/** Set document.title to `parts... — AberOWL`, restoring nothing (SPA-wide). */
export function useDocumentTitle(...parts: Array<string | null | undefined>) {
  const title = [...parts.filter(Boolean), SUFFIX].join(' — ')
  useEffect(() => {
    document.title = title
  }, [title])
}
