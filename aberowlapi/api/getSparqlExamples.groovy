import groovy.json.JsonOutput
import src.util.Util

if(!application) {
    application = request.getApplication(true);
}

def params = Util.extractParams(request)
def ontologyId = params.ontologyId ?: params.ontology
def manager = application.getAttribute("manager")
response.contentType = 'application/json'

if (!manager) {
    print('{"status": "error", "message": "Manager not available."}')
    return
}

// Resolve ontologyId
if (!ontologyId && manager.ontologies.size() == 1) {
    ontologyId = manager.getDefaultOntologyId()
} else if (!ontologyId) {
    response.setStatus(400)
    print('{"status": "error", "message": "ontologyId parameter required"}')
    return
}

if (!manager.hasOntology(ontologyId)) {
    response.setStatus(404)
    print(JsonOutput.toJson([status: "error", message: "Ontology not found: ${ontologyId}"]))
    return
}

def result = manager.getSparqlExamples(ontologyId)
print(JsonOutput.toJson(result))
