import groovy.json.JsonOutput
import org.semanticweb.owlapi.model.AxiomType
import org.semanticweb.owlapi.model.IRI
import org.semanticweb.owlapi.model.OWLClass
import org.semanticweb.owlapi.model.OWLLiteral
import org.semanticweb.owlapi.model.parameters.Imports
import org.semanticweb.owlapi.reasoner.InferenceType
import org.semanticweb.owlapi.util.DLExpressivityChecker

import java.util.HashMap
import java.util.HashSet
import java.util.LinkedList

// This script assumes 'ontology' and 'manager' are available in the binding.

def stats = [:]

// === Metadata from Ontology Annotations ===
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
def checker = new DLExpressivityChecker([ontology])
stats.dl_expressivity = checker.getDescriptionLogicName()

// === Hierarchy Metrics ===
def reasoner = manager.getOWLReasonerFactory().createReasoner(ontology)
reasoner.precomputeInferences(InferenceType.CLASS_HIERARCHY)

int maxDepth = 0
int maxChildren = 0
long totalChildren = 0
int classCountWithChildren = 0
def depths = new HashMap<OWLClass, Integer>()

// BFS from top node to calculate depths
def queue = new LinkedList<OWLClass>()
reasoner.getTopClassNode().getEntities().each { topClass ->
    if (topClass.isOWLThing()) {
        reasoner.getSubClasses(topClass, true).getFlattened().each { child ->
            if (!child.isOWLNothing()) {
                depths.put(child, 1)
                queue.add(child)
            }
        }
    }
}

def visitedInBfs = new HashSet<OWLClass>()
while (!queue.isEmpty()) {
    def currentClass = queue.poll()
    if (visitedInBfs.contains(currentClass)) continue
    visitedInBfs.add(currentClass)

    def currentDepth = depths.get(currentClass)
    maxDepth = Math.max(maxDepth, currentDepth)

    def children = reasoner.getSubClasses(currentClass, true).getFlattened()
    def childrenCount = children.size()
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

stats.max_depth = maxDepth
stats.max_children = maxChildren
stats.avg_children = classCountWithChildren > 0 ? (totalChildren / (double)classCountWithChildren) : 0

// Clean up reasoner
reasoner.dispose()

// === Output JSON ===
response.setContentType("application/json")
out << JsonOutput.toJson(stats)
