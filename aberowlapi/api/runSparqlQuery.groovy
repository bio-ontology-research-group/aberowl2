import groovy.json.*
import src.util.Util
import java.net.URLEncoder

if(!application) {
    application = request.getApplication(true)
}

def params = [:]
if (request != null) {
    println "DEBUG: Extracted params: ${request}"
    params = Util.extractParams(request)
}
println "DEBUG: Extracted params: ${params}"

def query = params.query
def endpoint = params.endpoint ?: "http://localhost:8080/virtuoso/"
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
    def endpointUrl = endpoint.startsWith("http") ? endpoint : request.getRequestURL().toString().replaceFirst(/runSparqlQuery\.groovy$/, (endpoint.startsWith('/') ? endpoint.substring(1) : endpoint))

    def queryParams = "query=" + URLEncoder.encode(rewrittenQuery, "UTF-8") + "&format=application%2Fsparql-results%2Bjson&default-graph-uri="
    
    println "DEBUG: Sending SPARQL query to endpoint ${endpointUrl}"

    def http = new URL(endpointUrl).openConnection() as HttpURLConnection
    http.setDoOutput(true)
    http.setRequestProperty('Accept', 'application/sparql-results+json')
    http.setRequestProperty('Content-Type', 'application/x-www-form-urlencoded')
    http.connect()
    
    http.setRequestMethod('GET')
    
    def writer = new OutputStreamWriter(http.getOutputStream())
    writer.write(queryParams)
    writer.flush()
    writer.close()

    def responseCode = http.responseCode
    println "DEBUG: SPARQL endpoint responded with HTTP ${responseCode}"
    
    if (responseCode >= 200 && responseCode < 300) {
        def responseText = http.inputStream.text
        println "DEBUG: Response text: ${responseText}"
        
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
