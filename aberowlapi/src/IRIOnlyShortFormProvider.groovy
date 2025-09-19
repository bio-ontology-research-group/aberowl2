package src;

import java.util.HashSet;
import java.util.Set;
import org.semanticweb.owlapi.apibinding.OWLManager;
import org.semanticweb.owlapi.model.IRI;
import org.semanticweb.owlapi.model.OWLEntity;
import org.semanticweb.owlapi.model.OWLOntology;
import org.semanticweb.owlapi.model.OWLOntologySetProvider;
import org.semanticweb.owlapi.util.BidirectionalShortFormProvider;

/**
 * A short form provider that uses the full IRI of an entity as its short form.
 */
public class IRIOnlyShortFormProvider implements BidirectionalShortFormProvider {
    private final OWLOntologySetProvider ontologySetProvider;

    public IRIOnlyShortFormProvider(Set<OWLOntology> ontologies) {
        this.ontologySetProvider = new SimpleOntologySetProviderImpl(ontologies);
    }

    @Override
    public String getShortForm(OWLEntity entity) {
        return entity.getIRI().toString();
    }

    @Override
    public Set<OWLEntity> getEntities(String shortForm) {
        Set<OWLEntity> result = new HashSet<>();
        OWLEntity entity = getEntity(shortForm);
        if (entity != null) {
            result.add(entity);
        }
        return result;
    }

    public OWLEntity getEntity(String shortForm) {
        try {
            IRI iri = IRI.create(shortForm);
            for (OWLOntology ontology : ontologySetProvider.getOntologies()) {
                if (ontology.containsClassInSignature(iri)) {
                    return OWLManager.getOWLDataFactory().getOWLClass(iri);
                }
                if (ontology.containsObjectPropertyInSignature(iri)) {
                    return OWLManager.getOWLDataFactory().getOWLObjectProperty(iri);
                }
                if (ontology.containsDataPropertyInSignature(iri)) {
                    return OWLManager.getOWLDataFactory().getOWLDataProperty(iri);
                }
                if (ontology.containsAnnotationPropertyInSignature(iri)) {
                    return OWLManager.getOWLDataFactory().getOWLAnnotationProperty(iri);
                }
                if (ontology.containsNamedIndividualInSignature(iri)) {
                    return OWLManager.getOWLDataFactory().getOWLNamedIndividual(iri);
                }
            }
        } catch (Exception e) {
            // Invalid IRI format
            return null;
        }
        return null;
    }

    @Override
    public Set<String> getShortForms() {
        Set<String> shortForms = new HashSet<>();
        for (OWLOntology o : ontologySetProvider.getOntologies()) {
            for (OWLEntity e : o.getSignature()) {
                shortForms.add(getShortForm(e));
            }
        }
        return shortForms;
    }

    @Override
    public void dispose() {
        // Nothing to dispose
    }

    private static class SimpleOntologySetProviderImpl implements OWLOntologySetProvider {
        private final Set<OWLOntology> ontologies;

        public SimpleOntologySetProviderImpl(Set<OWLOntology> ontologies) {
            this.ontologies = ontologies;
        }

        @Override
        public Set<OWLOntology> getOntologies() {
            return ontologies;
        }
    }
}
