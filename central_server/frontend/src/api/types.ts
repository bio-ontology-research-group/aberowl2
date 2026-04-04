export interface OntologySummary {
  id: string
  title: string
  status: string
}

export interface OntologyDetail {
  ontology: string
  title: string
  description: string
  url: string
  status: string
  class_count: number
  property_count: number
  object_property_count: number
  data_property_count: number
  individual_count: number
  version_info: string
  license: string
  home_page: string
}

export interface ClassResult {
  class: string
  owlClass: string
  label: string | string[]
  ontology: string
  ontology_title?: string
  definition?: string | string[]
  synonyms?: string[]
  oboid?: string
  deprecated?: boolean
}

export interface DLQueryResult {
  time: number
  result: ClassResult[]
}

export interface StatsAggregate {
  total_ontologies: number
  online_ontologies: number
  total_classes: number
  total_properties: number
}

export interface StatsSingle {
  ontology: string
  class_count: number
  property_count: number
  object_property_count: number
  individual_count: number
  status: string
}

export interface SPARQLResult {
  results: {
    head: { vars: string[] }
    results: { bindings: Record<string, { type: string; value: string }>[] }
  }
  expanded_query?: string
  expansions?: { pattern: string; variable: string; ontology: string; dl_query: string; type: string; result_count: number }[]
}
