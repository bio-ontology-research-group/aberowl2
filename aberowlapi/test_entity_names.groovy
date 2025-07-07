@Grab('org.slf4j:slf4j-api:1.7.36')
@Grab('org.slf4j:slf4j-simple:1.7.36')
@Grab('net.sourceforge.owlapi:owlapi-distribution:4.5.26')

// Test script for entity names with spaces
import org.semanticweb.owlapi.apibinding.OWLManager
import org.semanticweb.owlapi.model.*
import src.QueryParser
import src.NewShortFormProvider

// Load the Pizza ontology
def manager = OWLManager.createOWLOntologyManager()
def pizza = manager.loadOntologyFromOntologyDocument(new File("../data/pizza.owl"))
def go = manager.loadOntologyFromOntologyDocument(new File("../data/go.owl"))
// Create a short form provider
def shortFormProvider_pizza = new NewShortFormProvider(pizza.getImportsClosure())
def shortFormProvider_go = new NewShortFormProvider(go.getImportsClosure())

// Create a query parser
def queryParserPizza = new QueryParser(pizza, shortFormProvider_pizza)
def queryParserGO = new QueryParser(go, shortFormProvider_go)

// Test cases
def testCasesPizza = [
    "Domain Thing",
    "Interesting Pizza",
    "Pizza",
]

def testCasesGO = [
    "biological_process"
]

// Run tests
println "Testing entity name parsing:"
println "============================"

testCasesPizza.each { testCase ->
    println "\nTesting: '$testCase'"
    try {
        def result = queryParserPizza.parse(testCase, true)
        println "SUCCESS: Parsed as ${result}"
    } catch (Exception e) {
        println "FAILED: ${e.getMessage()}"
    }
}

testCasesGO.each { testCase ->
    println "\nTesting: '$testCase'"
    try {
        def result = queryParserGO.parse(testCase, true)
        println "SUCCESS: Parsed as ${result}"
    } catch (Exception e) {
        println "FAILED: ${e.getMessage()}"
    }
}
