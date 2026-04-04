import groovy.json.*
import src.util.Util;
import src.RequestManager;

def params = Util.extractParams(request)

def ontologyId = params.ontologyId ?: params.ontology ?: params.ont
def ontIRI = params.ontologyIRI;
def reasonerType = params.reasonerType ?: "elk"
def manager = application.getAttribute("manager");

response.contentType = 'application/json'

try {
    if (ontologyId != null && ontIRI != null) {
        // Security: Prevent Arbitrary File Read and SSRF.
        if (!ontIRI.startsWith("/data/") && !ontIRI.startsWith("http://") && !ontIRI.startsWith("https://")) {
            throw new Exception("Invalid ontology IRI. Must be in /data/ or a valid URL.")
        }
        if (ontIRI.startsWith("/") && !ontIRI.startsWith("/data/")) {
             throw new Exception("Local files must be within the /data/ directory.")
        }

        if (manager == null) {
            manager = new RequestManager()
            application.setAttribute("manager", manager)
        }

        // Reload (hot-swap) the specific ontology within the multi-ontology manager
        manager.reloadOntology(ontologyId, ontIRI, reasonerType)
        def classCount = manager.getOntology(ontologyId)?.getClassesInSignature(true)?.size() ?: 0
        println(new JsonBuilder([
            'status': 'ok',
            'ontologyId': ontologyId,
            'classCount': classCount,
            'reasonerType': reasonerType
        ]))
    } else {
        throw new Exception("Not enough parameters! Required: ontologyId, ontologyIRI");
    }
} catch(Exception e) {
  response.setStatus(400);
  println(new JsonBuilder([ 'status': 'error', 'message': e.getMessage() ]))
}
