package src

import java.nio.file.Path
import java.nio.file.Paths
import java.nio.file.Files
import java.security.MessageDigest
import org.semanticweb.owlapi.apibinding.OWLManager
import org.semanticweb.owlapi.io.FileDocumentSource
import org.semanticweb.owlapi.model.*

/** Reparse a proven local closure with import declarations available to the RDF parser. */
class ValidatedImportPlan {
    final Path rootPath
    final Map<Path, Map> documents
    final Map<String, Path> aliases
    final Set<String> resolvedIris

    ValidatedImportPlan(ImportCatalogue catalogue, OWLOntologyManager manager,
                        OWLOntology root, ImportClosure closure, String servedPath) {
        rootPath = documentPath(manager, root)
        documents = [:]
        aliases = [:]
        resolvedIris = new HashSet<>(closure.resolvedIris)
        closure.ontologies.each { ontology ->
            Path path = documentPath(manager, ontology)
            Set<String> ids = ImportCatalogue.idsOf(ontology)
            documents[path] = [ids: ids, imports: importsOf(ontology), digest: digest(path)]
            ids.each { aliases[it] = path }
        }
        Path served = servedPath ? Paths.get(servedPath).toRealPath() : rootPath
        catalogue.entries.each { iri, path ->
            Path actual = path == served ? rootPath : path
            if (documents.containsKey(actual)) {
                aliases[iri] = actual
            }
        }
        if (!aliases.keySet().containsAll(resolvedIris)) {
            throw new ImportResolutionException('Validated closure contains an unmapped import identity')
        }
    }

    Map load(OWLOntologyLoaderConfiguration configuration) {
        verifyBytes()
        def manager = OWLManager.createOWLOntologyManager()
        def blocked = new LocalImportIRIMapper()
        manager.addIRIMapper({ IRI iri ->
            Path path = aliases[iri.toString()]
            path == null ? blocked.getDocumentIRI(iri) : IRI.create(path.toFile())
        } as OWLOntologyIRIMapper)
        def root = manager.loadOntologyFromOntologyDocument(new FileDocumentSource(rootPath.toFile()), configuration)
        // A document URL may differ from its ontology IRI, or a vocabulary may
        // lack an ontology header. Include every validated file even if OWLAPI's
        // native getImportsClosure() does not connect that alias to the root.
        Set<Path> loaded = manager.ontologies.collect { documentPath(manager, it) } as Set
        documents.keySet().each { path ->
            if (!loaded.contains(path)) {
                manager.loadOntologyFromOntologyDocument(new FileDocumentSource(path.toFile()), configuration)
                loaded.add(path)
            }
        }
        verifyBytes()
        Set<OWLOntology> sources = new LinkedHashSet<>([root])
        manager.ontologies.each { ontology ->
            Path path = documentPath(manager, ontology)
            Map expected = documents[path]
            if (expected != null) {
                // OWLAPI may inline a headerless RDF vocabulary and remove its
                // import declaration (IMPORT_GRAPH). Restore only declarations
                // proven from unchanged bytes and backed by a separately loaded,
                // validated anonymous document; never excuse a new import.
                Set<String> missing = expected.imports - importsOf(ontology)
                missing.each { iri ->
                    Path target = aliases[iri]
                    if (target != null && documents[target]?.ids?.isEmpty()) {
                        manager.applyChange(new AddImport(ontology,
                            manager.OWLDataFactory.getOWLImportsDeclaration(IRI.create(iri))))
                    }
                }
            }
            if (expected == null || expected.ids != ImportCatalogue.idsOf(ontology)
                    || expected.imports != importsOf(ontology)) {
                throw new ImportResolutionException("Reparsed import identity or declarations changed: ${path}")
            }
            sources.add(ontology)
        }
        if (sources.size() != documents.size()) {
            throw new ImportResolutionException('Reparsed closure does not contain exactly the validated documents')
        }
        return [manager: manager, root: root, closure: new ImportClosure(sources, resolvedIris)]
    }

    private void verifyBytes() {
        documents.each { path, expected ->
            if (digest(path) != expected.digest) {
                throw new ImportResolutionException("Import document changed during validation: ${path}")
            }
        }
    }

    private static Set<String> importsOf(OWLOntology ontology) {
        ontology.importsDeclarations.collect { it.IRI.toString() } as Set
    }

    private static Path documentPath(OWLOntologyManager manager, OWLOntology ontology) {
        def iri = manager.getOntologyDocumentIRI(ontology)
        if (iri.toURI().scheme != 'file') {
            throw new ImportResolutionException("Import document is not a local file: ${iri}")
        }
        Paths.get(iri.toURI()).toRealPath()
    }

    private static String digest(Path path) {
        MessageDigest sha = MessageDigest.getInstance('SHA-256')
        Files.newInputStream(path).withCloseable { stream ->
            byte[] buffer = new byte[65536]
            int count
            while ((count = stream.read(buffer)) != -1) {
                sha.update(buffer, 0, count)
            }
        }
        sha.digest().encodeHex().toString()
    }
}
