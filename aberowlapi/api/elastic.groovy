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

// Handle both GET and POST requests
def requestBody = ""
def httpMethod = request.getMethod()

if (httpMethod == "POST") {
    // Read the request body for POST requests
    requestBody = request.reader.text
} else if (httpMethod == "GET") {
    // For GET requests, build a match_all query
    requestBody = new JsonBuilder([
        query: [
            match_all: [:]
        ]
    ]).toString()
} else {
    response.setStatus(405)
    response.setContentType("application/json")
    def error = [error: "Method not allowed. Only GET and POST are supported."]
    response.writer.write(new JsonBuilder(error).toString())
    return
}

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
