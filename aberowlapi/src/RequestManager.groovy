package src

import org.semanticweb.elk.owlapi.ElkReasonerFactory
import org.semanticweb.elk.owlapi.ElkReasonerConfiguration
import org.semanticweb.elk.reasoner.config.*

import org.semanticweb.owlapi.apibinding.OWLManager
import org.semanticweb.owlapi.reasoner.*
import org.semanticweb.owlapi.reasoner.structural.StructuralReasoner
import org.semanticweb.owlapi.reasoner.structural.StructuralReasonerFactory
import org.semanticweb.owlapi.vocab.OWLRDFVocabulary
import org.semanticweb.owlapi.model.*
import org.semanticweb.owlapi.io.*
import org.semanticweb.owlapi.owllink.*
import org.semanticweb.owlapi.util.*
import org.semanticweb.owlapi.search.*
import org.semanticweb.owlapi.manchestersyntax.renderer.*
import org.semanticweb.owlapi.reasoner.structural.*
import org.semanticweb.owlapi.expression.ShortFormEntityChecker
import org.semanticweb.owlapi.manchestersyntax.parser.ManchesterOWLSyntaxParserImpl
import org.semanticweb.owlapi.util.BidirectionalShortFormProviderAdapter

import java.util.concurrent.*
import java.util.concurrent.atomic.*
import java.util.timer.*
import java.io.File

import groovy.json.*
import groovy.io.*
import com.google.common.collect.*
import org.semanticweb.owlapi.model.UnloadableImportException
import src.IRIOnlyShortFormProvider
import src.ReasonerFactory
import groovyx.gpars.GParsPool


/**
 * Manages one or more ontologies within a single JVM container.
 *
 * Each ontology is identified by a string ontologyId and has its own
 * OWLOntologyManager, OWLReasoner, QueryEngine, and ShortFormProviders.
 *
 * Thread-safe: all per-ontology state is stored in ConcurrentHashMaps.
 */
public class RequestManager {
    private static final int MAX_UNSATISFIABLE_CLASSES = 500
    private static final int MAX_REASONER_RESULTS = 100000
    private static final int PARALLEL_THREADS = 4

    OWLDataFactory df = OWLManager.getOWLDataFactory()

    // Per-ontology state maps (keyed by ontologyId)
    final ConcurrentHashMap<String, OWLOntologyManager> oManagers = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, OWLOntology> ontologies = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, OWLReasoner> reasoners = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, OWLReasoner> structReasoners = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, QueryEngine> queryEngines = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, NewShortFormProvider> shortFormProviders = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, IRIOnlyShortFormProvider> iriShortFormProviders = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, String> ontologyPaths = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, String> reasonerTypes = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, String> loadStati = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, String> exampleSuperclassLabels = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, String> exampleSubclassExpressions = new ConcurrentHashMap<>()
    final ConcurrentHashMap<String, String> exampleSubclassExpressionTexts = new ConcurrentHashMap<>()

    // Shared annotation property lists
    def aProperties = [
        df.getRDFSLabel(),
        df.getOWLAnnotationProperty(IRI.create("http://www.geneontology.org/formats/oboInOwl#hasNarrowSynonym")),
        df.getOWLAnnotationProperty(IRI.create("http://www.geneontology.org/formats/oboInOwl#hasBroadSynonym")),
        df.getOWLAnnotationProperty(IRI.create("http://www.geneontology.org/formats/oboInOwl#hasExactSynonym"))
    ]

    def identifiers = [
        df.getOWLAnnotationProperty(IRI.create('http://purl.org/dc/elements/1.1/identifier')),
    ]

    def labels = [
        df.getRDFSLabel(),
        df.getOWLAnnotationProperty(IRI.create('http://www.w3.org/2004/02/skos/core#prefLabel')),
        df.getOWLAnnotationProperty(IRI.create('http://purl.obolibrary.org/obo/IAO_0000111'))
    ]
    def synonyms = [
        df.getOWLAnnotationProperty(IRI.create('http://www.w3.org/2004/02/skos/core#altLabel')),
        df.getOWLAnnotationProperty(IRI.create('http://purl.obolibrary.org/obo/IAO_0000118')),
        df.getOWLAnnotationProperty(IRI.create('http://www.geneontology.org/formats/oboInOwl#hasExactSynonym')),
        df.getOWLAnnotationProperty(IRI.create('http://www.geneontology.org/formats/oboInOwl#hasSynonym')),
        df.getOWLAnnotationProperty(IRI.create('http://www.geneontology.org/formats/oboInOwl#hasNarrowSynonym')),
        df.getOWLAnnotationProperty(IRI.create('http://www.geneontology.org/formats/oboInOwl#hasBroadSynonym'))
    ]
    def definitions = [
        df.getOWLAnnotationProperty(IRI.create('http://purl.obolibrary.org/obo/IAO_0000115')),
        df.getOWLAnnotationProperty(IRI.create('http://www.w3.org/2004/02/skos/core#definition')),
        df.getOWLAnnotationProperty(IRI.create('http://purl.org/dc/elements/1.1/description')),
        df.getOWLAnnotationProperty(IRI.create('http://purl.org/dc/terms/description')),
        df.getOWLAnnotationProperty(IRI.create('http://www.geneontology.org/formats/oboInOwl#hasDefinition'))
    ]

    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    public RequestManager() {
        // Empty multi-ontology manager
    }

    // -----------------------------------------------------------------------
    // Single-ontology convenience (backward compatibility)
    // -----------------------------------------------------------------------

    /**
     * Create a RequestManager with a single ontology loaded (backward compat).
     */
    public static RequestManager create(String ontId, String ontIRI) {
        return create(ontId, ontIRI, "elk")
    }

    public static RequestManager create(String ontId, String ontIRI, String reasonerType) {
        RequestManager mgr = new RequestManager()
        try {
            println("Starting manager for $ontId")
            mgr.loadOntology(ontId, ontIRI, reasonerType)
            mgr.createReasoner(ontId)
            println("Finished loading $ontId")
            return mgr
        } catch (UnloadableImportException e) {
            println("Unloadable ontology $ontId")
            e.printStackTrace()
            return null
        } catch (Exception e) {
            println("Failed loading $ontId")
            e.printStackTrace()
            return null
        }
    }

    // -----------------------------------------------------------------------
    // Ontology loading
    // -----------------------------------------------------------------------

    /**
     * Load a single ontology from a file path.
     */
    void loadOntology(String ontId, String ontIRI) {
        loadOntology(ontId, ontIRI, "elk")
    }

    void loadOntology(String ontId, String ontIRI, String reasonerType) {
        println "Loading ontology ${ontId} from ${ontIRI}"
        loadStati.put(ontId, "loading")
        reasonerTypes.put(ontId, reasonerType ?: "elk")
        ontologyPaths.put(ontId, ontIRI)

        OWLOntologyManager lManager = OWLManager.createOWLOntologyManager()

        def originalOntology = lManager.loadOntologyFromOntologyDocument(new File(ontIRI))
        IRI originalOntologyIRI = originalOntology.getOntologyID().getOntologyIRI().orNull()
        Set<OWLAnnotation> originalAnnotations = originalOntology.getAnnotations().collect()

        // Merge imports closure into a single ontology
        OWLOntologyImportsClosureSetProvider provider = new OWLOntologyImportsClosureSetProvider(lManager, originalOntology)
        OWLOntologyMerger merger = new OWLOntologyMerger(provider, false)
        def mergedOntology = merger.createMergedOntology(lManager, originalOntologyIRI ?: IRI.create("http://merged.owl"))

        originalAnnotations.each { annotation ->
            lManager.applyChange(new AddOntologyAnnotation(mergedOntology, annotation))
        }

        ontologies.put(ontId, mergedOntology)
        oManagers.put(ontId, lManager)
        loadStati.put(ontId, "loaded")
        println "Loaded ontology ${ontId}"
    }

    // -----------------------------------------------------------------------
    // Reasoner creation
    // -----------------------------------------------------------------------

    /**
     * Classify a single ontology with its configured reasoner.
     */
    void createReasoner(String ontId) {
        def ontology = ontologies.get(ontId)
        def manager = oManagers.get(ontId)
        def rType = reasonerTypes.get(ontId) ?: "elk"
        if (ontology == null || manager == null) {
            throw new IllegalArgumentException("Ontology not loaded: ${ontId}")
        }

        println "Classifying ${ontId} with reasoner: ${rType}"
        loadStati.put(ontId, "classifying")

        List<String> langs = new ArrayList<>()
        Map<OWLAnnotationProperty, List<String>> preferredLanguageMap = new HashMap<>()
        for (OWLAnnotationProperty annotationProperty : this.aProperties) {
            preferredLanguageMap.put(annotationProperty, langs)
        }

        // Create the main reasoner
        OWLReasoner oReasoner = src.ReasonerFactory.createReasoner(rType, ontology)
        oReasoner.precomputeInferences(InferenceType.CLASS_HIERARCHY)

        // Always create a structural reasoner for object property queries
        OWLReasoner sReasoner = src.ReasonerFactory.createStructuralReasoner(ontology)

        def sfp = new NewShortFormProvider(this.aProperties, preferredLanguageMap, manager)
        def iriSfp = new IRIOnlyShortFormProvider(manager.getOntologies())

        // Dispose old reasoners if present
        def oldQE = queryEngines.get(ontId)
        oldQE?.getoReasoner()?.dispose()

        // Check for excessive unsatisfiable classes -> fall back to structural
        def unsatCount = oReasoner.getEquivalentClasses(df.getOWLNothing()).getEntitiesMinusBottom().size()
        if (unsatCount >= MAX_UNSATISFIABLE_CLASSES) {
            oReasoner.dispose()
            oReasoner = sReasoner
            loadStati.put(ontId, "incoherent")
            println "Classified ${ontId} but switched to structural reasoner (${unsatCount} unsatisfiable classes)"
        } else {
            loadStati.put(ontId, "classified")
            println "Successfully classified ${ontId}"
        }

        reasoners.put(ontId, oReasoner)
        structReasoners.put(ontId, sReasoner)
        queryEngines.put(ontId, new QueryEngine(oReasoner, sfp))
        shortFormProviders.put(ontId, sfp)
        iriShortFormProviders.put(ontId, iriSfp)

        findExampleClassesAndExpressions(ontId)
        println "Classification complete for ${ontId}"
    }

    /**
     * Classify all loaded ontologies in parallel using GParsPool.
     */
    void createAllReasoners() {
        def ontIds = new ArrayList(ontologies.keySet())
        if (ontIds.isEmpty()) {
            println "No ontologies to classify"
            return
        }
        println "Classifying ${ontIds.size()} ontologies in parallel..."
        GParsPool.withPool(PARALLEL_THREADS) {
            ontIds.eachParallel { ontId ->
                try {
                    createReasoner(ontId)
                } catch (Exception e) {
                    loadStati.put(ontId, "error")
                    println "ERROR classifying ${ontId}: ${e.getMessage()}"
                    e.printStackTrace()
                }
            }
        }
        println "All ontologies classified"
    }

    // -----------------------------------------------------------------------
    // Ontology lifecycle
    // -----------------------------------------------------------------------

    /**
     * Reload (hot-swap) a single ontology from a new file path.
     */
    void reloadOntology(String ontId, String ontIRI) {
        reloadOntology(ontId, ontIRI, reasonerTypes.get(ontId) ?: "elk")
    }

    void reloadOntology(String ontId, String ontIRI, String reasonerType) {
        println "Reloading ontology ${ontId} from ${ontIRI}"
        disposeOntology(ontId)
        loadOntology(ontId, ontIRI, reasonerType)
        createReasoner(ontId)
        println "Reloaded ontology ${ontId}"
    }

    /**
     * Dispose all resources for a specific ontology.
     */
    void disposeOntology(String ontId) {
        try {
            def oReasoner = reasoners.remove(ontId)
            def sReasoner = structReasoners.remove(ontId)
            try { oReasoner?.dispose() } catch (Exception e) {
                println "Error disposing reasoner for ${ontId}: ${e.getMessage()}"
            }
            if (sReasoner != null && sReasoner !== oReasoner) {
                try { sReasoner.dispose() } catch (Exception e) {
                    println "Error disposing struct reasoner for ${ontId}: ${e.getMessage()}"
                }
            }
        } catch (Exception e) {
            println "Error during dispose for ${ontId}: ${e.getMessage()}"
        }
        queryEngines.remove(ontId)
        shortFormProviders.remove(ontId)
        iriShortFormProviders.remove(ontId)
        ontologies.remove(ontId)
        oManagers.remove(ontId)
        ontologyPaths.remove(ontId)
        reasonerTypes.remove(ontId)
        loadStati.remove(ontId)
        exampleSuperclassLabels.remove(ontId)
        exampleSubclassExpressions.remove(ontId)
        exampleSubclassExpressionTexts.remove(ontId)
        println "Disposed all resources for ${ontId}"
    }

    /**
     * Dispose all ontologies and release all resources.
     */
    public void disposeAll() {
        def ontIds = new ArrayList(ontologies.keySet())
        ontIds.each { ontId ->
            disposeOntology(ontId)
        }
        println "Disposed all ontologies"
    }

    // -----------------------------------------------------------------------
    // Queries
    // -----------------------------------------------------------------------

    /**
     * List all loaded ontology IDs with their status and reasoner type.
     */
    List<Map> listOntologies() {
        return ontologies.keySet().collect { ontId ->
            [
                ontologyId: ontId,
                status: loadStati.get(ontId) ?: "unknown",
                reasonerType: reasonerTypes.get(ontId) ?: "unknown",
                path: ontologyPaths.get(ontId) ?: "",
                classCount: ontologies.get(ontId)?.getClassesInSignature(true)?.size() ?: 0
            ]
        }
    }

    /**
     * Check if a specific ontology is loaded.
     */
    boolean hasOntology(String ontId) {
        return ontologies.containsKey(ontId)
    }

    /**
     * Get the status of a specific ontology.
     */
    String getStatus(String ontId) {
        return loadStati.get(ontId)
    }

    /**
     * Run a DL query against a specific ontology (by OWLClassExpression).
     */
    Set runQuery(String ontId, OWLClassExpression mOwlQuery, String type, boolean direct, boolean labels, boolean axioms, String shortform) {
        def qEngine = queryEngines.get(ontId)
        if (qEngine == null) {
            throw new IllegalArgumentException("Ontology not loaded or not classified: ${ontId}")
        }

        type = type.toLowerCase()
        def requestType
        switch (type) {
            case "superclass": requestType = RequestType.SUPERCLASS; break
            case "subclass": requestType = RequestType.SUBCLASS; break
            case "equivalent": requestType = RequestType.EQUIVALENT; break
            case "supeq": requestType = RequestType.SUPEQ; break
            case "subeq": requestType = RequestType.SUBEQ; break
            case "realize": requestType = RequestType.REALIZE; break
            default: requestType = RequestType.SUBEQ; break
        }

        def currentSfp = (shortform == 'iri') ? iriShortFormProviders.get(ontId) : shortFormProviders.get(ontId)

        Set resultSet = Sets.newHashSet(Iterables.limit(qEngine.getClasses(mOwlQuery, requestType, direct, labels), MAX_REASONER_RESULTS))
        resultSet.remove(df.getOWLNothing())
        resultSet.remove(df.getOWLThing())
        def classes = classes2info(ontId, resultSet, axioms, currentSfp)
        return classes.sort { x, y -> x["label"].compareTo(y["label"]) }
    }

    /**
     * Run a DL query against a specific ontology (by string, Manchester OWL Syntax or IRI).
     */
    Set runQuery(String ontId, String mOwlQuery, String type, boolean direct, boolean labels, boolean axioms, String shortform) {
        if (mOwlQuery.startsWith("http") || mOwlQuery.startsWith("<")) {
            type = type.toLowerCase()
            def requestType
            switch (type) {
                case "superclass": requestType = RequestType.SUPERCLASS; break
                case "subclass": requestType = RequestType.SUBCLASS; break
                case "equivalent": requestType = RequestType.EQUIVALENT; break
                case "supeq": requestType = RequestType.SUPEQ; break
                case "subeq": requestType = RequestType.SUBEQ; break
                case "realize": requestType = RequestType.REALIZE; break
                default: requestType = RequestType.SUBEQ; break
            }

            def qEngine = queryEngines.get(ontId)
            if (qEngine == null) {
                throw new IllegalArgumentException("Ontology not loaded or not classified: ${ontId}")
            }

            def currentSfp = (shortform == 'iri') ? iriShortFormProviders.get(ontId) : shortFormProviders.get(ontId)

            Set resultSet = Sets.newHashSet(Iterables.limit(qEngine.getClasses(mOwlQuery, requestType, direct, labels), MAX_REASONER_RESULTS))
            resultSet.remove(df.getOWLNothing())
            resultSet.remove(df.getOWLThing())
            def classes = classes2info(ontId, resultSet, axioms, currentSfp)
            return classes.sort { x, y -> x["label"].compareTo(y["label"]) }
        } else {
            def ont = ontologies.get(ontId)
            if (ont == null) {
                throw new IllegalArgumentException("Ontology not loaded: ${ontId}")
            }
            def sfp = new NewShortFormProvider(ont.getImportsClosure())
            def bidiSfp = new BidirectionalShortFormProviderAdapter(ont.getImportsClosure(), sfp)
            def checker = new ShortFormEntityChecker(bidiSfp)
            def dataFactory = ont.getOWLOntologyManager().getOWLDataFactory()
            def configSupplier = { -> ont.getOWLOntologyManager().getOntologyLoaderConfiguration() }
            def parser = new org.semanticweb.owlapi.manchestersyntax.parser.ManchesterOWLSyntaxParserImpl(configSupplier, dataFactory)
            parser.setStringToParse(mOwlQuery)
            parser.setOWLEntityChecker(checker)
            def expression = parser.parseClassExpression()
            return runQuery(ontId, expression, type, direct, labels, axioms, shortform)
        }
    }

    Set runQuery(String ontId, String mOwlQuery, String type, boolean direct, boolean labels, boolean axioms) {
        return runQuery(ontId, mOwlQuery, type, direct, labels, axioms, null)
    }

    Set runQuery(String ontId, String mOwlQuery, String type) {
        return runQuery(ontId, mOwlQuery, type, false, false, false, null)
    }

    // Backward-compatible: single-ontology runQuery (uses first loaded ontology)
    Set runQuery(String mOwlQuery, String type, boolean direct, boolean labels, boolean axioms, String shortform) {
        def ontId = getDefaultOntologyId()
        return runQuery(ontId, mOwlQuery, type, direct, labels, axioms, shortform)
    }

    Set runQuery(String mOwlQuery, String type, boolean direct, boolean labels, boolean axioms) {
        return runQuery(mOwlQuery, type, direct, labels, axioms, null)
    }

    Set runQuery(String mOwlQuery, String type) {
        return runQuery(mOwlQuery, type, false, false, false, null)
    }

    /**
     * Return the direct R-successors of a class C in ontology.
     */
    Set relationQuery(String ontId, String relation, String cl) {
        Set classes = new HashSet<>()
        def qEngine = queryEngines.get(ontId)
        if (qEngine == null) {
            throw new IllegalArgumentException("Ontology not loaded or not classified: ${ontId}")
        }

        Set<OWLClass> subclasses = qEngine.getClasses(cl, RequestType.SUBCLASS, true, false)
        String query1 = "<$relation> SOME $cl"
        Set<OWLClass> mainResult = qEngine.getClasses(query1, RequestType.SUBCLASS, true, false)
        subclasses.each { sc ->
            String query2 = "$relation SOME " + sc.toString()
            def subResult = qEngine.getClasses(query2, RequestType.SUBCLASS, true, false)
            mainResult = mainResult - subResult
        }
        def sfp = shortFormProviders.get(ontId)
        classes.addAll(classes2info(ontId, mainResult, false, sfp))
        return classes
    }

    // Backward-compatible
    Set relationQuery(String relation, String cl) {
        return relationQuery(getDefaultOntologyId(), relation, cl)
    }

    // -----------------------------------------------------------------------
    // Entity info
    // -----------------------------------------------------------------------

    def toInfo(String ontId, OWLEntity c, boolean axioms, shortFormProvider) {
        def o = ontologies.get(ontId)
        if (o == null) {
            throw new IllegalArgumentException("Ontology not loaded: ${ontId}")
        }
        def sfp = shortFormProvider ?: shortFormProviders.get(ontId)

        def info = [
            "owlClass": c.toString(),
            "class": c.getIRI().toString(),
            "ontology": ontId,
            "deprecated": false
        ].withDefault { key -> [] }

        def hasLabel = false
        def hasAnnot = false

        EntitySearcher.getAnnotationAssertionAxioms(c, o).each { axiom ->
            hasAnnot = true
            def annot = axiom.getAnnotation()
            def aProp = axiom.getProperty()
            if (annot.isDeprecatedIRIAnnotation()) {
                info["deprecated"] = true
            } else if (aProp in this.identifiers) {
                if (annot.getValue() instanceof OWLLiteral) {
                    def aVal = annot.getValue().getLiteral()
                    info['identifier'] << aVal
                }
            } else if (aProp in this.labels) {
                if (annot.getValue() instanceof OWLLiteral) {
                    def aVal = annot.getValue().getLiteral()
                    info['label'] = addSpacesToCamelCase(aVal)
                    hasLabel = true
                }
            } else if (aProp in this.definitions) {
                if (annot.getValue() instanceof OWLLiteral) {
                    def aVal = annot.getValue().getLiteral()
                    info["definition"] << aVal
                }
            } else if (aProp in this.synonyms) {
                if (annot.getValue() instanceof OWLLiteral) {
                    def aVal = annot.getValue().getLiteral()
                    info["synonyms"] << aVal
                }
            } else {
                if (annot.getValue() instanceof OWLLiteral) {
                    try {
                        def aVal = annot.getValue().getLiteral()
                        def aLabels = EntitySearcher.getAnnotations(aProp, o)
                        if (aLabels.size() > 0) {
                            aLabels.each { l ->
                                if (l.getValue() instanceof OWLLiteral) {
                                    def lab = l.getValue().getLiteral()
                                    info[lab].add(aVal)
                                }
                            }
                        } else {
                            def prop = sfp.getShortForm(aProp)
                            info[prop].add(aVal)
                        }
                    } catch (Exception e) {
                    }
                }
            }
        }

        if (!hasLabel) {
            info["label"] = sfp.getShortForm(c)
        }

        if (!hasAnnot) {
            info["deprecated"] = true
        }

        if (axioms) {
            def manSyntaxRenderer = new AberOWLSyntaxRendererImpl()
            manSyntaxRenderer.setShortFormProvider(sfp)

            EntitySearcher.getSuperClasses(c, o).each { cExpr ->
                info["SubClassOf"] << cleanRenderedAxiom(manSyntaxRenderer.render(cExpr))
            }
            EntitySearcher.getEquivalentClasses(c, o).each { cExpr ->
                info["Equivalent"] << cleanRenderedAxiom(manSyntaxRenderer.render(cExpr))
            }
            EntitySearcher.getDisjointClasses(c, o).each { cExpr ->
                info["Disjoint"] << cleanRenderedAxiom(manSyntaxRenderer.render(cExpr))
            }
        }
        return info
    }

    // Backward-compatible
    def toInfo(OWLEntity c, boolean axioms, shortFormProvider) {
        return toInfo(getDefaultOntologyId(), c, axioms, shortFormProvider)
    }

    ArrayList<HashMap> classes2info(String ontId, Set<OWLClass> classes, boolean axioms, shortFormProvider) {
        ArrayList<HashMap> result = new ArrayList<HashMap>()
        classes.each { c ->
            def info = toInfo(ontId, c, axioms, shortFormProvider)
            if (!info["deprecated"]) {
                result.add(info)
            }
        }
        return result
    }

    // Backward-compatible
    ArrayList<HashMap> classes2info(Set<OWLClass> classes, boolean axioms, shortFormProvider) {
        return classes2info(getDefaultOntologyId(), classes, axioms, shortFormProvider)
    }

    // -----------------------------------------------------------------------
    // Object properties
    // -----------------------------------------------------------------------

    def getObjectProperties(String ontId) {
        return getObjectProperties(ontId, df.getOWLTopObjectProperty())
    }

    def getObjectProperties(String ontId, String prop) {
        def objProp = df.getOWLObjectProperty(IRI.create(prop))
        return getObjectProperties(ontId, objProp)
    }

    def getObjectProperties(String ontId, OWLObjectProperty prop) {
        def sReasoner = structReasoners.get(ontId)
        if (sReasoner == null) {
            throw new IllegalArgumentException("Ontology not loaded: ${ontId}")
        }
        def sfp = shortFormProviders.get(ontId)
        def subProps = sReasoner.getSubObjectProperties(prop, true).getFlattened()
        subProps.remove(df.getOWLBottomObjectProperty())
        subProps.remove(df.getOWLTopObjectProperty())
        def used = new HashSet<OWLObjectProperty>()
        def result = []
        for (def expression : subProps) {
            def objProp = expression.getNamedProperty()
            if (!used.contains(objProp)) {
                result.add(toInfo(ontId, objProp, false, sfp))
                used.add(objProp)
            }
        }
        return ["result": result.sort { x, y -> x["label"].compareTo(y["label"]) }]
    }

    // Backward-compatible (no-arg only; single-String overload removed to avoid Groovy ambiguity)
    def getObjectProperties() {
        return getObjectProperties(getDefaultOntologyId())
    }

    // -----------------------------------------------------------------------
    // SPARQL examples
    // -----------------------------------------------------------------------

    def getSparqlExamples(String ontId) {
        return [
            exampleSuperclassLabel: exampleSuperclassLabels.get(ontId),
            exampleSubclassExpression: exampleSubclassExpressions.get(ontId),
            exampleSubclassExpressionText: exampleSubclassExpressionTexts.get(ontId)
        ]
    }

    // Backward-compatible
    def getSparqlExamples() {
        return getSparqlExamples(getDefaultOntologyId())
    }

    // -----------------------------------------------------------------------
    // Accessors
    // -----------------------------------------------------------------------

    OWLOntologyManager getoManager(String ontId) {
        return oManagers.get(ontId)
    }

    def getOntology(String ontId) {
        return ontologies.get(ontId)
    }

    QueryEngine getQueryEngine(String ontId) {
        return queryEngines.get(ontId)
    }

    // Backward-compatible: return first ontology
    OWLOntologyManager getoManager() {
        return oManagers.get(getDefaultOntologyId())
    }

    def getOntology() {
        return ontologies.get(getDefaultOntologyId())
    }

    def getQueryEngine() {
        return queryEngines.get(getDefaultOntologyId())
    }

    // For backward compat: expose ont name of default ontology
    String getOnt() {
        return getDefaultOntologyId()
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    /**
     * Get the default ontology ID (first loaded). Used for backward compatibility.
     */
    String getDefaultOntologyId() {
        if (ontologies.isEmpty()) {
            throw new IllegalStateException("No ontologies loaded")
        }
        return ontologies.keySet().iterator().next()
    }

    void findExampleClassesAndExpressions(String ontId) {
        def ontology = ontologies.get(ontId)
        def sfp = shortFormProviders.get(ontId)
        if (ontology == null || sfp == null) return

        def classes = ontology.getClassesInSignature(true)
        OWLClass exampleSuperclass = null

        for (def cls : classes) {
            if (!cls.isOWLThing() && !cls.isOWLNothing()) {
                exampleSuperclass = cls
                break
            }
        }

        if (exampleSuperclass != null) {
            exampleSuperclassLabels.put(ontId, sfp.getShortForm(exampleSuperclass))
        }

        def manSyntaxHTMLRenderer = new AberOWLSyntaxRendererImpl()
        manSyntaxHTMLRenderer.setShortFormProvider(sfp)
        for (def cls : classes) {
            def subClassAxioms = ontology.getSubClassAxiomsForSubClass(cls)
            for (def axiom : subClassAxioms) {
                def superClass = axiom.getSuperClass()
                if (superClass.isAnonymous()) {
                    def rendered = manSyntaxHTMLRenderer.render(superClass)
                    exampleSubclassExpressions.put(ontId, rendered)
                    def text = rendered
                        .replaceAll("</span>", "</span> ")
                        .replaceAll("<[^>]+>", "")
                        .replaceAll("&gt;", ">")
                        .replaceAll("&lt;", "<")
                        .replaceAll("&amp;", "&")
                        .replaceAll("&quot;", "\"")
                        .replaceAll("&#39;", "'")
                        .replaceAll("'>", "")
                        .replaceAll("'<", "")
                        .trim()
                        .replaceAll("\\s+", " ")
                    exampleSubclassExpressionTexts.put(ontId, text)
                    return
                }
            }
        }
    }

    /**
     * Strip HTML tags and clean up IRI artifacts from rendered Manchester OWL syntax.
     * The AberOWLSyntaxRenderer produces HTML with <a href='#/Browse/<...>'>label</a>
     * and <span> tags. Stripping HTML can leave behind '>' and '<' fragments from
     * href attributes. This method produces clean plain text Manchester OWL syntax.
     */
    private String cleanRenderedAxiom(String rendered) {
        if (rendered == null) return ""
        return rendered
            .replaceAll("</span>", " ")         // Replace closing spans with space
            .replaceAll("<[^>]+>", "")           // Remove all HTML tags
            .replaceAll("&gt;", ">")             // Decode HTML entities
            .replaceAll("&lt;", "<")
            .replaceAll("&amp;", "&")
            .replaceAll("&quot;", "\"")
            .replaceAll("&#39;", "'")
            .replaceAll("'>", "")                // Remove IRI quote artifacts
            .replaceAll("'<", "")
            .trim()
            .replaceAll("\\s+", " ")             // Normalize whitespace
    }

    private String addSpacesToCamelCase(String text) {
        if (text == null || text.isEmpty()) {
            return text
        }
        if (text.contains(" ") || text.contains("_")) {
            return text
        }
        StringBuilder result = new StringBuilder()
        result.append(text.charAt(0))
        for (int i = 1; i < text.length(); i++) {
            char current = text.charAt(i)
            char previous = text.charAt(i - 1)
            if (Character.isUpperCase(current) && Character.isLowerCase(previous)) {
                result.append(' ')
            } else if (Character.isDigit(current) && Character.isLetter(previous)) {
                result.append(' ')
            }
            result.append(current)
        }
        return result.toString()
    }
}
