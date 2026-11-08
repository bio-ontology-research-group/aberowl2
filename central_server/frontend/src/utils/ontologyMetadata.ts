/** Catalogue names identify the whole ontology; source titles may name a module. */
export function ontologyDisplayTitle(ont: { name?: string; title?: string; ontology?: string } | null, id?: string): string {
  return ont?.name?.trim() || ont?.title?.trim() || ont?.ontology?.toUpperCase() || id?.toUpperCase() || ''
}

/** Extract readable text in an inert template; never mount provider HTML. */
export function ontologyDescriptionText(value?: string): string {
  if (!value) return ''
  const template = document.createElement('template')
  template.innerHTML = value
  template.content.querySelectorAll('script, style, iframe, object, template').forEach(node => node.remove())
  template.content.querySelectorAll('br, p, div, li, h1, h2, h3, h4, h5, h6, tr').forEach(node => {
    node.before(document.createTextNode(' '))
    node.after(document.createTextNode(' '))
  })
  return (template.content.textContent || '').replace(/\s+/g, ' ').trim()
}
