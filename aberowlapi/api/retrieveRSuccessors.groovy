// Gets the relational direct successors

import groovy.json.*
import src.util.Util

if(!application) {
  application = request.getApplication(true)
}

def params = Util.extractParams(request)
def relation = params.relation
def qClass = params.class
def ontologyId = params.ontologyId ?: params.ontology
def manager = application.getAttribute("manager")

if (!manager) {
  response.setStatus(503)
  println new JsonBuilder([ 'err': true, 'message': 'Manager not available.' ]).toString()
  return
}

// Resolve ontologyId
if (!ontologyId && manager.ontologies.size() == 1) {
    ontologyId = manager.getDefaultOntologyId()
} else if (!ontologyId) {
    response.setStatus(400)
    println new JsonBuilder([ 'err': true, 'message': 'ontologyId parameter required.' ]).toString()
    return
}

if (!relation || !qClass) {
  response.setStatus(400)
  println new JsonBuilder([ 'err': true, 'message': 'Missing parameters: relation, class.' ]).toString()
  return
}

if (!manager.hasOntology(ontologyId)) {
  response.setStatus(404)
  println new JsonBuilder([ 'err': true, 'message': "Ontology not found: ${ontologyId}" ]).toString()
  return
}

try {
  def results = new HashMap()
  def out = manager.relationQuery(ontologyId, relation, qClass)

  results['result'] = out
  response.contentType = 'application/json'
  print new JsonBuilder(results).toString()
} catch(Exception e) {
  response.setStatus(400)
  println new JsonBuilder([ 'err': true, 'message': 'Generic query error: ' + e.getMessage() ]).toString()
}
