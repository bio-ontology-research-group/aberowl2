import groovy.json.*
import javax.servlet.http.*
import java.net.http.*
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.net.URI

// Get the index name from the path
def pathInfo = request.getPathInfo()
def indexName = pathInfo ? pathInfo.substring(1) : null // Remove leading slash

if (!indexName) {
    response.setStatus(400)
    response.setContentType("application/json")
    def error = [error: "Index name is required"]
    response.writer.write(new JsonBuilder(error).toString())
    return
}

// Read the request body
def requestBody = request.reader.text

// Forward the request to Elasticsearch
def esUrl = "http://localhost:9200/${indexName}/_search"

try {
    def client = HttpClient.newHttpClient()
    def esRequest = HttpRequest.newBuilder()
        .uri(URI.create(esUrl))
        .header("Content-Type", "application/json")
        .POST(HttpRequest.BodyPublishers.ofString(requestBody))
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
