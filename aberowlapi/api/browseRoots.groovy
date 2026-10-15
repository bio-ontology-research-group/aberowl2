/**
 * browseRoots.groovy
 *
 * The classes a hierarchy browser should start from for one ontology.
 *
 * When the ontology declares its own root terms ("has ontology root term",
 * IAO_0000700, on its header) and at least one is a usable class, those are
 * returned with source "declared". Otherwise the direct subclasses of
 * owl:Thing are returned with source "owl:Thing". This only chooses where
 * browsing starts: DL queries, including owl:Thing queries in runQuery.groovy,
 * still answer over the whole ontology and its loaded imports.
 *
 * GET parameters:
 *   ontologyId - the ontology (optional when the worker holds exactly one)
 *
 * Response: {"result": [<class info>], "source": "declared" | "owl:Thing",
 *            "declared": [<declared IRIs>], "ignored": [<declared IRIs not used>]}
 */

import groovy.json.*
import src.util.Util

if (!application) {
    application = request.getApplication(true)
}

def params = Util.extractParams(request)
def ontologyId = params.ontologyId ?: params.ontology
def manager = application.getAttribute("manager")

response.contentType = 'application/json'

if (manager == null) {
    response.setStatus(503)
    print new JsonBuilder([error: true, message: 'Manager not available.']).toString()
    return
}

if (!ontologyId && manager.ontologies.size() == 1) {
    ontologyId = manager.getDefaultOntologyId()
} else if (!ontologyId) {
    response.setStatus(400)
    print new JsonBuilder([error: true, message: 'ontologyId parameter required (multiple ontologies loaded).']).toString()
    return
}

if (!manager.hasOntology(ontologyId)) {
    response.setStatus(404)
    print new JsonBuilder([error: true, message: "Ontology not found: ${ontologyId}"]).toString()
    return
}

def owlThing = '<http://www.w3.org/2002/07/owl#Thing>'
try {
    def body
    // Servlets reload hot while RequestManager reloads only on a worker
    // restart: a manager without declared roots answers with owl:Thing roots.
    if (manager.respondsTo("getBrowseRoots")) {
        body = manager.getBrowseRoots(ontologyId)
    } else {
        body = [result: manager.runQuery(ontologyId, owlThing, 'subclass', true, true, false, null),
                source: 'owl:Thing', declared: [], ignored: []]
    }
    print new JsonBuilder(body).toString()
} catch (IllegalArgumentException e) {
    response.setStatus(404)
    print new JsonBuilder([error: true, message: e.getMessage()]).toString()
} catch (Exception e) {
    response.setStatus(500)
    print new JsonBuilder([error: true, message: 'Could not compute browsing roots: ' + e.getMessage()]).toString()
}
