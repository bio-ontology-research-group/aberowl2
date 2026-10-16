/**
 * updateOntology.groovy
 *
 * Hot-swap endpoint: loads a pre-staged OWL file into the multi-ontology
 * RequestManager, replacing the existing ontology if present.
 *
 * POST parameters (JSON body or query string):
 *   owlPath      - an existing regular file under /data
 *   ontologyId   - identifier for the ontology to update/add
 *   reasonerType - reasoner to use: elk, structural, hermit. Defaults to the
 *                  ontology's current reasoner, else elk.
 *   servedPath   - optional, an existing file under /data that owlPath is a
 *                  staged copy of (see ImportCatalogue.resolveClosure)
 *   secretKey    - must match ABEROWL_SECRET_KEY env var
 *   taskId       - optional, used to track status via updateStatus.groovy
 *   callbackUrl  - optional, URL to POST the result to when finished
 *
 * Answers 409 while a hot-swap or indexing task for the same ontology is
 * pending. The finished task records, besides status and message, whether
 * the ontology is serving (loaded and classified) and the path it is served
 * from, so a caller can tell whether a failed swap left the previous copy
 * in place.
 */

import groovy.json.*
import src.util.Util
import src.RequestManager
import src.UpdateFiles
import java.util.concurrent.ConcurrentHashMap

def params = Util.extractParams(request)
def owlPath      = params.owlPath
def ontologyId   = params.ontologyId
def servedPath   = params.servedPath ?: null
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
if (!owlPath || UpdateFiles.dataFile(owlPath) == null) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: "owlPath must be an existing regular file under ${UpdateFiles.dataRoot()}: ${owlPath}"])
    return
}
if (servedPath && UpdateFiles.dataFile(servedPath) == null) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: "servedPath must be an existing regular file under ${UpdateFiles.dataRoot()}: ${servedPath}"])
    return
}
if (!ontologyId) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'ontologyId parameter required'])
    return
}

def manager = application.getAttribute("manager")
def reasonerType = params.reasonerType ?: manager?.reasonerTypes?.get(ontologyId) ?: "elk"

// ---- Task registry -------------------------------------------------------
synchronized (application) {
    if (application.getAttribute("updateTasks") == null) {
        application.setAttribute("updateTasks", new ConcurrentHashMap())
    }
}
def updateTasks = application.getAttribute("updateTasks")
def busy = null
synchronized (updateTasks) {
    busy = updateTasks.containsKey(taskId) ? taskId : UpdateFiles.activeTask(updateTasks, ontologyId)
    if (!busy) {
        updateTasks[taskId] = [status: 'pending', kind: 'hotswap', ontologyId: ontologyId,
                               owlPath: owlPath, started: new Date().toString()]
    }
}
if (busy) {
    response.setStatus(409)
    println new JsonBuilder([status: 'error', taskId: busy,
                             message: "task ${busy} for ${ontologyId} exists or is still pending"])
    return
}

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
        manager.reloadOntology(ontologyId, owlPath, reasonerType, servedPath)

        def classCount = manager.getOntology(ontologyId)?.getClassesInSignature(true)?.size() ?: 0
        finalStatus = 'success'
        message = "Loaded ${classCount} classes from ${owlPath} for ontology ${ontologyId} with ${reasonerType} reasoner"
    } catch (Throwable e) {
        message = e.getMessage() ?: e.getClass().getName()
        e.printStackTrace()
    }

    updateTasks[taskId] = [status: finalStatus, kind: 'hotswap', ontologyId: ontologyId, owlPath: owlPath,
                           message: message, completed: new Date().toString(),
                           serving: manager?.isServing(ontologyId) ?: false,
                           servedFrom: manager?.ontologyPaths?.get(ontologyId) ?: ""]

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
