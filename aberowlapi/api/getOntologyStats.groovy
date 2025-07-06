import groovy.json.JsonOutput
import org.semanticweb.owlapi.model.AxiomType
import org.semanticweb.owlapi.model.IRI
import org.semanticweb.owlapi.model.OWLClass
import org.semanticweb.owlapi.model.OWLLiteral
import org.semanticweb.owlapi.model.parameters.Imports
import org.semanticweb.owlapi.reasoner.InferenceType
import org.semanticweb.owlapi.util.DLExpressivityChecker
import org.semanticweb.owlapi.reasoner.OWLReasoner

import java.util.Collections
import java.util.HashMap
import java.util.HashSet
import java.util.LinkedList
import java.util.Queue
import java.util.Set

if(!application) {
    application = request.getApplication(true);
}

def manager = application.manager
def ontology = application.ontology

if(!ontology || !manager) {
    response.setContentType("application/json")
    out << JsonOutput.toJson([status: "error", message: "Ontology or manager not loaded."])
    return
}

def stats = [:]

// === Metadata from Ontology Annotations ===
try {
    def metadata = [:]
    ontology.getAnnotations().each { annotation ->
        def propertyIRI = annotation.getProperty().getIRI().toString()
        def value = annotation.getValue()
        def key = propertyIRI.split('#').pop().split('/').pop()
        if (!metadata.containsKey(key)) {
            metadata[key] = []
        }

        if (value instanceof OWLLiteral) {
            metadata[key].add(value.getLiteral())
        } else if (value instanceof IRI) {
            metadata[key].add(value.toString())
        }
    }
    stats.metadata = metadata
} catch (Exception e) {
    System.err.println("Could not extract metadata: " + e.getMessage())
    e.printStackTrace()
    stats.metadata = [:]
}


// === Axiom Counts ===
stats.axiom_count = ontology.getAxiomCount(Imports.INCLUDED)
stats.logical_axiom_count = ontology.getLogicalAxiomCount(Imports.INCLUDED)
stats.declaration_axiom_count = ontology.getAxioms(AxiomType.DECLARATION, Imports.INCLUDED).size()

// === Entity Counts ===
stats.class_count = ontology.getClassesInSignature(Imports.INCLUDED).size()
stats.object_property_count = ontology.getObjectPropertiesInSignature(Imports.INCLUDED).size()
stats.data_property_count = ontology.getDataPropertiesInSignature(Imports.INCLUDED).size()
stats.annotation_property_count = ontology.getAnnotationPropertiesInSignature(Imports.INCLUDED).size()
stats.individual_count = ontology.getIndividualsInSignature(Imports.INCLUDED).size()

// === DL Expressivity ===
try {
    def checker = new DLExpressivityChecker(Collections.singleton(ontology))
    stats.dl_expressivity = checker.getDescriptionLogicName()
} catch (Exception e) {
    System.err.println("Could not determine DL expressivity: " + e.getMessage())
    e.printStackTrace()
}


// === Hierarchy Metrics ===
try {
    OWLReasoner reasoner = manager.getOWLReasonerFactory().createReasoner(ontology)
    reasoner.precomputeInferences(InferenceType.CLASS_HIERARCHY)

    int maxDepth = 0
    int maxChildren = 0
    long totalChildren = 0
    int classCountWithChildren = 0
    Map<OWLClass, Integer> depths = new HashMap<>()
    Queue<OWLClass> queue = new LinkedList<>()

    def topClassNode = reasoner.getTopClassNode().getEntities().find { it.isOWLThing() }
    if (topClassNode) {
        reasoner.getSubClasses(topClassNode, true).getFlattened().each { child ->
            if (!child.isOWLNothing()) {
                depths.put(child, 1)
                queue.add(child)
            }
        }
    }

    Set<OWLClass> visitedInBfs = new HashSet<>()
    while (!queue.isEmpty()) {
        OWLClass currentClass = queue.poll()
        if (visitedInBfs.contains(currentClass)) continue
        visitedInBfs.add(currentClass)

        Integer currentDepth = depths.get(currentClass)
        if (currentDepth != null) {
            maxDepth = Math.max(maxDepth, currentDepth)

            Set<OWLClass> children = reasoner.getSubClasses(currentClass, true).getFlattened()
            int childrenCount = children.size()
            if (childrenCount > 0) {
                maxChildren = Math.max(maxChildren, childrenCount)
                totalChildren += childrenCount
                classCountWithChildren++
            }

            children.each { child ->
                if (!child.isOWLNothing() && !depths.containsKey(child)) {
                    depths.put(child, currentDepth + 1)
                    queue.add(child)
                }
            }
        }
    }

    stats.max_depth = maxDepth
    stats.max_children = maxChildren
    stats.avg_children = classCountWithChildren > 0 ? (double) totalChildren / classCountWithChildren : 0.0

    reasoner.dispose()
} catch (Exception e) {
    System.err.println("Could not compute hierarchy stats: " + e.getMessage())
    e.printStackTrace()
}

// === Output JSON ===
response.setContentType("application/json")
out << JsonOutput.toJson(stats)
