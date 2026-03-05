import groovy.json.*
import src.util.Util

import org.semanticweb.owlapi.apibinding.OWLManager
import org.semanticweb.owlapi.model.*

def params = Util.extractParams(request)
def owlPath = params.owlPath

response.contentType = 'application/json'

if (!owlPath) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'owlPath parameter required'])
    return
}

// Security: restrict to /data/ directory only
if (!owlPath.startsWith("/data/")) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'owlPath must be within /data/'])
    return
}

try {
    OWLOntologyManager manager = OWLManager.createOWLOntologyManager()
    OWLOntology ont = manager.loadOntologyFromOntologyDocument(new File(owlPath))
    int classCount = ont.getClassesInSignature(true).size()
    println new JsonBuilder([status: 'ok', classCount: classCount])
} catch (Exception e) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: e.getMessage()])
}
