package src

import org.semanticweb.owlapi.model.*
import org.semanticweb.owlapi.expression.OWLEntityChecker
import org.semanticweb.owlapi.search.EntitySearcher

public class BasicEntityChecker implements OWLEntityChecker {
   private final OWLDataFactory dFactory;
   private final OWLOntology ontology;


    public BasicEntityChecker(OWLDataFactory dFactory, OWLOntology ontology) {
        this.dFactory = dFactory;
        this.ontology = ontology;
    }

    @Override
    public OWLClass getOWLClass(String name) {
        name = name.replaceAll("<","").replaceAll(">","")
        def iri = new IRI(name)
        def result = null
        if(ontology.containsClassInSignature(iri, true) || iri == dFactory.getOWLThing().getIRI() || iri == dFactory.getOWLNothing().getIRI()) {
          result = dFactory.getOWLClass(iri)
        }
        
        // If we couldn't find the class by direct IRI lookup, try to find it by label
        if (result == null) {
            // Try to find a class with a label that matches when underscores are replaced with spaces
            for (OWLClass cls : ontology.getClassesInSignature(true)) {
                // Get the class's annotations
                for (OWLAnnotation annotation : EntitySearcher.getAnnotations(cls, ontology)) {
                    if (annotation.getProperty().isLabel()) {
                        if (annotation.getValue() instanceof OWLLiteral) {
                            OWLLiteral val = (OWLLiteral) annotation.getValue()
                            String label = val.getLiteral().toLowerCase()
                            
                            // Handle only the case where the input uses underscores
                            if (name.contains("_")) {
                                // Remove underscores from input and spaces from label for comparison
                                String nameWithoutUnderscores = name.replace("_", "")
                                String labelWithoutSpaces = label.replace(" ", "")
                                
                                if (nameWithoutUnderscores.equalsIgnoreCase(labelWithoutSpaces)) {
                                    return cls
                                }
                            }
                        }
                    }
                }
            }
        }
        
        return result
    }


    @Override
    public OWLDataProperty getOWLDataProperty(String name) {
        name = name.replaceAll("<","").replaceAll(">","") 
        def iri = new IRI(name)
        def result = null
        if(ontology.containsDataPropertyInSignature(iri, true) || iri == dFactory.getOWLTopDataProperty().getIRI() || iri == dFactory.getOWLBottomDataProperty().getIRI()) {
          result = dFactory.getOWLDataProperty(iri)
        }
        return result
    }


    @Override
    public OWLDatatype getOWLDatatype(String name) {
        name = name.replaceAll("<","").replaceAll(">","") 
        def iri = new IRI(name)
        def result = null
        if(ontology.containsDataTypeInSignature(iri)) {
          result = dFactory.getOWLDataType(iri)
        }
        return result
    }


    @Override
    public OWLNamedIndividual getOWLIndividual(String name) {
        name = name.replaceAll("<","").replaceAll(">","") 
        def iri = new IRI(name)
        def result = null
        if(ontology.containsIndividualInSignature(iri, true)) {
          result = dFactory.getOWLNamedIndividual(iri)
        }
        return result
    }


    @Override
    public OWLObjectProperty getOWLObjectProperty(String name) {
        name = name.replaceAll("<","").replaceAll(">","") 
        def iri = new IRI(name)
        def result = null
        if(ontology.containsObjectPropertyInSignature(iri, true) || iri == dFactory.getOWLTopObjectProperty().getIRI() || iri == dFactory.getOWLBottomDataProperty().getIRI()) {
          result = dFactory.getOWLObjectProperty(iri)
        }
        return result
    }

    @Override
    public OWLAnnotationProperty getOWLAnnotationProperty(String name) {
        name = name.replaceAll("<","").replaceAll(">","") 
        def iri = new IRI(name)
        def result = null
        if(ontology.containsAnnotationPropertyInSignature(iri, true)) {
          result = dFactory.getOWLAnnotationProperty(iri)
        }
        return result
    }
}
