import groovy.json.*
import src.util.Util;
import src.RequestManager;

def params = Util.extractParams(request)

def ont = params.ontology;
def ontIRI = params.ontologyIRI;
def manager = application.manager;

try {
    if (ont != null && ontIRI != null) {
	def manager = RequestManager.create(ont, ontIRI);
	if (manager != null) {
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
