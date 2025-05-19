// An Api for SPARQL query rewriting.

import groovy.json.*
import src.util.Util
import groovyx.gpars.GParsPool
import src.AberowlManchesterOwlQueryEngine;


if(!application) {
    application = request.getApplication(true)
}

def queryEngine = new AberowlManchesterOwlQueryEngine();
def params = Util.extractParams(request)
print(params)
def query = params.query
def manager = application.manager

def response_dbg = queryEngine.expandAndExecQuery(manager, query)
// print new JsonBuilder(response).toString() 

try {
    def data = queryEngine.expandAndExecQuery(manager, query)
    // throw new RuntimeException("sparql.groovy:"+ data.query + " || " + data.endpoint)
    def expandedQuery = data.query
    def endpoint = data.endpoint

    if (endpoint == null || endpoint.isEmpty()){
	endpoint = "http://localhost:88/virtuoso"
    }
    def response
    endpoint = "http://localhost:88/virtuoso/"
    def connection = new URL(endpoint).openConnection() as HttpURLConnection
    connection.setRequestMethod("POST")
    connection.setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
    connection.setRequestProperty("Accept", "application/sparql-results+json")
    connection.setDoOutput(true)
    
    def queryParams = "query=" + URLEncoder.encode(expandedQuery, "UTF-8")
    
    connection.getOutputStream().write(queryParams.getBytes("UTF-8"))
    throw new RuntimeException("sparql.groovy: "+ expandedQuery + " || " + endpoint)
    def responseCode = connection.getResponseCode()
    if (responseCode == HttpURLConnection.HTTP_OK) {
	def reader = new BufferedReader(new InputStreamReader(connection.getInputStream()))
	def responseBody = reader.text
    
	// Parse the JSON response
	def jsonSlurper = new JsonSlurper()
	response = jsonSlurper.parseText(responseBody)
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
} catch(org.semanticweb.owlapi.manchestersyntax.renderer.ParserException e) {
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

