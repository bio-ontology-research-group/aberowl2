package src

import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

/**
 * Paths the update servlets accept (validateOntology, updateOntology,
 * triggerIndexing, finalizeUpdate).
 *
 * Ontology files live under the data root (/data), which deployed workers
 * mount read-only. A validation export, the merged ontology that indexing
 * reads, is written to a worker-local export root instead: by default
 * <java.io.tmpdir>/aberowl-update-exports, one directory per transaction.
 */
class UpdateFiles {
    // Central's transaction id: lower-case letters, digits and hyphens.
    static final String TXN_PATTERN = /[a-z0-9][a-z0-9-]{0,63}/
    static final String ID_PATTERN = /[A-Za-z0-9][A-Za-z0-9._-]*/

    static String dataRoot() {
        System.getProperty("aberowl.dataRoot", "/data")
    }

    static String exportRoot() {
        System.getProperty("aberowl.exportRoot",
            Paths.get(System.getProperty("java.io.tmpdir"), "aberowl-update-exports").toString())
    }

    /**
     * The real path of an existing regular file under root, both as written
     * and after following symlinks, or null. Rejects relative paths, ..
     * traversal, symlinks out of the root and the root itself.
     */
    static Path fileUnder(String path, String root) {
        if (!path || !root) {
            return null
        }
        try {
            Path p = Paths.get(path)
            if (!p.isAbsolute()) {
                return null
            }
            Path lexicalRoot = Paths.get(root).toAbsolutePath().normalize()
            Path lexical = p.normalize()
            if (!lexical.startsWith(lexicalRoot) || lexical == lexicalRoot) {
                return null
            }
            Path real = p.toRealPath()
            Path realRoot = lexicalRoot.toRealPath()
            if (!real.startsWith(realRoot) || real == realRoot || !Files.isRegularFile(real)) {
                return null
            }
            return real
        } catch (Exception ignored) {
            return null
        }
    }

    static Path dataFile(String path) {
        fileUnder(path, dataRoot())
    }

    /** Where the export of transaction txnId for ontId goes; null for an invalid id. */
    static Path exportFile(String ontId, String txnId) {
        if (!ontId || !txnId || !(ontId ==~ ID_PATTERN) || !(txnId ==~ TXN_PATTERN)) {
            return null
        }
        Paths.get(exportRoot(), txnId, ontId + ".owl").toAbsolutePath().normalize()
    }

    /** The real path of an existing export file, or null. */
    static Path existingExport(String path) {
        Path root = Paths.get(exportRoot())
        if (!Files.isDirectory(root)) {
            return null
        }
        fileUnder(path, root.toString())
    }

    /**
     * The id of a task in tasks (the servlet context's updateTasks) that is
     * still pending for ontId, or null. updateOntology and triggerIndexing
     * refuse to start a second task for an ontology while one runs: two
     * reloads of one ontology would interleave dispose, install and
     * classification. Call while holding the tasks map's lock.
     */
    static String activeTask(Map tasks, String ontId) {
        def hit = tasks.find { id, task -> task.ontologyId == ontId && task.status == 'pending' }
        return hit?.key
    }

    /** The id of a pending task reading path, or null. */
    static String taskReading(Map tasks, String path) {
        def hit = tasks.find { id, task -> task.owlPath == path && task.status == 'pending' }
        return hit?.key
    }

    /** Delete an export file and its transaction directory once empty. */
    static boolean deleteExport(Path export) {
        if (export == null) {
            return false
        }
        boolean deleted = Files.deleteIfExists(export)
        Path dir = export.getParent()
        try {
            Files.deleteIfExists(dir)  // only succeeds when empty
        } catch (IOException ignored) {
        }
        return deleted
    }
}
