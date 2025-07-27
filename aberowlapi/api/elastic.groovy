import groovy.json.*
import javax.servlet.http.*
import java.net.http.*
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.net.URI
import java.net.URLEncoder

// Get the index name from the query parameter
def indexName = request.getParameter("index")

if (!indexName) {
    response.setStatus(400)
    response.setContentType("application/json")
    def error = [error: "Index name is required"]
    response.writer.write(new JsonBuilder(error).toString())
    return
}

// Handle both GET and POST requests
def requestBody = ""
def httpMethod = request.getMethod()

if (httpMethod == "POST") {
    // Read the request body for POST requests
    requestBody = request.reader.text
} else if (httpMethod == "GET") {
    def source = request.getParameter("source")
    if (source) {
        requestBody = source
    } else {
        // For GET requests without a source, build a match_all query
        requestBody = new JsonBuilder([
            query: [
                match_all: [:]
            ]
        ]).toString()
    }
} else {
    response.setStatus(405)
    response.setContentType("application/json")
    def error = [error: "Method not allowed. Only GET and POST are supported."]
    response.writer.write(new JsonBuilder(error).toString())
    return
}

// Forward the request to Elasticsearch
def esBaseUrl = System.getenv("ELASTICSEARCH_URL") ?: "http://localhost:9200"
def encodedQuery = URLEncoder.encode(requestBody, "UTF-8")
def esUrl = "${esBaseUrl}/${indexName}/_search?source=${encodedQuery}&source_content_type=application/json"

try {
    def client = HttpClient.newHttpClient()
    def esRequest = HttpRequest.newBuilder()
        .uri(URI.create(esUrl))
        .GET()
        .build()

    def esResponse = client.send(esRequest, HttpResponse.BodyHandlers.ofString())
    
    // Forward the response
    response.setStatus(esResponse.statusCode())
    response.setContentType("application/json")
    response.writer.write(esResponse.body())
} catch (Exception e) {
    response.setStatus(500)
    response.setContentType("application/json")
    def error = [error: "Failed to query Elasticsearch: ${e.message}"]
    response.writer.write(new JsonBuilder(error).toString())
}
