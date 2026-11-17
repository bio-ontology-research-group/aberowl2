package src

import org.semanticweb.owlapi.io.FileDocumentSource
import org.semanticweb.owlapi.model.MissingImportHandlingStrategy
import org.semanticweb.owlapi.model.OWLOntology
import org.semanticweb.owlapi.model.IRI
import org.semanticweb.owlapi.model.OWLOntologyIRIMapper
import org.semanticweb.owlapi.model.OWLOntologyLoaderConfiguration
import org.semanticweb.owlapi.model.OWLOntologyManager

/** Read a worker validation export without rewriting its explicit axioms. */
class ValidationExportLoader {
    static OWLOntology load(OWLOntologyManager manager, File file, boolean validatedExport) {
        if (!validatedExport) return manager.loadOntologyFromOntologyDocument(file)
        def source = new FileDocumentSource(file)
        def configuration = new OWLOntologyLoaderConfiguration()
            .setMissingImportHandlingStrategy(MissingImportHandlingStrategy.SILENT)
        manager.getIRIMappers().clear()
        manager.addIRIMapper({ IRI ignored -> IRI.create('urn:aberowl:blocked-validation-import') } as OWLOntologyIRIMapper)
        OWLOntology ontology
        if (configuration.metaClass.respondsTo(configuration, 'setRepairIllegalPunnings', Boolean.TYPE)) {
            ontology = manager.loadOntologyFromOntologyDocument(source,
                configuration.setRepairIllegalPunnings(false))
        } else {
            // OWLAPI 4.2 runs fixIllegalPunnings unconditionally after parsing.
            // Its public factory API performs the same parse and registration
            // without that post-parse conversion of explicit annotations.
            def factory = manager.getOntologyFactories().find { it.canLoad(source) }
            if (factory == null) throw new IOException('No parser for the validation export')
            ontology = factory.loadOWLOntology(manager, source, manager, configuration)
            manager.setOntologyDocumentIRI(ontology, source.getDocumentIRI())
        }
        if (!ontology.getImportsDeclarations().isEmpty()) {
            manager.removeOntology(ontology)
            throw new IOException('A validation export must not declare imports')
        }
        return ontology
    }
}
