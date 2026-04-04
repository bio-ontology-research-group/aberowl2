/**
 * listLoadedOntologies.groovy
 *
 * Return a list of all ontologies loaded in this container with their status,
 * reasoner type, and class count.
 */

import groovy.json.*

if(!application) {
    application = request.getApplication(true)
}

def manager = application.getAttribute("manager")
response.contentType = 'application/json'

if (manager == null) {
    response.setStatus(503)
    print new JsonBuilder([status: 'error', message: 'Manager not available', ontologies: []]).toString()
    return
}

def ontologies = manager.listOntologies()
print new JsonBuilder([status: 'ok', ontologies: ontologies]).toString()
