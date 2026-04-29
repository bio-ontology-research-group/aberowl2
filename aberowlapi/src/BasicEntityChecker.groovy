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
        // Remove angle brackets if present
        name = name.replaceAll("<","").replaceAll(">","")
        
        // Remove quotes if present
        if ((name.startsWith("'") && name.endsWith("'")) ||
            (name.startsWith("\"") && name.endsWith("\""))) {
            name = name.substring(1, name.length() - 1)
        }
        
        def iri = new IRI(name)
        def result = null
        if(ontology.containsClassInSignature(iri, true) || iri == dFactory.getOWLThing().getIRI() || iri == dFactory.getOWLNothing().getIRI()) {
          result = dFactory.getOWLClass(iri)
        }
        
        // If we couldn't find the class by direct IRI lookup, try to find it by label
        if (result == null) {
            // Try to find a class with a label that matches when underscores are replaced with spaces
            for (OWLClass cls : ontology.getClassesInSignature(true)) {
                // First try to match by fragment/short name
                String fragment = cls.getIRI().getFragment()
                if (fragment != null) {
                    // Try direct match
                    if (fragment.equalsIgnoreCase(name)) {
                        return cls
                    }
                    
                    // Try replacing underscores with spaces in fragment
                    String fragmentWithSpaces = fragment.replace("_", " ")
                    if (fragmentWithSpaces.equalsIgnoreCase(name)) {
                        return cls
                    }
                    
                    // Try replacing spaces with underscores in name
                    String nameWithUnderscores = name.replace(" ", "_")
                    if (fragment.equalsIgnoreCase(nameWithUnderscores)) {
                        return cls
                    }
                    
                    // Try without any spaces or underscores
                    String fragmentNoSpacesOrUnderscores = fragment.replace("_", "").replace(" ", "")
                    String nameNoSpacesOrUnderscores = name.replace("_", "").replace(" ", "")
                    if (fragmentNoSpacesOrUnderscores.equalsIgnoreCase(nameNoSpacesOrUnderscores)) {
                        return cls
                    }
                }
                
                // Get the class's annotations
                for (OWLAnnotation annotation : EntitySearcher.getAnnotations(cls, ontology)) {
                    if (annotation.getProperty().isLabel()) {
                        if (annotation.getValue() instanceof OWLLiteral) {
                            OWLLiteral val = (OWLLiteral) annotation.getValue()
                            String label = val.getLiteral().toLowerCase()
                            
                            // Direct comparison
                            if (name.toLowerCase().equalsIgnoreCase(label)) {
                                return cls
                            }
                            
                            // Handle case where the input uses underscores
                            if (name.contains("_")) {
                                // Replace underscores with spaces for comparison
                                String nameWithSpaces = name.replace("_", " ")
                                if (nameWithSpaces.equalsIgnoreCase(label)) {
                                    return cls
                                }
                                
                                // Remove underscores from input and spaces from label for comparison
                                String nameWithoutUnderscores = name.replace("_", "")
                                String labelWithoutSpaces = label.replace(" ", "")
                                
                                if (nameWithoutUnderscores.equalsIgnoreCase(labelWithoutSpaces)) {
                                    return cls
                                }
                            }
                            // Handle case where input already contains spaces
                            else if (name.contains(" ")) {
                                // Replace spaces with underscores for comparison
                                String nameWithUnderscores = name.replace(" ", "_")
                                String labelWithUnderscores = label.replace(" ", "_")
                                
                                if (nameWithUnderscores.equalsIgnoreCase(labelWithUnderscores)) {
                                    return cls
                                }
                                
                                // Also try comparing without spaces
                                String nameWithoutSpaces = name.replace(" ", "")
                                String labelWithoutSpaces = label.replace(" ", "")
                                
                                if (nameWithoutSpaces.equalsIgnoreCase(labelWithoutSpaces)) {
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
        // Remove angle brackets if present
        name = name.replaceAll("<","").replaceAll(">","")
        
        // Remove quotes if present
        if ((name.startsWith("'") && name.endsWith("'")) ||
            (name.startsWith("\"") && name.endsWith("\""))) {
            name = name.substring(1, name.length() - 1)
        }
        
        def iri = new IRI(name)
        def result = null
        if(ontology.containsDataPropertyInSignature(iri, true) || iri == dFactory.getOWLTopDataProperty().getIRI() || iri == dFactory.getOWLBottomDataProperty().getIRI()) {
          result = dFactory.getOWLDataProperty(iri)
        }
        return result
    }


    @Override
    public OWLDatatype getOWLDatatype(String name) {
        // Remove angle brackets if present
        name = name.replaceAll("<","").replaceAll(">","")
        
        // Remove quotes if present
        if ((name.startsWith("'") && name.endsWith("'")) ||
            (name.startsWith("\"") && name.endsWith("\""))) {
            name = name.substring(1, name.length() - 1)
        }
        
        def iri = new IRI(name)
        def result = null
        if(ontology.containsDataTypeInSignature(iri)) {
          result = dFactory.getOWLDataType(iri)
        }
        return result
    }


    @Override
    public OWLNamedIndividual getOWLIndividual(String name) {
        // Remove angle brackets if present
        name = name.replaceAll("<","").replaceAll(">","")
        
        // Remove quotes if present
        if ((name.startsWith("'") && name.endsWith("'")) ||
            (name.startsWith("\"") && name.endsWith("\""))) {
            name = name.substring(1, name.length() - 1)
        }
        
        def iri = new IRI(name)
        def result = null
        if(ontology.containsIndividualInSignature(iri, true)) {
          result = dFactory.getOWLNamedIndividual(iri)
        }
        return result
    }


    @Override
    public OWLObjectProperty getOWLObjectProperty(String name) {
        // Remove angle brackets if present
        name = name.replaceAll("<","").replaceAll(">","")
        
        // Remove quotes if present
        if ((name.startsWith("'") && name.endsWith("'")) ||
            (name.startsWith("\"") && name.endsWith("\""))) {
            name = name.substring(1, name.length() - 1)
        }
        
        def iri = new IRI(name)
        def result = null
        if(ontology.containsObjectPropertyInSignature(iri, true) || iri == dFactory.getOWLTopObjectProperty().getIRI() || iri == dFactory.getOWLBottomDataProperty().getIRI()) {
          result = dFactory.getOWLObjectProperty(iri)
        }
        return result
    }

    @Override
    public OWLAnnotationProperty getOWLAnnotationProperty(String name) {
        // Remove angle brackets if present
        name = name.replaceAll("<","").replaceAll(">","")
        
        // Remove quotes if present
        if ((name.startsWith("'") && name.endsWith("'")) ||
            (name.startsWith("\"") && name.endsWith("\""))) {
            name = name.substring(1, name.length() - 1)
        }
        
        def iri = new IRI(name)
        def result = null
        if(ontology.containsAnnotationPropertyInSignature(iri, true)) {
          result = dFactory.getOWLAnnotationProperty(iri)
        }
        return result
    }
}
