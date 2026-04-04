/**
 * updateOntology.groovy
 *
 * Hot-swap endpoint: loads a pre-staged OWL file into the multi-ontology
 * RequestManager, replacing the existing ontology if present.
 *
 * POST parameters (JSON body or query string):
 *   owlPath      - absolute path inside the container, must start with /data/
 *   ontologyId   - identifier for the ontology to update/add
 *   reasonerType - reasoner to use: elk (default), structural, hermit
 *   secretKey    - must match ABEROWL_SECRET_KEY env var
 *   taskId       - optional, used to track status via updateStatus.groovy
 *   callbackUrl  - optional, URL to POST the result to when finished
 */

import groovy.json.*
import src.util.Util
import src.RequestManager
import java.util.concurrent.ConcurrentHashMap

def params = Util.extractParams(request)
def owlPath      = params.owlPath
def ontologyId   = params.ontologyId
def reasonerType = params.reasonerType ?: "elk"
def secretKey    = params.secretKey
def taskId       = params.taskId ?: UUID.randomUUID().toString()
def callbackUrl  = params.callbackUrl

response.contentType = 'application/json'

// ---- Auth ---------------------------------------------------------------
def expectedKey = System.getenv("ABEROWL_SECRET_KEY") ?: ""
if (!expectedKey || secretKey != expectedKey) {
    response.setStatus(401)
    println new JsonBuilder([status: 'error', message: 'Unauthorized'])
    return
}

// ---- Validate path -------------------------------------------------------
if (!owlPath || !owlPath.startsWith("/data/")) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'owlPath must be within /data/'])
    return
}
if (!new File(owlPath).exists()) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: "File not found: ${owlPath}"])
    return
}
if (!ontologyId) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'ontologyId parameter required'])
    return
}

// ---- Task registry -------------------------------------------------------
synchronized (application) {
    if (application.getAttribute("updateTasks") == null) {
        application.setAttribute("updateTasks", new ConcurrentHashMap())
    }
}
def updateTasks = application.getAttribute("updateTasks")
updateTasks[taskId] = [status: 'pending', started: new Date().toString()]

def manager = application.getAttribute("manager")
def servletContext = application

// ---- Background hot-swap -------------------------------------------------
Thread.start {
    def finalStatus = 'failed'
    def message = ''
    try {
        if (manager == null) {
            manager = new RequestManager()
            servletContext.setAttribute("manager", manager)
        }

        // Reload the specific ontology (disposes old, loads new, classifies)
        manager.reloadOntology(ontologyId, owlPath, reasonerType)

        def classCount = manager.getOntology(ontologyId)?.getClassesInSignature(true)?.size() ?: 0
        finalStatus = 'success'
        message = "Loaded ${classCount} classes from ${owlPath} for ontology ${ontologyId} with ${reasonerType} reasoner"
    } catch (Exception e) {
        message = e.getMessage() ?: e.getClass().getName()
        e.printStackTrace()
    }

    updateTasks[taskId] = [status: finalStatus, message: message, completed: new Date().toString()]

    // ---- Callback --------------------------------------------------------
    if (callbackUrl) {
        try {
            def payload = new JsonBuilder([
                ontology_id:  ontologyId,
                task_id:      taskId,
                status:       finalStatus,
                message:      message,
                reasoner_type: reasonerType
            ]).toString()
            def conn = new URL(callbackUrl).openConnection() as HttpURLConnection
            conn.setRequestMethod('POST')
            conn.setDoOutput(true)
            conn.setRequestProperty('Content-Type', 'application/json')
            conn.setConnectTimeout(10_000)
            conn.setReadTimeout(10_000)
            conn.outputStream.withWriter { it.write(payload) }
            conn.responseCode   // triggers the request
        } catch (Exception e) {
            println "WARNING: callback to ${callbackUrl} failed: ${e.getMessage()}"
        }
    }
}

println new JsonBuilder([status: 'accepted', taskId: taskId, ontologyId: ontologyId])
