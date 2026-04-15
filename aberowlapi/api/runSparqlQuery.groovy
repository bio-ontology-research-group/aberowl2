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
def ontologyId = params.ontologyId ?: params.ontology
// Prefer central Virtuoso if configured; fall back to the per-ontology instance.
def centralVirtuosoUrl = System.getenv("CENTRAL_VIRTUOSO_URL")
def defaultEndpoint = centralVirtuosoUrl ? "${centralVirtuosoUrl.replaceAll('/+$', '')}/sparql/" : "http://virtuoso:8890/sparql/"
def endpoint = params.endpoint ?: defaultEndpoint
// Get the original host as seen by the user (before nginx proxy)
def userHost = request.getHeader('X-Forwarded-Host') ?: request.getHeader('Host')

// Security: Prevent SSRF by validating the endpoint.
def allowedEndpoints = [defaultEndpoint, "http://virtuoso:8890/sparql/", "http://localhost:8890/sparql/"].unique()
if (endpoint && !allowedEndpoints.contains(endpoint)) {
    endpoint = defaultEndpoint
}
def manager = application.getAttribute("manager")

response.contentType = 'application/json'

// Resolve ontologyId for DL query expansion
if (!ontologyId && manager && manager.ontologies.size() == 1) {
    ontologyId = manager.getDefaultOntologyId()
}

try {
    def rewrittenQuery = query

    // Pattern 1: VALUES ?var { OWL type { dl_query } }
    def valuesOwlPattern = /VALUES\s+(\?\w+)\s+\{\s*OWL\s+(\w+)\s+\{\s*(.*?)\s*\}\s*\}/
    def matcher = query =~ valuesOwlPattern
    if (matcher.find()) {
        if (!ontologyId || !manager?.hasOntology(ontologyId)) {
            response.setStatus(400)
            print new JsonBuilder([ 'error': true, 'message': 'ontologyId required for OWL query expansion' ]).toString()
            return
        }
        def variableName = matcher.group(1)
        def type = matcher.group(2)
        def dlQuery = matcher.group(3)
        def owlResults = manager.runQuery(ontologyId, dlQuery, type, true, true, false)
        def iriList = owlResults.collect { "<${it.class}>" }.join("\n")
        rewrittenQuery = query.replaceFirst(valuesOwlPattern, "VALUES ${variableName} { \n${iriList}\n}")
    }

    // Pattern 2: FILTER OWL(?var, type, "dl_query")
    def filterOwlPattern = /FILTER\s+OWL\(\s*(\?\w+)\s*,\s*(\w+)\s*,\s*["'](.+?)["']\s*\)/
    def filterMatcher = rewrittenQuery =~ filterOwlPattern
    if (filterMatcher.find()) {
        if (!ontologyId || !manager?.hasOntology(ontologyId)) {
            response.setStatus(400)
            print new JsonBuilder([ 'error': true, 'message': 'ontologyId required for OWL query expansion' ]).toString()
            return
        }
        def variable = filterMatcher.group(1)
        def type = filterMatcher.group(2)
        def dlQuery = filterMatcher.group(3)
        def owlResults = manager.runQuery(ontologyId, dlQuery, type, true, true, false)
        def iriList = owlResults.collect { "<${it.class}>" }.join(", ")
        rewrittenQuery = rewrittenQuery.replaceFirst(filterOwlPattern,
            "FILTER (${variable} IN (${iriList}))")
    }

    def endpointUrl = endpoint
    def queryParams = "query=" + URLEncoder.encode(rewrittenQuery, "UTF-8") + "&format=application%2Fsparql-results%2Bjson&default-graph-uri="
    def fullUrl = new URL(endpointUrl + "?" + queryParams)

    def http = fullUrl.openConnection() as HttpURLConnection
    http.setRequestMethod('GET')

    def responseCode = http.responseCode

    if (responseCode >= 200 && responseCode < 300) {
        def responseText = http.inputStream.text

        if (!responseText?.trim()) {
            response.setStatus(500)
            print new JsonBuilder([ 'error': true, 'message': 'SPARQL endpoint returned an empty response' ]).toString()
        } else {
            try {
                def results = new JsonSlurper().parseText(responseText)
                print new JsonBuilder(results).toString()
            } catch (Exception e) {
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
