package aberowlapi.src;

import org.semanticweb.owlapi.model.OWLOntology;
import org.semanticweb.owlapi.model.OWLOntologyManager;
import org.semanticweb.owlapi.model.AxiomType;
import org.semanticweb.owlapi.model.IRI;
import org.semanticweb.owlapi.model.OWLClass;
import org.semanticweb.owlapi.model.OWLLiteral;
import org.semanticweb.owlapi.model.parameters.Imports;
import org.semanticweb.owlapi.reasoner.OWLReasoner;
import org.semanticweb.owlapi.reasoner.InferenceType;
import org.semanticweb.owlapi.util.DLExpressivityChecker;
import org.semanticweb.owlapi.model.OWLAnnotationValueVisitorEx;

import java.util.Map;
import java.util.HashMap;
import java.util.List;
import java.util.ArrayList;
import java.util.LinkedList;
import java.util.HashSet;
import java.util.Queue;
import java.util.Set;
import java.util.Collections;

public class OntologyStatsCalculator {

    private final OWLOntology ontology;
    private final OWLOntologyManager manager;

    public OntologyStatsCalculator(OWLOntology ontology, OWLOntologyManager manager) {
        this.ontology = ontology;
        this.manager = manager;
    }

    public Map<String, Object> calculateStats() {
        Map<String, Object> stats = new HashMap<>();

        // === Metadata from Ontology Annotations ===
        Map<String, List<String>> metadata = new HashMap<>();
        ontology.getAnnotations().forEach(annotation -> {
            String propertyIRI = annotation.getProperty().getIRI().toString();
            String key = propertyIRI.substring(propertyIRI.lastIndexOf('/') + 1);
            if (key.contains("#")) {
                key = key.substring(key.lastIndexOf('#') + 1);
            }
            metadata.computeIfAbsent(key, k -> new ArrayList<>());

            annotation.getValue().accept(new OWLAnnotationValueVisitorEx<Void>() {
                @Override
                public Void visit(IRI iri) {
                    metadata.get(key).add(iri.toString());
                    return null;
                }
                @Override
                public Void visit(OWLLiteral literal) {
                    metadata.get(key).add(literal.getLiteral());
                    return null;
                }
            });
        });
        stats.put("metadata", metadata);

        // === Axiom Counts ===
        stats.put("axiom_count", ontology.getAxiomCount(Imports.INCLUDED));
        stats.put("logical_axiom_count", ontology.getLogicalAxiomCount(Imports.INCLUDED));
        stats.put("declaration_axiom_count", ontology.getAxioms(AxiomType.DECLARATION, Imports.INCLUDED).size());

        // === Entity Counts ===
        stats.put("class_count", ontology.getClassesInSignature(Imports.INCLUDED).size());
        stats.put("object_property_count", ontology.getObjectPropertiesInSignature(Imports.INCLUDED).size());
        stats.put("data_property_count", ontology.getDataPropertiesInSignature(Imports.INCLUDED).size());
        stats.put("annotation_property_count", ontology.getAnnotationPropertiesInSignature(Imports.INCLUDED).size());
        stats.put("individual_count", ontology.getIndividualsInSignature(Imports.INCLUDED).size());

        // === DL Expressivity ===
        DLExpressivityChecker checker = new DLExpressivityChecker(Collections.singleton(ontology));
        stats.put("dl_expressivity", checker.getDescriptionLogicName());

        // === Hierarchy Metrics ===
        try {
            OWLReasoner reasoner = manager.getOWLReasonerFactory().createReasoner(ontology);
            reasoner.precomputeInferences(InferenceType.CLASS_HIERARCHY);

            int maxDepth = 0;
            int maxChildren = 0;
            long totalChildren = 0;
            int classCountWithChildren = 0;
            Map<OWLClass, Integer> depths = new HashMap<>();
            Queue<OWLClass> queue = new LinkedList<>();

            reasoner.getTopClassNode().getEntities().stream()
                .filter(c -> c.isOWLThing())
                .findFirst()
                .ifPresent(topClass -> {
                    reasoner.getSubClasses(topClass, true).getFlattened().forEach(child -> {
                        if (!child.isOWLNothing()) {
                            depths.put(child, 1);
                            queue.add(child);
                        }
                    });
                });

            Set<OWLClass> visitedInBfs = new HashSet<>();
            while (!queue.isEmpty()) {
                OWLClass currentClass = queue.poll();
                if (visitedInBfs.contains(currentClass)) continue;
                visitedInBfs.add(currentClass);

                Integer currentDepth = depths.get(currentClass);
                if (currentDepth != null) {
                    maxDepth = Math.max(maxDepth, currentDepth);

                    Set<OWLClass> children = reasoner.getSubClasses(currentClass, true).getFlattened();
                    int childrenCount = children.size();
                    if (childrenCount > 0) {
                        maxChildren = Math.max(maxChildren, childrenCount);
                        totalChildren += childrenCount;
                        classCountWithChildren++;
                    }

                    children.forEach(child -> {
                        if (!child.isOWLNothing() && !depths.containsKey(child)) {
                            depths.put(child, currentDepth + 1);
                            queue.add(child);
                        }
                    });
                }
            }

            stats.put("max_depth", maxDepth);
            stats.put("max_children", maxChildren);
            stats.put("avg_children", classCountWithChildren > 0 ? (double) totalChildren / classCountWithChildren : 0.0);

            reasoner.dispose();
        } catch (Exception e) {
            System.err.println("Could not compute hierarchy stats: " + e.getMessage());
            e.printStackTrace();
        }

        return stats;
    }
}
