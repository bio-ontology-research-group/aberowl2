package src

/**
 * An ontology that opted into catalogue import resolution could not have its
 * imports closure resolved: the catalogue is invalid, or an import reached
 * from the root is missing from it or fails to load, or one IRI would name
 * two different files.
 * The ontology is then not loaded at all, so a worker never reports a
 * classification as complete when part of its closure is missing.
 */
class ImportResolutionException extends Exception {
    ImportResolutionException(String message) {
        super(message)
    }

    ImportResolutionException(String message, Throwable cause) {
        super(message, cause)
    }
}
