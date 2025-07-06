import groovy.json.JsonOutput
import aberowlapi.src.OntologyStatsCalculator

// This script assumes 'ontology' and 'manager' are available in the binding.

def calculator = new OntologyStatsCalculator(ontology, manager)
def stats = calculator.calculateStats()

// === Output JSON ===
response.setContentType("application/json")
out << JsonOutput.toJson(stats)
