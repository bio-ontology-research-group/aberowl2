import groovy.json.*
import src.util.Util
import java.net.URLEncoder

if(!application) {
    application = request.getApplication(true)
}

def params = [:]
if (request != null) {
    params = Util.extractParams(request)
}

def query = params.query
// Prefer central Virtuoso if configured; fall back to the per-ontology instance.
def centralVirtuosoUrl = System.getenv("CENTRAL_VIRTUOSO_URL")
def defaultEndpoint = centralVirtuosoUrl ? "${centralVirtuosoUrl.replaceAll('/+$', '')}/sparql/" : "http://virtuoso:8890/sparql/"
def endpoint = params.endpoint ?: defaultEndpoint
// Get the original host as seen by the user (before nginx proxy)
def userHost = request.getHeader('X-Forwarded-Host') ?: request.getHeader('Host')

// Security: Prevent SSRF by validating the endpoint.
// We only allow internal Virtuoso or the central Virtuoso instance.
def allowedEndpoints = [defaultEndpoint, "http://virtuoso:8890/sparql/", "http://localhost:8890/sparql/"].unique()
if (endpoint && !allowedEndpoints.contains(endpoint)) {
    endpoint = defaultEndpoint
}
def manager = application.getAttribute("manager")

response.contentType = 'application/json'

try {
    def rewrittenQuery = query
    def valuesOwlPattern = /VALUES\s+(\?\w+)\s+\{\s*OWL\s+(\w+)\s+\{\s*(.*?)\s*\}\s*\}/
    def matcher = query =~ valuesOwlPattern
    if (matcher.find()) {
	def variableName = matcher.group(1)  // e.g., "?class"
	def type = matcher.group(2)          // e.g., "SomeType"
	def dlQuery = matcher.group(3)       // e.g., "some_dl_query"
	def owlResults = manager.runQuery(dlQuery, type, true, true, false)
	//	println "DL Query results: ${owlResults}"
	def iriList = owlResults.collect { "<${it.class}>" }.join("\n")
	rewrittenQuery = query.replaceFirst(valuesOwlPattern, "VALUES ${variableName} { \n${iriList}\n}")
    }
    def endpointUrl = endpoint

    def queryParams = "query=" + URLEncoder.encode(rewrittenQuery, "UTF-8") + "&format=application%2Fsparql-results%2Bjson&default-graph-uri="
    def fullUrl = new URL(endpointUrl + "?" + queryParams)
//    println "DEBUG: Sending SPARQL query to endpoint ${fullUrl}"

    def http = fullUrl.openConnection() as HttpURLConnection
    http.setRequestMethod('GET')
    //http.setRequestProperty('Accept', 'application/sparql-results+json')

    def responseCode = http.responseCode
//    println "DEBUG: SPARQL endpoint responded with HTTP ${responseCode}"
    
    if (responseCode >= 200 && responseCode < 300) {
        def responseText = http.inputStream.text
//        println "DEBUG: Response text: ${responseText}"
        
        if (!responseText?.trim()) {
            // Handle empty response
            response.setStatus(500)
            print new JsonBuilder([ 'error': true, 'message': 'SPARQL endpoint returned an empty response' ]).toString()
        } else {
            try {
                def results = new JsonSlurper().parseText(responseText)
                print new JsonBuilder(results).toString()
            } catch (Exception e) {
                // Handle JSON parsing errors
                response.setStatus(500)
                print new JsonBuilder([ 'error': true, 'message': 'Error parsing SPARQL response: ' + e.getMessage() ]).toString()
            }
        }
    } else {
        def errorText = http.errorStream?.text ?: "No error message from server."
        response.setStatus(500)
        print new JsonBuilder([ 'error': true, 'message': "SPARQL endpoint returned HTTP ${responseCode}. Message: ${errorText}" ]).toString()
    }
    
} catch(Exception e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'SPARQL query error: ' + e.getMessage() ]).toString() 
}
