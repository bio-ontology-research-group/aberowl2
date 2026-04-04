// Retrieve all labels and synonyms for an ontology from Elasticsearch

import groovy.json.*
import src.util.Util

if(!application) {
  application = request.getApplication(true)
}

def params = Util.extractParams(request)
def ontologyId = params.ontologyId ?: params.ontology

response.contentType = 'application/json'

if (!ontologyId) {
    // Try to get from the manager's default
    def manager = application.getAttribute("manager")
    if (manager && manager.ontologies.size() == 1) {
        ontologyId = manager.getDefaultOntologyId()
    } else {
        response.setStatus(400)
        println new JsonBuilder([ 'err': true, 'message': 'ontologyId parameter required.' ]).toString()
        return
    }
}

// Query Elasticsearch directly for all labels and synonyms
def esUrl = System.getenv("CENTRAL_ES_URL") ?: System.getenv("ELASTICSEARCH_URL") ?: "http://elasticsearch:9200"
def indexName = "aberowl_${ontologyId.toLowerCase()}_classes"

try {
    def output = new TreeSet()
    def searchBody = new JsonBuilder([
        query: [
            term: [ontology: ontologyId.toLowerCase()]
        ],
        _source: ["label", "synonym"],
        size: 10000
    ]).toString()

    def url = new URL("${esUrl}/${indexName}/_search")
    def conn = url.openConnection() as HttpURLConnection
    conn.setRequestMethod('POST')
    conn.setDoOutput(true)
    conn.setRequestProperty('Content-Type', 'application/json')
    conn.setConnectTimeout(30000)
    conn.setReadTimeout(30000)
    conn.outputStream.withWriter('UTF-8') { it.write(searchBody) }

    if (conn.responseCode >= 200 && conn.responseCode < 300) {
        def responseText = conn.inputStream.text
        def results = new JsonSlurper().parseText(responseText)
        def hits = results?.hits?.hits ?: []
        hits.each { hit ->
            def source = hit._source
            if (source?.label instanceof List) {
                source.label.each { output.add(it) }
            } else if (source?.label) {
                output.add(source.label)
            }
            if (source?.synonym instanceof List) {
                source.synonym.each { output.add(it) }
            } else if (source?.synonym) {
                output.add(source.synonym)
            }
        }
    }

    print new JsonBuilder(output).toString()
} catch (Exception e) {
    response.setStatus(500)
    println new JsonBuilder([ 'err': true, 'message': 'Error querying Elasticsearch: ' + e.getMessage() ]).toString()
}
