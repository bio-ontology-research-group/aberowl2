// Gets the relational direct successors

import groovy.json.*
import src.util.Util

if(!application) {
  application = request.getApplication(true)
}

def params = Util.extractParams(request)
def relation = params.relation
def qClass = params.class
def manager = application.getAttribute("manager")

if (!relation || !qClass || !manager) {
  response.setStatus(400)
  println new JsonBuilder([ 'err': true, 'message': 'Missing parameters: relation, class, or manager not found.' ]).toString()
  return
}

try {
  def results = new HashMap()
  def out = manager.relationQuery(relation, qClass)

  results['result'] = out
  response.contentType = 'application/json'
  print new JsonBuilder(results).toString()
} catch(Exception e) {
  response.setStatus(400)
  println new JsonBuilder([ 'err': true, 'message': 'Generic query error: ' + e.getMessage() ]).toString() 
}
