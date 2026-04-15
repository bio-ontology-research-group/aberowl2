import groovy.json.JsonBuilder
import org.json.simple.JSONValue;
import java.net.URLDecoder;
import src.util.Util

if(!application) {
    application = request.getApplication(true);
}

def params = Util.extractParams(request);
def property = params.property;
def ontologyId = params.ontologyId ?: params.ontology;
def manager = application.getAttribute("manager");

response.contentType = 'application/json';

if (!manager) {
    response.setStatus(503)
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
    print(new JsonBuilder([status: "error", message: "Ontology not found: ${ontologyId}"]).toString())
    return
}

if (property == null) {
    def objectProperties = manager.getObjectProperties(ontologyId)
    print(new JsonBuilder(objectProperties))
} else {
    property = URLDecoder.decode(property, "UTF-8")
    def objectProperties = manager.getObjectProperties(ontologyId, property)
    print(new JsonBuilder(objectProperties))
}
