package src

import groovy.json.JsonSlurper

import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.NoSuchFileException
import java.nio.file.Path
import java.nio.file.Paths

import org.semanticweb.owlapi.io.FileDocumentSource
import org.semanticweb.owlapi.model.OWLImportsDeclaration
import org.semanticweb.owlapi.model.OWLOntology
import org.semanticweb.owlapi.model.OWLOntologyAlreadyExistsException
import org.semanticweb.owlapi.model.OWLOntologyCreationException
import org.semanticweb.owlapi.model.OWLOntologyLoaderConfiguration
import org.semanticweb.owlapi.model.OWLOntologyManager

/**
 * Opt-in, local-only resolution of owl:imports for one root ontology (#126).
 *
 * By default a worker suppresses every import (see LocalImportIRIMapper). A
 * root ontology opts in when the operator places a catalogue file at
 * <data root>/import_catalogues/<ontologyId>.json:
 *
 *   {
 *     "comment": "optional free text",
 *     "imports": {
 *       "http://purl.obolibrary.org/obo/po.owl":   "/data/po/po.owl",
 *       "http://purl.obolibrary.org/obo/pato.owl": "/data/pato/pato.owl"
 *     }
 *   }
 *
 * Each key is an import IRI exactly as it appears in an owl:imports
 * declaration; each value is a file under the data root. The entry is the
 * authority for that IRI: the file may declare a different ontology IRI, as
 * when an ontology imports a module by its download URL and the module
 * declares a PURL. Every import reached from the root, directly or
 * transitively, must be satisfied, either by an entry or by the ontology or
 * version IRI of a document already resolved; one IRI may never name two
 * files (see resolveClosure). Anything else fails the load with an
 * ImportResolutionException. Import IRIs are never dereferenced: the only
 * documents read are the listed files, and documents are never rewritten.
 *
 * The catalogue is read each time the ontology is loaded, so a reload or
 * hot-swap applies the catalogue as it is on disk at that moment.
 */
class ImportCatalogue {
    static final String CATALOGUE_DIR = "import_catalogues"
    static final long MAX_CATALOGUE_BYTES = 1024L * 1024L
    // The ontology id becomes a file name, so only ids that are safe as one
    // can opt in; any other id keeps the default suppression.
    private static final String ID_PATTERN = /[A-Za-z0-9][A-Za-z0-9._-]*/
    // An IRI with a scheme (RFC 3987); keys are compared as strings, never parsed further.
    private static final String ABSOLUTE_IRI = /[A-Za-z][A-Za-z0-9+.-]*:\S+/

    final String ontologyId
    final Path file
    // import IRI -> real path of a readable regular file under the data root
    final Map<String, Path> entries

    private ImportCatalogue(String ontologyId, Path file, Map<String, Path> entries) {
        this.ontologyId = ontologyId
        this.file = file
        this.entries = Collections.unmodifiableMap(entries)
    }

    /**
     * The catalogue that opts ontId in, or null when there is none. A file
     * that exists but is invalid (including a dangling symlink) is an error,
     * never a silent fallback to suppression.
     */
    static ImportCatalogue forOntology(String ontId, String dataRoot) throws ImportResolutionException {
        if (ontId == null || !(ontId ==~ ID_PATTERN) || dataRoot == null) {
            return null
        }
        Path candidate = Paths.get(dataRoot, CATALOGUE_DIR, ontId + ".json").toAbsolutePath()
        if (!Files.exists(candidate, LinkOption.NOFOLLOW_LINKS)) {
            return null
        }
        return parse(ontId, candidate, dataRoot)
    }

    static ImportCatalogue parse(String ontId, Path catalogueFile, String dataRoot) throws ImportResolutionException {
        String where = "Import catalogue ${catalogueFile} for ${ontId}"
        Path lexicalRoot = Paths.get(dataRoot).toAbsolutePath().normalize()
        Path realRoot
        try {
            realRoot = lexicalRoot.toRealPath()
        } catch (IOException e) {
            throw new ImportResolutionException("${where}: data root ${dataRoot} cannot be resolved: ${e.getMessage()}")
        }

        def (Path realFile, String fileProblem) = checkFile(catalogueFile.toString(), lexicalRoot, realRoot)
        if (fileProblem) {
            throw new ImportResolutionException("${where}: the catalogue file ${fileProblem}")
        }
        def json
        try {
            if (Files.size(realFile) > MAX_CATALOGUE_BYTES) {
                throw new ImportResolutionException("${where}: larger than ${MAX_CATALOGUE_BYTES} bytes")
            }
            json = new JsonSlurper().parse(realFile.toFile(), "UTF-8")
        } catch (ImportResolutionException e) {
            throw e
        } catch (Exception e) {
            throw new ImportResolutionException("${where}: not valid JSON: ${e.getMessage()}")
        }
        if (!(json instanceof Map)) {
            throw new ImportResolutionException("${where}: must be a JSON object with an \"imports\" object")
        }
        def unknown = json.keySet() - ["imports", "comment"]
        if (unknown) {
            throw new ImportResolutionException("${where}: unknown keys ${unknown.sort()}; allowed are \"imports\" and \"comment\"")
        }
        if (json.comment != null && !(json.comment instanceof String)) {
            throw new ImportResolutionException("${where}: \"comment\" must be a string")
        }
        if (!(json.imports instanceof Map)) {
            throw new ImportResolutionException("${where}: \"imports\" must be an object mapping import IRIs to files")
        }

        List<String> problems = []
        Map<String, Path> entries = new LinkedHashMap<>()
        json.imports.each { key, value ->
            String iri = key as String
            if (!iri || iri =~ /\s/) {
                problems << "import IRI '${iri}' is empty or contains whitespace"
                return
            }
            if (!(iri ==~ ABSOLUTE_IRI)) {
                problems << "import IRI '${iri}' is not an absolute IRI"
                return
            }
            if (!(value instanceof String)) {
                problems << "the file for <${iri}> must be a string path"
                return
            }
            def (Path realPath, String problem) = checkFile(value as String, lexicalRoot, realRoot)
            if (problem) {
                problems << "the file for <${iri}>, ${value}, ${problem}"
            } else {
                entries.put(iri, realPath)
            }
        }
        if (problems) {
            throw new ImportResolutionException("${where} is invalid: ${problems.join('; ')}")
        }
        return new ImportCatalogue(ontId, realFile, entries)
    }

    /**
     * Check that path names a readable regular file inside the data root, both
     * as written and after following every symlink. Returns [realPath, null]
     * or [null, problem].
     */
    private static List checkFile(String path, Path lexicalRoot, Path realRoot) {
        Path p
        try {
            p = Paths.get(path)
        } catch (Exception e) {
            return [null, "is not a valid path"]
        }
        if (!p.isAbsolute()) {
            return [null, "is not an absolute path"]
        }
        Path lexical = p.normalize()
        if (!lexical.startsWith(lexicalRoot) || lexical == lexicalRoot) {
            return [null, "is not under ${lexicalRoot}"]
        }
        Path real
        try {
            real = p.toRealPath()
        } catch (NoSuchFileException e) {
            return [null, "does not exist"]
        } catch (IOException e) {
            return [null, "cannot be resolved: ${e.getMessage()}"]
        }
        if (!real.startsWith(realRoot) || real == realRoot) {
            return [null, "resolves outside ${lexicalRoot} (to ${real})"]
        }
        if (!Files.isRegularFile(real)) {
            return [null, "is not a regular file"]
        }
        if (!Files.isReadable(real)) {
            return [null, "is not readable"]
        }
        return [real, null]
    }

    /**
     * Load every import reachable from root, each from its catalogue file, into
     * manager. Returns the closure, root first, with each document once, and
     * every IRI that names a document in it.
     *
     * The manager must suppress imports itself (LocalImportIRIMapper with the
     * SILENT strategy): this method decides what is loaded, and OWLAPI only
     * ever links an import to a document already in the manager.
     *
     * An IRI names a document once that document declares it as its ontology
     * or version IRI, or once an import of it has been resolved to that
     * document through the catalogue. The catalogue entry is the authority, so
     * an import IRI may name a file whose own ontology IRI differs (a document
     * URL for a module published under a PURL). An import of an IRI that
     * already names a document (a repeated import or alias, a cycle, a
     * self-import) is satisfied by it, and a file is loaded once per real path.
     * One IRI may never name two files: a loaded file that declares an IRI the
     * catalogue maps to another file, two files that declare the same IRI, or
     * an import satisfied by one file but listed with another, all fail.
     */
    ImportClosure resolveClosure(OWLOntologyManager manager, OWLOntology root, String rootPath,
                                 OWLOntologyLoaderConfiguration loaderConfig) throws ImportResolutionException {
        return resolveClosure(manager, root, rootPath, null, loaderConfig)
    }

    /**
     * As above, for a root read from a staged copy (an update) of the file it
     * is served from, servedRootPath. The root is then that file as well: an
     * entry listing the served file, for the root's own IRI or for an alias
     * that loops back to it, names the root, and the served file is never
     * loaded beside its staged copy. Every other identity check is unchanged.
     */
    ImportClosure resolveClosure(OWLOntologyManager manager, OWLOntology root, String rootPath,
                                 String servedRootPath, OWLOntologyLoaderConfiguration loaderConfig)
            throws ImportResolutionException {
        String where = "Imports of ${ontologyId} (catalogue ${file})"
        Set<OWLOntology> closure = new LinkedHashSet<>([root])
        Map<String, OWLOntology> byIri = [:]
        Map<Path, OWLOntology> byPath = [:]
        Map<OWLOntology, Path> pathOf = new IdentityHashMap<>()
        Path rootReal = realOrNormal(rootPath)
        Set<Path> rootPaths = new LinkedHashSet<>([rootReal])
        if (servedRootPath) {
            rootPaths.add(realOrNormal(servedRootPath))
        }
        register(where, root, rootReal, null, byIri, byPath, pathOf, rootPaths)
        rootPaths.each { byPath.put(it, root) }
        // Whether path is a file of document o: its own, or for the root, its served file too.
        Closure<Boolean> fileOf = { OWLOntology o, Path path ->
            path == pathOf.get(o) || (o.is(root) && rootPaths.contains(path))
        }

        Deque<OWLOntology> queue = new ArrayDeque<>([root])
        while (!queue.isEmpty()) {
            OWLOntology current = queue.poll()
            List<String> declared = current.getImportsDeclarations()
                .collect { OWLImportsDeclaration d -> d.getIRI().toString() }
                .sort()
            for (String iri : declared) {
                Path path = entries.get(iri)
                OWLOntology named = byIri.get(iri)
                if (named != null) {
                    if (path != null && !fileOf(named, path)) {
                        throw new ImportResolutionException(
                            "${where}: <${iri}> names ${pathOf.get(named)}, but the catalogue maps it to ${path}")
                    }
                    continue
                }
                if (path == null) {
                    throw new ImportResolutionException(
                        "${where}: <${iri}>, imported by ${describe(current)}, is not listed in the catalogue")
                }
                OWLOntology imported = byPath.get(path)
                if (imported == null) {
                    try {
                        imported = manager.loadOntologyFromOntologyDocument(
                            new FileDocumentSource(path.toFile()), loaderConfig)
                    } catch (OWLOntologyAlreadyExistsException e) {
                        throw new ImportResolutionException(
                            "${where}: ${path} (for <${iri}>) declares an ontology already loaded from another file: ${firstLine(e.getMessage())}", e)
                    } catch (OWLOntologyCreationException | RuntimeException e) {
                        throw new ImportResolutionException(
                            "${where}: ${path} (for <${iri}>) could not be loaded: ${e.getClass().getSimpleName()}: ${firstLine(e.getMessage())}", e)
                    }
                    register(where, imported, path, iri, byIri, byPath, pathOf, [path] as Set)
                    closure.add(imported)
                    queue.add(imported)
                }
                // the import IRI now names this file, whatever the file declares
                byIri.put(iri, imported)
            }
        }
        return new ImportClosure(closure, new HashSet<>(byIri.keySet()))
    }

    private static Path realOrNormal(String path) {
        try {
            return Paths.get(path).toRealPath()
        } catch (IOException ignored) {
            return Paths.get(path).toAbsolutePath().normalize()
        }
    }

    /**
     * Record a loaded document under its real path and under the IRIs it
     * declares, refusing an IRI that already names, or is listed for, a
     * file other than ownPaths (the document's path, and for a staged root
     * also its served file).
     */
    private void register(String where, OWLOntology o, Path path, String viaIri, Map<String, OWLOntology> byIri,
                          Map<Path, OWLOntology> byPath, Map<OWLOntology, Path> pathOf,
                          Set<Path> ownPaths) throws ImportResolutionException {
        String source = viaIri == null ? "the root ${path}" : "${path} (for <${viaIri}>)"
        for (String id : idsOf(o).sort()) {
            OWLOntology other = byIri.get(id)
            if (other != null && !other.is(o)) {
                throw new ImportResolutionException(
                    "${where}: ${source} declares <${id}>, which already names ${pathOf.get(other)}")
            }
            Path listed = entries.get(id)
            if (listed != null && !ownPaths.contains(listed)) {
                throw new ImportResolutionException(
                    "${where}: ${source} declares <${id}>, which the catalogue maps to ${listed}")
            }
        }
        idsOf(o).each { byIri.put(it, o) }
        byPath.put(path, o)
        pathOf.put(o, path)
    }

    /**
     * Import IRIs declared by members of closure that no member satisfies,
     * i.e. imports whose axioms are missing from a merge of closure. An import
     * is satisfied by a member's ontology or version IRI, or by an IRI in
     * resolvedIris (catalogue entries that were resolved to a member).
     */
    static List<String> unresolvedImports(Collection<OWLOntology> closure, Collection<String> resolvedIris) {
        Set<String> present = new HashSet<>(resolvedIris)
        closure.each { present.addAll(idsOf(it)) }
        Set<String> missing = new TreeSet<>()
        closure.each { o ->
            o.getImportsDeclarations().each { d ->
                String iri = d.getIRI().toString()
                if (!present.contains(iri)) {
                    missing.add(iri)
                }
            }
        }
        return new ArrayList<>(missing)
    }

    static Set<String> idsOf(OWLOntology o) {
        Set<String> ids = new HashSet<>()
        def id = o.getOntologyID()
        if (id.getOntologyIRI().isPresent()) {
            ids.add(id.getOntologyIRI().get().toString())
        }
        if (id.getVersionIRI().isPresent()) {
            ids.add(id.getVersionIRI().get().toString())
        }
        return ids
    }

    private static String describe(OWLOntology o) {
        def iri = o.getOntologyID().getOntologyIRI()
        return iri.isPresent() ? "<${iri.get()}>" : "an anonymous ontology"
    }

    private static String firstLine(String message) {
        if (message == null) {
            return ""
        }
        def line = message.readLines().find { it.trim() } ?: ""
        return line.length() > 300 ? line.substring(0, 300) + "..." : line
    }
}

/**
 * A resolved imports closure: the documents to merge, root first, and every
 * IRI that names one of them, including import IRIs that the catalogue
 * resolved to a document declaring a different ontology IRI.
 */
class ImportClosure {
    final Set<OWLOntology> ontologies
    final Set<String> resolvedIris

    ImportClosure(Set<OWLOntology> ontologies, Set<String> resolvedIris) {
        this.ontologies = Collections.unmodifiableSet(ontologies)
        this.resolvedIris = Collections.unmodifiableSet(resolvedIris)
    }
}
