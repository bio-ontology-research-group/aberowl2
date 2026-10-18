/**
 * finalizeUpdate.groovy
 *
 * Bookkeeping that ends one central update transaction on this worker.
 *
 * POST JSON parameters:
 *   secretKey  - must match ABEROWL_SECRET_KEY
 *   ontologyId - the ontology
 *   action     - "finalize" or "discardExport"
 *
 * action "finalize": central has hot-swapped the ontology from a staged file
 * and then promoted that file to the served path. Record the served path as
 * the ontology's file, in place of the staged one.
 *   owlPath    - the staged path the hot-swap loaded; must still be recorded
 *   servedPath - an existing regular file under /data
 * Answers {status: "ok", ontologyId, path}; 409 with {status: "error",
 * message, path} when another path is recorded (path is that one, "" when
 * the ontology is not loaded) or a task for the ontology is pending.
 *
 * action "discardExport": delete the validation export of a transaction
 * that will not be indexed.
 *   exportId   - central's transaction id
 * Answers {status: "ok", deleted: true|false}; 409 while a task reads it.
 */

import groovy.json.*
import src.util.Util
import src.UpdateFiles
import java.util.concurrent.ConcurrentHashMap

def params = Util.extractParams(request)
def ontologyId = params.ontologyId
def action = params.action

response.contentType = 'application/json'

def answer = { int code, Map body ->
    response.setStatus(code)
    println new JsonBuilder(body)
}

def expectedKey = System.getenv("ABEROWL_SECRET_KEY") ?: ""
if (!expectedKey || params.secretKey != expectedKey) {
    answer(401, [status: 'error', message: 'Unauthorized'])
    return
}
if (!ontologyId) {
    answer(400, [status: 'error', message: 'ontologyId parameter required'])
    return
}

synchronized (application) {
    if (application.getAttribute("updateTasks") == null) {
        application.setAttribute("updateTasks", new ConcurrentHashMap())
    }
}
def updateTasks = application.getAttribute("updateTasks")

if (action == 'finalize') {
    def owlPath = params.owlPath
    def servedPath = params.servedPath
    if (!owlPath || !servedPath || UpdateFiles.dataFile(servedPath) == null) {
        answer(400, [status: 'error', message: "owlPath and servedPath (an existing regular file under ${UpdateFiles.dataRoot()}) are required"])
        return
    }
    def manager = application.getAttribute("manager")
    synchronized (updateTasks) {
        def busy = UpdateFiles.activeTask(updateTasks, ontologyId)
        if (busy) {
            answer(409, [status: 'error', message: "task ${busy} for ${ontologyId} is still pending",
                         path: manager?.ontologyPaths?.get(ontologyId) ?: ""])
            return
        }
        def recorded = manager?.finalizeServedPath(ontologyId, owlPath, servedPath)
        if (recorded != servedPath) {
            answer(409, [status: 'error', path: recorded ?: "",
                         message: "${ontologyId} is recorded as ${recorded ?: 'not loaded'}, not ${owlPath}"])
            return
        }
    }
    answer(200, [status: 'ok', ontologyId: ontologyId, path: servedPath])
} else if (action == 'discardExport') {
    def export = UpdateFiles.exportFile(ontologyId, params.exportId)
    if (export == null) {
        answer(400, [status: 'error', message: 'a valid ontologyId and exportId are required'])
        return
    }
    synchronized (updateTasks) {
        def reader = UpdateFiles.taskReading(updateTasks, export.toString())
        if (reader) {
            answer(409, [status: 'error', message: "task ${reader} is reading ${export}"])
            return
        }
        answer(200, [status: 'ok', deleted: UpdateFiles.deleteExport(export)])
    }
} else {
    answer(400, [status: 'error', message: 'action must be "finalize" or "discardExport"'])
}
