/**
 * updateOntology.groovy
 *
 * Hot-swap endpoint: downloads a pre-staged OWL file (already at owlPath on the
 * shared volume) into a new RequestManager, then atomically replaces the current
 * manager and disposes the old one.
 *
 * POST parameters (JSON body or query string):
 *   owlPath     - absolute path inside the container, must start with /data/
 *   secretKey   - must match ABEROWL_SECRET_KEY env var
 *   taskId      - optional, used to track status via updateStatus.groovy
 *   callbackUrl - optional, URL to POST the result to when finished
 */

import groovy.json.*
import src.util.Util
import src.RequestManager
import java.util.concurrent.ConcurrentHashMap

def params = Util.extractParams(request)
def owlPath    = params.owlPath
def secretKey  = params.secretKey
def taskId     = params.taskId ?: UUID.randomUUID().toString()
def callbackUrl = params.callbackUrl

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

// ---- Task registry -------------------------------------------------------
synchronized (application) {
    if (application.getAttribute("updateTasks") == null) {
        application.setAttribute("updateTasks", new ConcurrentHashMap())
    }
}
def updateTasks = application.getAttribute("updateTasks")
updateTasks[taskId] = [status: 'pending', started: new Date().toString()]

// ---- Current ontology name -----------------------------------------------
def currentManager = application.getAttribute("manager")
def ontName = currentManager?.ont ?: "unknown"

def servletContext = application

// ---- Background hot-swap -------------------------------------------------
Thread.start {
    def finalStatus = 'failed'
    def message = ''
    try {
        def newManager = RequestManager.create(ontName, owlPath)
        if (newManager != null) {
            def oldManager = servletContext.getAttribute("manager")
            // Swap AFTER create() is finished (create calls createReasoner)
            servletContext.setAttribute("manager", newManager)
            // Release reasoner resources from the old manager
            try { oldManager?.disposeAll() } catch (Exception ex) {
                println "WARNING: disposeAll on old manager failed: ${ex.getMessage()}"
            }
            finalStatus = 'success'
            message = "Loaded ${newManager.getOntology()?.getClassesInSignature(true)?.size() ?: 0} classes from ${owlPath}"
        } else {
            message = "RequestManager.create returned null for ${owlPath}"
        }
    } catch (Exception e) {
        message = e.getMessage() ?: e.getClass().getName()
        e.printStackTrace()
    }

    updateTasks[taskId] = [status: finalStatus, message: message, completed: new Date().toString()]

    // ---- Callback --------------------------------------------------------
    if (callbackUrl) {
        try {
            def payload = new JsonBuilder([
                ontology_id: ontName,
                task_id:     taskId,
                status:      finalStatus,
                message:     message
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

println new JsonBuilder([status: 'accepted', taskId: taskId])
