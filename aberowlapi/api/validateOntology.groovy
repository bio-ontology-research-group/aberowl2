/**
 * validateOntology.groovy
 *
 * Check that an OWL file (a staged update) loads, without touching the
 * ontology being served. Used by central's update pipeline.
 *
 * POST JSON (or GET) parameters:
 *   secretKey  - must match ABEROWL_SECRET_KEY
 *   owlPath    - the file to check, an existing regular file under /data
 *   ontologyId - optional. The file is read the way a hot-swap of this
 *                ontology would read it: with its import catalogue
 *                (/data/import_catalogues/<ontologyId>.json) if it has one,
 *                otherwise with imports suppressed. Without it, imports are
 *                suppressed. No import IRI is fetched.
 *   servedPath - optional, an existing file under /data: the file owlPath is
 *                a staged copy of. Catalogue entries naming it name the root.
 *   exportId   - optional, central's transaction id; needs ontologyId. The
 *                merged ontology, imports merged in and no owl:imports left,
 *                is written to <export root>/<exportId>/<ontologyId>.owl for
 *                triggerIndexing. An existing export is never replaced.
 *
 * Answers {status: "ok", classCount, importsPolicy, importsDeclared,
 * importsResolved, importsUnresolved, declaredImports[, exportPath]}, HTTP
 * 400 with {status: "error", message}, or 401.
 */

import groovy.json.*
import src.util.Util
import src.RequestManager
import src.UpdateFiles

def params = Util.extractParams(request)
def owlPath = params.owlPath
def ontologyId = params.ontologyId
def servedPath = params.servedPath
def exportId = params.exportId

response.contentType = 'application/json'

def fail = { int code, String message ->
    response.setStatus(code)
    println new JsonBuilder([status: 'error', message: message])
}

// A validation parses a whole closure in this worker's heap, and an export
// writes a file: callers must hold the shared secret.
def expectedKey = System.getenv("ABEROWL_SECRET_KEY") ?: ""
if (!expectedKey || params.secretKey != expectedKey) {
    fail(401, 'Unauthorized')
    return
}
if (!owlPath) {
    fail(400, 'owlPath parameter required')
    return
}
if (UpdateFiles.dataFile(owlPath) == null) {
    fail(400, "owlPath must be an existing regular file under ${UpdateFiles.dataRoot()}: ${owlPath}")
    return
}
if (servedPath && UpdateFiles.dataFile(servedPath) == null) {
    fail(400, "servedPath must be an existing regular file under ${UpdateFiles.dataRoot()}: ${servedPath}")
    return
}
def exportTo = null
if (exportId) {
    exportTo = UpdateFiles.exportFile(ontologyId, exportId)
    if (exportTo == null) {
        fail(400, 'exportId needs a valid ontologyId and a transaction id of lower-case letters, digits and hyphens')
        return
    }
    if (exportTo.toFile().exists()) {
        fail(400, "export already exists: ${exportTo}")
        return
    }
}

try {
    // A fresh manager: validation holds no state, and every RequestManager
    // applies the same import policy.
    def summary = new RequestManager().validateOntologyFile(ontologyId, owlPath, servedPath ?: null, exportTo)
    println new JsonBuilder([status: 'ok'] + summary)
} catch (Exception e) {
    fail(400, e.getMessage() ?: e.getClass().getName())
}
