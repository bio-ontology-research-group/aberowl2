/**
 * triggerIndexing.groovy
 *
 * Runs IndexElastic.groovy as a subprocess to index an OWL file into
 * Elasticsearch.  Returns immediately with a task ID; poll via
 * updateStatus.groovy.
 *
 * POST parameters (JSON body or query string):
 *   owlPath          - path to OWL file inside container (must start with /data/)
 *   ontologyId       - lowercase ontology identifier / acronym
 *   classIndexName   - ES index to write classes into  (e.g. aberowl_hp_classes_v2)
 *   ontologyIndexName - ES ontologies index (default: aberowl_ontologies)
 *   name             - display name (optional, defaults to ontologyId)
 *   description      - description (optional)
 *   freshIndex       - "true" to skip deleteOntologyData (writing to a new index)
 *   secretKey        - must match ABEROWL_SECRET_KEY env var
 */

import groovy.json.*
import src.util.Util
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
if (!owlPath || !owlPath.startsWith("/data/")) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'owlPath must be within /data/'])
    return
}
if (!ontologyId || !classIndexName) {
    response.setStatus(400)
    println new JsonBuilder([status: 'error', message: 'ontologyId and classIndexName are required'])
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
def taskId = "idx_${UUID.randomUUID()}"
updateTasks[taskId] = [status: 'pending', started: new Date().toString()]

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

        // Write JSON metadata to stdin of IndexElastic.groovy
        proc.outputStream.withWriter('UTF-8') { it.write(jsonInput) }
        proc.outputStream.close()

        def output = proc.inputStream.text
        proc.waitFor()

        if (proc.exitValue() == 0) {
            finalStatus = 'success'
            message = "Indexing completed for ${ontologyId} into ${classIndexName}"
        } else {
            message = "Indexer exited ${proc.exitValue()}: ${output?.take(500)}"
        }
    } catch (Exception e) {
        message = e.getMessage() ?: e.getClass().getName()
        e.printStackTrace()
    }

    updateTasks[taskId] = [status: finalStatus, message: message, completed: new Date().toString()]
}

println new JsonBuilder([status: 'accepted', taskId: taskId])
