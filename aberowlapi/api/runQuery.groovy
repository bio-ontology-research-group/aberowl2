// Run a DL query against a specific ontology

import groovy.json.*
import src.util.Util

if(!application) {
    application = request.getApplication(true)
}

def params = Util.extractParams(request)

def query = params.query
def type = params.type
def direct = params.direct
def labels = params.labels
def axioms = params.axioms
def ontologyId = params.ontologyId ?: params.ontology
def ontologyIds = params.ontologyIds  // comma-separated list, optional
def shortform = params.shortform
def manager = application.getAttribute("manager")

if (type == null) {
    type = "all"
}

direct = (direct != null && direct.equals("true")) ? true : false;
labels = (labels != null && labels.equals("true")) ? true : false;
axioms = (axioms != null && axioms.equals("true")) ? true : false;

response.contentType = 'application/json'

if (manager == null) {
    response.setStatus(503)
    print new JsonBuilder([ 'error': true, 'message': 'Manager not available.' ]).toString()
    return
}

// Multi-ontology branch: ontologyIds=a,b,c — run query against each in
// parallel inside this worker, aggregate. Lets the central server send
// one HTTP call per worker URL instead of one per ontology.
if (ontologyIds) {
    def ids = ontologyIds.split(',').collect { it.trim() }.findAll { it }
    try {
        def results = new HashMap()
        def start = System.currentTimeMillis()
        def out = manager.runQueryMulti(ids, query, type, direct, labels, axioms, shortform)
        def end = System.currentTimeMillis()
        results.put('time', (end - start))
        results.put('result', out)
        print new JsonBuilder(results).toString()
    } catch(org.semanticweb.owlapi.manchestersyntax.parser.ManchesterOWLSyntaxParserException e) {
        response.setStatus(400)
        print new JsonBuilder([ 'error': true, 'message': 'Query parsing error: ' + e.getMessage() ]).toString()
    } catch(Exception e) {
        response.setStatus(400)
        print new JsonBuilder([ 'error': true, 'message': 'Generic query error: ' + e.getMessage() ]).toString()
    }
    return
}

// Resolve ontologyId: use param, fall back to default
if (!ontologyId && manager.ontologies.size() == 1) {
    ontologyId = manager.getDefaultOntologyId()
} else if (!ontologyId) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'ontologyId parameter required (multiple ontologies loaded).' ]).toString()
    return
}

if (!manager.hasOntology(ontologyId)) {
    response.setStatus(404)
    print new JsonBuilder([ 'error': true, 'message': "Ontology not found: ${ontologyId}" ]).toString()
    return
}

try {
    def results = new HashMap()
    def start = System.currentTimeMillis()
    def out = manager.runQuery(ontologyId, query, type, direct, labels, axioms, shortform)
    def end = System.currentTimeMillis()
    results.put('time', (end - start))
    results.put('result', out)
    print new JsonBuilder(results).toString()
} catch(java.lang.IllegalArgumentException e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'Ontology not found.' ]).toString()
} catch(org.semanticweb.owlapi.manchestersyntax.parser.ManchesterOWLSyntaxParserException e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'Query parsing error: ' + e.getMessage() ]).toString()
} catch(Exception e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'Generic query error: ' + e.getMessage() ]).toString()
}
