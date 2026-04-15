/**
 * removeOntology.groovy
 *
 * Dynamically unload an ontology from the running multi-ontology container.
 *
 * POST parameters:
 *   ontologyId - identifier of the ontology to unload
 *   secretKey  - must match ABEROWL_SECRET_KEY env var
 */

import groovy.json.*
import src.util.Util

def params = Util.extractParams(request)
def ontologyId = params.ontologyId
def secretKey  = params.secretKey

response.contentType = 'application/json'

// Auth
def expectedKey = System.getenv("ABEROWL_SECRET_KEY") ?: ""
if (!expectedKey || secretKey != expectedKey) {
    response.setStatus(401)
    println new JsonBuilder([status: 'error', message: 'Unauthorized'])
    return
}

if (!ontologyId) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'ontologyId required'])
    return
}

def manager = application.getAttribute("manager")
if (manager == null || !manager.hasOntology(ontologyId)) {
    response.setStatus(404)
    println new JsonBuilder([status: 'error', message: "Ontology not found: ${ontologyId}"])
    return
}

try {
    manager.disposeOntology(ontologyId)
    println new JsonBuilder([status: 'ok', message: "Ontology ${ontologyId} unloaded"])
} catch (Exception e) {
    response.setStatus(500)
    println new JsonBuilder([status: 'error', message: e.getMessage()])
}
