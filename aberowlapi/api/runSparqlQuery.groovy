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
    def owlPattern = /OWL\s+(\w+)\s+\{\s*(.*?)\s*\}/
    def matcher = query =~ owlPattern
    if (matcher.find()) {
        def type = matcher.group(1)
        def dlQuery = matcher.group(2)

        def owlResults = manager.runQuery(dlQuery, type, true, true, false)
        def iriList = owlResults.collect { "<${it}>" }.join("\n")
        rewrittenQuery = query.replaceFirst(owlPattern, "VALUES ?ontid { \n${iriList}\n}")
    }
    
    // Use the provided endpoint or default to /virtuoso/ if not specified
    def endpointUrl = endpoint.startsWith("http") ? endpoint : request.getRequestURL().toString().replaceFirst(/\/api\/runSparqlQuery\.groovy$/, endpoint)

    def http = new URL(endpointUrl).openConnection() as HttpURLConnection
    http.setRequestMethod('POST')
    http.setDoOutput(true)
    http.setRequestProperty('Content-Type', 'application/sparql-query')
    http.setRequestProperty('Accept', 'application/sparql-results+json')
    
    def writer = new OutputStreamWriter(http.getOutputStream())
    writer.write(rewrittenQuery)
    writer.flush()
    writer.close()
    http.connect()

    def responseCode = http.responseCode
    if (responseCode >= 200 && responseCode < 300) {
        def responseText = http.inputStream.text
        if (responseText.trim().isEmpty()) {
            // Handle empty response as empty result set
            print new JsonBuilder([head: [vars:[]], results: [bindings:[]]]).toString()
        } else {
            def results = new JsonSlurper().parseText(responseText)
            print new JsonBuilder(results).toString()
        }
    } else {
        def errorText = http.errorStream?.text ?: "No error message from server."
        throw new Exception("SPARQL endpoint returned HTTP ${responseCode}. Message: ${errorText}")
    }
    
} catch(Exception e) {
    response.setStatus(400)
    print new JsonBuilder([ 'error': true, 'message': 'SPARQL query error: ' + e.getMessage() ]).toString() 
}
