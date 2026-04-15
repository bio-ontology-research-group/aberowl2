// Find root classes in the hierarchy

import src.util.Util
import groovy.json.*

if(!application) {
  application = request.getApplication(true)
}

def params = Util.extractParams(request)

def query = params.query
def ontologyId = params.ontologyId ?: params.ontology
def manager = application.getAttribute("manager")

def owlThing = '<http://www.w3.org/2002/07/owl#Thing>'

if (!manager) {
    response.setStatus(503)
    print('{"result": [], "error": "Manager not available"}')
    return
}

// Resolve ontologyId
if (!ontologyId && manager.ontologies.size() == 1) {
    ontologyId = manager.getDefaultOntologyId()
} else if (!ontologyId) {
    response.setStatus(400)
    print('{"result": [], "error": "ontologyId parameter required"}')
    return
}

if (!manager.hasOntology(ontologyId)) {
    response.setStatus(404)
    print(new JsonBuilder(["result": [], "error": "Ontology not found: ${ontologyId}"]).toString())
    return
}

if(query) {
    query = java.net.URLDecoder.decode(query, "UTF-8")

    // find superclasses
    def supers = [query]
    def visited = new HashSet()
    visited.add(query)
    int it = 0
    while(it < supers.size()) {
	    q = supers[it]
	    parents = manager.runQuery(ontologyId, q, 'superclass', true, false, true).toArray()
	    if (parents.size() == 0 || parents[0].owlClass.equals(owlThing)) {
	        break
	    }
	    def parent = parents[0].owlClass
	    if (visited.contains(parent)) {
	        break // Cycle detected
	    }
	    supers.add(parent)
	    visited.add(parent)
	    it++
	    if (it > 100) break // Safety limit
    }

    supers = supers.reverse()

    // expand children
    def result = manager.runQuery(ontologyId, owlThing, 'subclass', true, false, true).toArray()
    def classes = result
    def classlabels;
    it = 0
    for (int i = 0; i < supers.size(); i++) {
	    for (int j = 0; j < classes.size(); j++) {
	        if (classes[j].owlClass.equals(supers[i])) {
		        def children = manager.runQuery(
		            ontologyId, classes[j].owlClass, 'subclass', true, false, true).toArray()
		        classes[j]["children"] = children
		        classes = children
		        break
	        }
	    }
    }

    response.contentType = 'application/json'
    print(new JsonBuilder(["result": result]).toString())
} else {
  print('{"result": []}')
}
