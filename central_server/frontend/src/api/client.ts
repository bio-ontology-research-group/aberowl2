import type { OntologySummary, OntologyDetail, ClassResult, StatsAggregate, StatsSingle } from './types'

const BASE = ''  // same origin in production; Vite proxy in dev

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(path, window.location.origin)
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v))
  const r = await fetch(url.toString())
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export async function listOntologies(): Promise<OntologySummary[]> {
  const data = await get<{ result: OntologySummary[] }>('/api/listOntologies')
  return data.result
}

export async function getOntology(id: string): Promise<OntologyDetail> {
  return get<OntologyDetail>('/api/getOntology', { ontology: id })
}

export async function searchClasses(query: string, ontology?: string, size = 100): Promise<ClassResult[]> {
  const params: Record<string, string> = { query, size: String(size) }
  if (ontology) params.ontologies = ontology
  const data = await get<{ result: ClassResult[] }>('/api/search_all', params)
  return data.result
}

export async function queryNames(term: string, ontology?: string, prefix = false): Promise<ClassResult[]> {
  const params: Record<string, string> = { term }
  if (ontology) params.ontology = ontology
  if (prefix) params.prefix = 'true'
  const data = await get<{ result: ClassResult[] }>('/api/queryNames', params)
  return data.result
}

export async function dlQuery(query: string, type: string, ontologies?: string, direct = false): Promise<{ time: number; result: ClassResult[] }> {
  const params: Record<string, string> = { query, type, labels: 'true' }
  if (ontologies) params.ontologies = ontologies
  if (direct) params.direct = 'true'
  return get('/api/dlquery_all', params)
}

export async function getClass(classIri: string, ontology: string): Promise<ClassResult> {
  return get('/api/getClass', { query: classIri, ontology })
}

export async function getStats(): Promise<StatsAggregate> {
  return get('/api/getStats')
}

export async function getOntologyStats(ontology: string): Promise<StatsSingle> {
  return get('/api/getStats', { ontology })
}

export async function queryOntologies(term: string): Promise<{ result: unknown[] }> {
  return get('/api/queryOntologies', { term })
}

export async function runSparql(query: string, endpoint?: string) {
  return post('/api/sparql', { query, endpoint })
}
