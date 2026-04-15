package src

import org.semanticweb.elk.owlapi.ElkReasonerFactory
import org.semanticweb.elk.owlapi.ElkReasonerConfiguration
import org.semanticweb.elk.reasoner.config.ReasonerConfiguration

import org.semanticweb.owlapi.reasoner.*
import org.semanticweb.owlapi.reasoner.structural.StructuralReasonerFactory
import org.semanticweb.owlapi.model.OWLOntology

/**
 * Factory for creating OWL reasoners based on a string identifier.
 *
 * Supported reasoner types:
 *   - "elk"        (default) EL++ reasoner, fast, suitable for most OBO ontologies
 *   - "structural" OWLAPI built-in structural reasoner, no real inference
 *   - "hermit"     Full OWL DL reasoner (HermiT), slower but more complete
 *
 * To add a new reasoner:
 *   1. Add its @Grab dependency in OntologyServer.groovy
 *   2. Add a case to createReasoner() below
 */
public class ReasonerFactory {

    private static final String ELK_THREADS = "4"

    /**
     * Create a reasoner for the given ontology.
     *
     * @param type     Reasoner identifier string (case-insensitive). Defaults to "elk".
     * @param ontology The OWL ontology to reason over.
     * @return A configured OWLReasoner instance.
     */
    static OWLReasoner createReasoner(String type, OWLOntology ontology) {
        type = (type ?: "elk").toLowerCase().trim()
        switch (type) {
            case "elk":
                return createElkReasoner(ontology)
            case "structural":
                return createStructuralReasoner(ontology)
            case "hermit":
                return createHermiTReasoner(ontology)
            default:
                println "Unknown reasoner type '${type}', falling back to ELK"
                return createElkReasoner(ontology)
        }
    }

    /**
     * Create a structural reasoner (always needed for object property queries).
     */
    static OWLReasoner createStructuralReasoner(OWLOntology ontology) {
        def factory = new StructuralReasonerFactory()
        return factory.createReasoner(ontology)
    }

    /**
     * Create an ELK reasoner with configured thread count and incremental mode.
     */
    static OWLReasoner createElkReasoner(OWLOntology ontology) {
        def reasonerFactory = new ElkReasonerFactory()
        ReasonerConfiguration eConf = ReasonerConfiguration.getConfiguration()
        eConf.setParameter(ReasonerConfiguration.NUM_OF_WORKING_THREADS, ELK_THREADS)
        eConf.setParameter(ReasonerConfiguration.INCREMENTAL_MODE_ALLOWED, "true")

        OWLReasonerConfiguration rConf = new ElkReasonerConfiguration(
            ElkReasonerConfiguration.getDefaultOwlReasonerConfiguration(
                new NullReasonerProgressMonitor()), eConf)

        return reasonerFactory.createReasoner(ontology, rConf)
    }

    /**
     * Create a HermiT reasoner for full OWL DL reasoning.
     * Requires the HermiT @Grab dependency in OntologyServer.groovy.
     */
    static OWLReasoner createHermiTReasoner(OWLOntology ontology) {
        try {
            def factoryClass = Class.forName("org.semanticweb.HermiT.ReasonerFactory")
            OWLReasonerFactory factory = factoryClass.getDeclaredConstructor().newInstance()
            OWLReasonerConfiguration conf = new SimpleConfiguration(new NullReasonerProgressMonitor())
            return factory.createReasoner(ontology, conf)
        } catch (ClassNotFoundException e) {
            throw new RuntimeException(
                "HermiT reasoner not available. Add the HermiT @Grab dependency to OntologyServer.groovy.", e)
        }
    }

    /**
     * Check whether a given reasoner type string is recognized.
     */
    static boolean isSupported(String type) {
        return (type ?: "").toLowerCase().trim() in ["elk", "structural", "hermit"]
    }

    /**
     * List all supported reasoner type identifiers.
     */
    static List<String> supportedTypes() {
        return ["elk", "structural", "hermit"]
    }
}
