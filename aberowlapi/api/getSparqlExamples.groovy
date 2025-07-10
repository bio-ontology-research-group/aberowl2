import groovy.json.JsonOutput

if(!application) {
    application = request.getApplication(true);
}

def manager = application.manager
response.contentType = 'application/json'

if(manager) {
    def result = manager.getSparqlExamples()
    print(JsonOutput.toJson(result))
} else {
    print('{"status": "error", "message": "Please provide an ontology!"}')
}
