import groovy.json.*

if(!application) {
    application = request.getApplication(true)
}

def manager = application.getAttribute("manager")
response.contentType = 'application/json'

if (manager == null) {
    response.setStatus(503)
    print new JsonBuilder([status: 'unavailable']).toString()
    return
}

def ontologies = manager.listOntologies()
def classified = ontologies.findAll { it.status == 'classified' || it.status == 'incoherent' }

print new JsonBuilder([
    status: classified.size() > 0 ? 'ok' : 'loading',
    totalLoaded: ontologies.size(),
    totalClassified: classified.size()
]).toString()
