// An Api for SPARQL query rewriting.

import groovy.json.*
import src.util.Util
import groovyx.gpars.GParsPool
import src.NewShortFormProvider
import org.semanticweb.owlapi.expression.ShortFormEntityChecker
import org.semanticweb.owlapi.util.BidirectionalShortFormProviderAdapter
import org.semanticweb.owlapi.model.OWLClassExpression


if(!application) {
    application = request.getApplication(true)
}

def params = Util.extractParams(request)
// print(params)
def query = params.query
def userEndpoint = params.endpoint
def manager = application.manager

try {
    if (query == null || query.trim().isEmpty()) {
        response.setStatus(400)
        print new JsonBuilder([ 'error': true, 'message': 'Query parameter is missing or empty.' ]).toString()
        return
    }
    def expandedQuery = query
    def owlPattern = /(?s)OWL\s+([a-zA-Z_]+)\s*(?:<[^>]*>)?\s*(?:<[^>]*>)?\s*\{(.*?)\}/
    def matcher = query =~ owlPattern

    if (matcher) {
        def fullMatch = matcher[0][0]
        def type = matcher[0][1]
        def dlQuery = matcher[0][2].trim()

        def ont = manager.getOntology()
        def sfp = new NewShortFormProvider(ont.getImportsClosure())
        def bidiSfp = new BidirectionalShortFormProviderAdapter(ont.getImportsClosure(), sfp)
        def checker = new ShortFormEntityChecker(bidiSfp)
        def df = ont.getOWLOntologyManager().getOWLDataFactory()
        def configSupplier = { -> ont.getOWLOntologyManager().getOntologyLoaderConfiguration() }
        def parser = new org.semanticweb.owlapi.manchestersyntax.parser.ManchesterOWLSyntaxParserImpl(configSupplier, df)
        parser.setStringToParse(dlQuery)
        parser.setOWLEntityChecker(checker)
        def expression = parser.parseClassExpression()
        
        def direct = true
        def labels = false
        def axioms = false
        def out = manager.runQuery(expression, type, direct, labels, axioms)

        def iris = out.collect { "<${it.owlClass}>" }.join("\n    ")
        expandedQuery = query.replace(fullMatch, iris)
    }
    
    def endpoint = userEndpoint
    if (endpoint == null || endpoint.isEmpty() || endpoint == "local" || endpoint.startsWith('/virtuoso/sparql') || endpoint.contains("localhost")){
	    endpoint = "http://virtuoso:8890/sparql"
    }
    def response
    def connection = new URL(endpoint).openConnection() as HttpURLConnection
    connection.setRequestMethod("POST")
    connection.setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
    connection.setRequestProperty("Accept", "application/sparql-results+json")
    connection.setDoOutput(true)
    
    def queryParams = "query=" + URLEncoder.encode(expandedQuery, "UTF-8")
    
    connection.getOutputStream().write(queryParams.getBytes("UTF-8"))

    def responseCode = connection.getResponseCode()
    if (responseCode == HttpURLConnection.HTTP_OK) {
        def reader = new BufferedReader(new InputStreamReader(connection.getInputStream()))
        def responseBody = reader.text
        
        if (responseBody) {
            // Parse the JSON response
            def jsonSlurper = new JsonSlurper()
            response = jsonSlurper.parseText(responseBody)
        } else {
            response = [
                error: true,
                statusCode: responseCode,
                message: "Empty response body"
            ]
        }
    } else {
        response = [
            error: true,
            statusCode: responseCode,
            message: connection.getResponseMessage()
        ]
    }

    // Disconnect
    connection.disconnect()

    print new JsonBuilder(response).toString() 

} catch(java.lang.IllegalArgumentException e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'sparql.groovy: Invalid Sparql query' ]).toString() 
} catch(org.semanticweb.owlapi.manchestersyntax.parser.ManchesterOWLSyntaxParserException e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'Query parsing error: ' + e.getMessage() ]).toString() 
} catch(RuntimeException e) {
    response.setStatus(400)
    e.printStackTrace();
    print new JsonBuilder([ 'error': true, 'message': e.getMessage() ]).toString() 
}catch(Exception e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'Generic query error: ' + e.getMessage() ]).toString() 
}

