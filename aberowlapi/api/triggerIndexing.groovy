/**
 * triggerIndexing.groovy
 *
 * Runs IndexElastic.groovy as a subprocess to index an OWL file into
 * Elasticsearch.  Returns immediately with a task ID; poll via
 * updateStatus.groovy.
 *
 * POST parameters (JSON body or query string):
 *   owlPath          - the file to index: an existing regular file under
 *                      /data, or a validation export (see validateOntology)
 *   ontologyId       - lowercase ontology identifier / acronym
 *   classIndexName   - ES index to write classes into  (e.g. aberowl_hp_classes_v2)
 *   ontologyIndexName - ES ontologies index (default: aberowl_ontologies)
 *   name             - display name (optional, defaults to ontologyId)
 *   description      - description (optional)
 *   freshIndex       - "true" to skip deleteOntologyData (writing to a new index)
 *   secretKey        - must match ABEROWL_SECRET_KEY env var
 *
 * Answers 409 while a hot-swap or indexing task for the same ontology is
 * pending. A validation export is deleted once the indexer has exited,
 * whatever its outcome: it belongs to one transaction and is read once.
 */

import groovy.json.*
import src.util.Util
import src.UpdateFiles
import src.IndexerProcess
import java.util.concurrent.ConcurrentHashMap

def params = Util.extractParams(request)
def owlPath           = params.owlPath
def ontologyId        = params.ontologyId
def classIndexName    = params.classIndexName
def ontologyIndexName = params.ontologyIndexName ?: "aberowl_ontologies"
def name              = params.name ?: ontologyId
def description       = params.description ?: ""
def freshIndex        = (params.freshIndex == 'true') ? 'True' : 'False'
def secretKey         = params.secretKey

response.contentType = 'application/json'

// ---- Auth ---------------------------------------------------------------
def expectedKey = System.getenv("ABEROWL_SECRET_KEY") ?: ""
if (!expectedKey || secretKey != expectedKey) {
    response.setStatus(401)
    println new JsonBuilder([status: 'error', message: 'Unauthorized'])
    return
}

// ---- Validate inputs -----------------------------------------------------
def export = owlPath ? UpdateFiles.existingExport(owlPath) : null
if (!owlPath || (export == null && UpdateFiles.dataFile(owlPath) == null)) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: "owlPath must be an existing regular file under ${UpdateFiles.dataRoot()} or a validation export: ${owlPath}"])
    return
}
if (!ontologyId || !classIndexName) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'ontologyId and classIndexName are required'])
    return
}

// ---- Task registry -------------------------------------------------------
synchronized (application) {
    if (application.getAttribute("updateTasks") == null) {
        application.setAttribute("updateTasks", new ConcurrentHashMap())
    }
}
def updateTasks = application.getAttribute("updateTasks")
def taskId = "idx_${UUID.randomUUID()}"
def busy = null
synchronized (updateTasks) {
    busy = UpdateFiles.activeTask(updateTasks, ontologyId)
    if (!busy) {
        updateTasks[taskId] = [status: 'pending', kind: 'index', ontologyId: ontologyId,
                               owlPath: owlPath, started: new Date().toString()]
    }
}
if (busy) {
    response.setStatus(409)
    println new JsonBuilder([status: 'error', taskId: busy, message: "task ${busy} for ${ontologyId} is still pending"])
    return
}

// ---- Resolve ES connection params from environment ----------------------
def esUrl  = System.getenv("CENTRAL_ES_URL") ?: System.getenv("ELASTICSEARCH_URL") ?: "http://elasticsearch:9200"
def esUser = System.getenv("ES_USERNAME") ?: ""
def esPass = System.getenv("ES_PASSWORD") ?: ""

// ---- Launch indexer in background ----------------------------------------
Thread.start {
    def finalStatus = 'failed'
    def message = ''
    try {
        def jsonInput = new JsonBuilder([
            acronym:     ontologyId,
            name:        name,
            description: description
        ]).toString()

        def cmd = [
            "groovy", "/scripts/IndexElastic.groovy",
            esUrl,
            esUser,
            esPass,
            ontologyIndexName,
            classIndexName,
            owlPath,
            "True",    // skip_embedding (args[6])
            freshIndex // freshIndex flag  (args[7])
        ]

        def pb = new ProcessBuilder(cmd)
        pb.redirectErrorStream(true)   // merge stderr into stdout
        def proc = pb.start()

        def result = IndexerProcess.collect(proc, jsonInput)

        if (result.exitCode == 0) {
            finalStatus = 'success'
            message = "Indexing completed for ${ontologyId} into ${classIndexName}"
        } else {
            message = "Indexer exited ${result.exitCode}: ${result.outputTail?.takeRight(500)}"
        }
    } catch (Throwable e) {
        message = e.getMessage() ?: e.getClass().getName()
        e.printStackTrace()
    } finally {
        // The indexer has exited (or never started): nothing reads the export now.
        if (export != null) {
            try {
                UpdateFiles.deleteExport(export)
            } catch (Exception e) {
                println "WARNING: could not delete export ${export}: ${e.getMessage()}"
            }
        }
    }

    updateTasks[taskId] = [status: finalStatus, kind: 'index', ontologyId: ontologyId, owlPath: owlPath,
                           message: message, completed: new Date().toString()]
}

println new JsonBuilder([status: 'accepted', taskId: taskId])
