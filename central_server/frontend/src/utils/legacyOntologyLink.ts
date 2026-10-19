/** Convert classic AberOWL #!class-IRI links without changing the IRI itself. */
export function legacyOntologySearch(search: string, hash: string): string | null {
  if (!hash.startsWith('#!')) return null
  let iri = hash.slice(2)
  // Only decode an encoded whole IRI; percent escapes inside raw IRIs are data.
  if (/^(?:https?|urn)%3a/i.test(iri)) {
    try { iri = decodeURIComponent(iri) } catch { return null }
  }
  if (!/^(?:https?:\/\/|urn:)/i.test(iri)) return null
  const params = new URLSearchParams(search)
  if (!params.has('class')) {
    params.set('class', iri)
    params.set('tab', 'browse')
  }
  return `?${params.toString()}`
}
