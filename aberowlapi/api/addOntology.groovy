/**
 * addOntology.groovy
 *
 * Dynamically load a new ontology into the running multi-ontology container.
 *
 * POST parameters:
 *   ontologyId   - unique identifier for the ontology
 *   owlPath      - absolute path to OWL file (must start with /data/)
 *   reasonerType - reasoner to use: elk (default), structural, hermit
 *   secretKey    - must match ABEROWL_SECRET_KEY env var
 */

import groovy.json.*
import src.util.Util
import src.RequestManager
import java.util.concurrent.ConcurrentHashMap

def params = Util.extractParams(request)
def ontologyId   = params.ontologyId
def owlPath      = params.owlPath
def reasonerType = params.reasonerType ?: "elk"
def secretKey    = params.secretKey

response.contentType = 'application/json'

// Auth
def expectedKey = System.getenv("ABEROWL_SECRET_KEY") ?: ""
if (!expectedKey || secretKey != expectedKey) {
    response.setStatus(401)
    println new JsonBuilder([status: 'error', message: 'Unauthorized'])
    return
}

// Validate inputs
if (!ontologyId || !owlPath) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'ontologyId and owlPath required'])
    return
}
if (!owlPath.startsWith("/data/")) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'owlPath must be within /data/'])
    return
}
if (!new File(owlPath).exists()) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: "File not found: ${owlPath}"])
    return
}

def manager = application.getAttribute("manager")
if (manager == null) {
    manager = new RequestManager()
    application.setAttribute("manager", manager)
}

if (manager.hasOntology(ontologyId)) {
    response.setStatus(409)
    println new JsonBuilder([status: 'error', message: "Ontology already loaded: ${ontologyId}. Use updateOntology to replace."])
    return
}

// Set up task tracking
synchronized (application) {
    if (application.getAttribute("updateTasks") == null) {
        application.setAttribute("updateTasks", new ConcurrentHashMap())
    }
}
def updateTasks = application.getAttribute("updateTasks")
def taskId = "add_${UUID.randomUUID()}"
updateTasks[taskId] = [status: 'pending', started: new Date().toString()]

// Load in background
Thread.start {
    def finalStatus = 'failed'
    def message = ''
    try {
        manager.loadOntology(ontologyId, owlPath, reasonerType)
        manager.createReasoner(ontologyId)
        def classCount = manager.getOntology(ontologyId)?.getClassesInSignature(true)?.size() ?: 0
        finalStatus = 'success'
        message = "Loaded ${classCount} classes for ${ontologyId} with ${reasonerType} reasoner"
    } catch (Exception e) {
        message = e.getMessage() ?: e.getClass().getName()
        e.printStackTrace()
        // Clean up on failure
        try { manager.disposeOntology(ontologyId) } catch (Exception ex) {}
    }
    updateTasks[taskId] = [status: finalStatus, message: message, completed: new Date().toString()]
}

println new JsonBuilder([status: 'accepted', taskId: taskId, ontologyId: ontologyId])
