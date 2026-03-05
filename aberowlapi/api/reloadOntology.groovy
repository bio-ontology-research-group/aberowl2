import groovy.json.*
import src.util.Util;
import src.RequestManager;

def params = Util.extractParams(request)

def ont = params.ontology;
def ontIRI = params.ontologyIRI;
def manager = application.getAttribute("manager");

try {
    if (ont != null && ontIRI != null) {
        // Security: Prevent Arbitrary File Read and SSRF.
        // We only allow local file paths in /data or valid HTTP/HTTPS URLs.
        if (!ontIRI.startsWith("/data/") && !ontIRI.startsWith("http://") && !ontIRI.startsWith("https://")) {
            throw new Exception("Invalid ontology IRI. Must be in /data/ or a valid URL.")
        }
        
        // Further restriction: if it's a file path, it MUST be in /data/
        if (ontIRI.startsWith("/") && !ontIRI.startsWith("/data/")) {
             throw new Exception("Local files must be within the /data/ directory.")
        }

	def newManager = RequestManager.create(ont, ontIRI);
	if (newManager != null) {
            application.setAttribute("manager", newManager)
	    println(new JsonBuilder(['status': 'ok']))
	} else {
	    throw new Exception("Unable to load ontology!");
	}
    } else {
	throw new Exception("Not enough parameters!");
    }
} catch(Exception e) {
  response.setStatus(400);
  println(new JsonBuilder([ 'status': 'error', 'message': e.getMessage() ])) 
}
