import groovy.json.JsonBuilder
import org.json.simple.JSONValue;
import java.net.URLDecoder;
import src.util.Util
import org.semanticweb.owlapi.model.OWLClass
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

def params = Util.extractParams(request);

if (params.stats) {
    response.contentType = 'application/json';
    def ontology = application.getOntology()
    def ontManager = application.getOntManager()

    if (!ontology || !ontManager) {
        print(new JsonBuilder([status: "error", message: "Ontology not loaded!"]))
        return
    }

    def stats = [:]
    
    // Entity Counts
    stats.class_count = ontology.getClassesInSignature(Imports.INCLUDED).size()
    stats.object_property_count = ontology.getObjectPropertiesInSignature(Imports.INCLUDED).size()
    stats.data_property_count = ontology.getDataPropertiesInSignature(Imports.INCLUDED).size()
    stats.annotation_property_count = ontology.getAnnotationPropertiesInSignature(Imports.INCLUDED).size()
    stats.individual_count = ontology.getIndividualsInSignature(Imports.INCLUDED).size()

    // DL Expressivity
    try {
        def checker = new DLExpressivityChecker(Collections.singleton(ontology))
        stats.dl_expressivity = checker.getDescriptionLogicName()
    } catch (Exception e) {
        System.err.println("Could not determine DL expressivity: " + e.getMessage())
    }

    // Axiom Counts
    stats.axiom_count = ontology.getAxiomCount(Imports.INCLUDED)
    stats.logical_axiom_count = ontology.getLogicalAxiomCount(Imports.INCLUDED)
    stats.tbox_axiom_count = ontology.getTBoxAxioms(Imports.INCLUDED).size()
    stats.rbox_axiom_count = ontology.getRBoxAxioms(Imports.INCLUDED).size()
    stats.abox_axiom_count = ontology.getABoxAxioms(Imports.INCLUDED).size()
    
    // Hierarchy Metrics
    try {
        OWLReasoner reasoner = ontManager.getOWLReasonerFactory().createReasoner(ontology)
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
    }

    print(new JsonBuilder(stats))
    return
}

def property = params.property;
def manager = application.manager;

response.contentType = 'application/json';

if(manager) {
    if (property == null) {
        def objectProperties = manager.getObjectProperties()
        print(new JsonBuilder(objectProperties))
    } else {
        property = URLDecoder.decode(property, "UTF-8")
        def objectProperties = manager.getObjectProperties(property)
        print(new JsonBuilder(objectProperties))
    }
} else {
    print('{status: "error", message: "Please provide an ontology!"}')
}
