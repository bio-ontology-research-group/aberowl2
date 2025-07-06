// Run a query and ting

import groovy.json.*
import src.util.Util
import groovyx.gpars.GParsPool
import src.AberowlManchesterOwlParser
import src.NewShortFormProvider
import org.semanticweb.owlapi.model.OWLClassExpression

if(!application) {
    application = request.getApplication(true)
}

def params = Util.extractParams(request)

def query = params.query
def type = params.type
def direct = params.direct
def labels = params.labels
def axioms = params.axioms
def ontology = params.ontology
def manager = application.manager

if (type == null) {
    type = "all"
}

direct = true; //(direct.equals("true")) ? true : false;
labels = (labels.equals("true")) ? true : false;
axioms = (axioms.equals("true")) ? true : false;

response.contentType = 'application/json'

try {
    def results = new HashMap()
    def start = System.currentTimeMillis()
    def out
    if (query.startsWith("http") || query.startsWith("<")) {
        out = manager.runQuery(query, type, direct, labels, axioms)
    } else {
        def sfp = new NewShortFormProvider(manager.getOntology().getImportsClosure())
        def parser = new AberowlManchesterOwlParser(manager.getOntology(), sfp)
        def expression = parser.parse(query)
        out = manager.runQuery(expression, type, direct, labels, axioms)
    }
    def end = System.currentTimeMillis()
    results.put('time', (end - start))
    results.put('result', out)
    print new JsonBuilder(results).toString()
} catch(java.lang.IllegalArgumentException e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'Ontology not found.' ]).toString() 
} catch(org.semanticweb.owlapi.manchestersyntax.renderer.ParserException e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'Query parsing error: ' + e.getMessage() ]).toString() 
} catch(Exception e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'Generic query error: ' + e.getMessage() ]).toString() 
}

