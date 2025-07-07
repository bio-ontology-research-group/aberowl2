import groovy.json.*
import src.util.Util

if(!application) {
    application = request.getApplication(true)
}

def params = Util.extractParams(request)

def query = params.query
def endpoint = params.endpoint ?: "/virtuoso/"
def manager = application.manager

response.contentType = 'application/json'

try {
    def rewrittenQuery = query
    def matcher = query =~ /OWL\s+(\w+)\s+<([^>]+)>\s+<([^>]*)>\s*\{\s*(.*?)\s*\}/
    if (matcher.find()) {
        def type = matcher.group(1)
        def ontology = matcher.group(2)
        def base = matcher.group(3)
        def dlQuery = matcher.group(4)

        def owlResults = manager.runQuery(dlQuery, type, true, true, false)
        def iriList = owlResults.collect { "<${it}>" }.join("\n")
        rewrittenQuery = query.replaceFirst(/OWL\s+(\w+)\s+<([^>]+)>\s+<([^>]*)>\s*\{\s*(.*?)\s*\}/, "VALUES ?ontid { \n${iriList}\n}")
    }
    
    // Use the provided endpoint or default to /virtuoso/ if not specified
    def endpointUrl = endpoint.startsWith("http") ? endpoint : request.getRequestURL().toString().replaceFirst(/\/api\/runSparqlQuery\.groovy$/, endpoint)

    def http = new URL(endpoint).openConnection() as HttpURLConnection
    http.setRequestMethod('POST')
    http.setDoOutput(true)
    http.setRequestProperty('Content-Type', 'application/sparql-query')
    http.setRequestProperty('Accept', 'application/sparql-results+json')
    
    def writer = new OutputStreamWriter(http.getOutputStream())
    writer.write(rewrittenQuery)
    writer.flush()
    writer.close()
    http.connect()

    def results = new JsonSlurper().parse(http.getInputStream())
    print new JsonBuilder(results).toString()
    
} catch(Exception e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'SPARQL query error: ' + e.getMessage() ]).toString() 
}
