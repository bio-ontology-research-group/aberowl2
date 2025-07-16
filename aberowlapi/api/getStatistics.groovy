import groovy.json.JsonOutput
import src.RequestManager
import groovy.json.JsonOutput
import src.RequestManager
import org.semanticweb.owlapi.model.OWLOntology
import org.semanticweb.owlapi.model.AxiomType
import org.semanticweb.owlapi.model.parameters.Imports
import org.semanticweb.owlapi.util.DLExpressivityChecker
import org.semanticweb.owlapi.metrics.*
import java.util.Collections
import org.semanticweb.owlapi.vocab.DublinCoreVocabulary
import org.semanticweb.owlapi.vocab.OWLRDFVocabulary
import org.semanticweb.owlapi.model.OWLAnnotation
import org.semanticweb.owlapi.model.OWLLiteral
import org.semanticweb.owlapi.model.IRI
import org.semanticweb.owlapi.model.OWLClass
import org.semanticweb.owlapi.model.OWLAnnotationAssertionAxiom
import org.semanticweb.owlapi.model.OWLObjectIntersectionOf
import org.semanticweb.owlapi.model.OWLEquivalentClassesAxiom
import org.semanticweb.owlapi.model.OWLClassExpression
import uk.ac.manchester.cs.owl.owlapi.mansyntaxrenderer.ManchesterOWLSyntaxObjectRenderer
import src.NewShortFormProvider
import java.io.StringWriter

response.setContentType("application/json")

def manager = application.getAttribute("manager")

if (manager == null) {
    response.setStatus(503) // Service Unavailable
    out << JsonOutput.toJson([status: "error", message: "RequestManager not available. Ontology may still be loading."])
    return
}

OWLOntology ontology = manager.getOntology()

if (ontology == null) {
    response.setStatus(503) // Service Unavailable
    out << JsonOutput.toJson([status: "error", message: "OWLOntology object not available."])
    return
}

def annotations = ontology.getAnnotations()
def title = ""
def description = ""
def versionInfo = ""
def versionIRI = ""
def license = ""
def defaultNamespace = ""
def oboFormatVersion = ""
def homePage = ""
def documentation = ""
def publication = ""
def creators = []
def licenseIRI = IRI.create("http://purl.org/dc/terms/license")
def homePageIRI = IRI.create("http://xmlns.com/foaf/0.1/homepage")
def publicationIRI = IRI.create("http://purl.org/dc/terms/bibliographicCitation")
def creatorIRI = IRI.create("http://purl.org/dc/terms/creator")
def defaultNamespaceIRI = IRI.create("http://www.geneontology.org/formats/oboInOwl#default-namespace")
def oboFormatVersionIRI = IRI.create("http://www.geneontology.org/formats/oboInOwl#hasOBOFormatVersion")

for (OWLAnnotation annotation : annotations) {
    def propertyIRI = annotation.getProperty().getIRI()
    def value = annotation.getValue()

    if (propertyIRI.equals(DublinCoreVocabulary.TITLE.getIRI())) {
        if (value instanceof OWLLiteral) {
            title = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(DublinCoreVocabulary.DESCRIPTION.getIRI())) {
        if (value instanceof OWLLiteral) {
            description = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(OWLRDFVocabulary.OWL_VERSION_INFO.getIRI())) {
        if (value instanceof OWLLiteral) {
            versionInfo = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(OWLRDFVocabulary.OWL_VERSION_IRI.getIRI())) {
        if (value instanceof IRI) {
            versionIRI = value.toString()
        }
    } else if (propertyIRI.equals(licenseIRI)) {
        if (value instanceof IRI) {
            license = value.toString()
        } else if (value instanceof OWLLiteral) {
            license = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(defaultNamespaceIRI)) {
        if (value instanceof OWLLiteral) {
            defaultNamespace = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(oboFormatVersionIRI)) {
        if (value instanceof OWLLiteral) {
            oboFormatVersion = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(homePageIRI)) {
        if (value instanceof IRI) {
            homePage = value.toString()
        }
    } else if (propertyIRI.equals(OWLRDFVocabulary.RDFS_SEE_ALSO.getIRI())) {
        if (value instanceof IRI) {
            documentation = value.toString()
        } else if (value instanceof OWLLiteral) {
            documentation = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(publicationIRI)) {
        if (value instanceof OWLLiteral) {
            publication = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(creatorIRI)) {
        if (value instanceof OWLLiteral) {
            creators.add(((OWLLiteral) value).getLiteral())
        }
    }
}

def classCount = ontology.getClassesInSignature(true).size()
def objectPropertyCount = ontology.getObjectPropertiesInSignature(true).size()
def dataPropertyCount = ontology.getDataPropertiesInSignature(true).size()
def annotationPropertyCount = ontology.getAnnotationPropertiesInSignature(true).size()
def individualCount = ontology.getIndividualsInSignature(true).size()
def propertyCount = objectPropertyCount + dataPropertyCount + annotationPropertyCount

def axiomCount = ontology.getAxiomCount(true)
def logicalAxiomCount = ontology.getLogicalAxiomCount(true)

def tboxAxiomsCount = ontology.getTBoxAxioms(Imports.INCLUDED).size()
def aboxAxiomsCount = ontology.getABoxAxioms(Imports.INCLUDED).size()
def rboxAxiomsCount = ontology.getRBoxAxioms(Imports.INCLUDED).size()

def declarationAxiomsCount = ontology.getAxioms(AxiomType.DECLARATION, true).size()

//def checker = new DLExpressivityChecker(Collections.singleton(ontology))
def checker = new DLExpressivity(ontology)
def dlExpressivity = checker.getValue()

def exampleSuperclassLabel = ""
def exampleSubclassExpression = ""
def exampleSubclassExpressionText = ""

// Find a class with a label for the superclass example
outerloop1:
for (OWLClass cls : ontology.getClassesInSignature(true)) {
    if (cls.isBuiltIn()) continue
    for (OWLAnnotationAssertionAxiom annAxiom : ontology.getAnnotationAssertionAxioms(cls.getIRI())) {
        if (annAxiom.getProperty().isLabel()) {
            if (annAxiom.getValue() instanceof OWLLiteral) {
                def label = ((OWLLiteral) annAxiom.getValue()).getLiteral()
                if (label) {
                    exampleSuperclassLabel = label
                    break outerloop1
                }
            }
        }
    }
}

// Find a class with an intersection for the subclass example
outerloop2:
for (OWLClass cls : ontology.getClassesInSignature(true)) {
    if (cls.isBuiltIn()) continue
    for (OWLEquivalentClassesAxiom axiom : ontology.getEquivalentClassesAxioms(cls)) {
        for (OWLClassExpression ce : axiom.getClassExpressions()) {
            if (ce instanceof OWLObjectIntersectionOf) {
                def writer = new StringWriter()
                def renderer = new ManchesterOWLSyntaxObjectRenderer(writer, new NewShortFormProvider(Collections.singleton(ontology)))
                renderer.setUseWrapping(false)
                ce.accept(renderer)
                
                exampleSubclassExpressionText = writer.toString()
                exampleSubclassExpression = writer.toString() // No HTML version for now
                
                break outerloop2
            }
        }
    }
}

def result = [
    "title": title,
    "description": description,
    "version_info": versionInfo,
    "version_iri": versionIRI,
    "license": license,
    "default_namespace": defaultNamespace,
    "obo_format_version": oboFormatVersion,
    "home_page": homePage,
    "documentation": documentation,
    "publication": publication,
    "creators": creators,
    "exampleSuperclassLabel": exampleSuperclassLabel,
    "exampleSubclassExpression": exampleSubclassExpression,
    "exampleSubclassExpressionText": exampleSubclassExpressionText,
    "dl_expressivity": dlExpressivity,
    "class_count": classCount,
    "property_count": propertyCount,
    "object_property_count": objectPropertyCount,
    "data_property_count": dataPropertyCount,
    "annotation_property_count": annotationPropertyCount,
    "individual_count": individualCount,
    "axiom_count": axiomCount,
    "logical_axiom_count": logicalAxiomCount,
    "tbox_axiom_count": tboxAxiomsCount,
    "abox_axiom_count": aboxAxiomsCount,
    "rbox_axiom_count": rboxAxiomsCount,
    "declaration_axiom_count": declarationAxiomsCount
]

out << JsonOutput.toJson(result)
