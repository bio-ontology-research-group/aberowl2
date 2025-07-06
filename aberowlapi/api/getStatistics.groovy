import groovy.json.JsonOutput
import src.RequestManager
import groovy.json.JsonOutput
import src.RequestManager
import org.semanticweb.owlapi.model.OWLOntology
import org.semanticweb.owlapi.model.AxiomType
import org.semanticweb.owlapi.model.parameters.Imports
import org.semanticweb.owlapi.model.OWLAnnotation
import org.semanticweb.owlapi.model.OWLLiteral
import org.semanticweb.owlapi.vocab.OWLRDFVocabulary
import org.semanticweb.owlapi.util.DLExpressivityChecker
import java.util.Collections

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

def checker = new DLExpressivityChecker(Collections.singleton(ontology))
def dlExpressivity = checker.getDescriptionLogicName()

def version = ""
def releaseDate = ""

ontology.getAnnotations().each { OWLAnnotation annotation ->
    if (annotation.getProperty().isBuiltIn() && annotation.getProperty().getIRI().equals(OWLRDFVocabulary.OWL_VERSION_INFO.getIRI())) {
        if (annotation.getValue() instanceof OWLLiteral) {
            version = annotation.getValue().getLiteral()
        }
    }
    def propertyIRI = annotation.getProperty().getIRI().toString()
    if (propertyIRI == "http://purl.org/dc/elements/1.1/date" || propertyIRI == "http://purl.org/dc/terms/date") {
        if (annotation.getValue() instanceof OWLLiteral) {
            releaseDate = annotation.getValue().getLiteral()
        }
    }
}

def result = [
    "dl_expressivity": dlExpressivity,
    "version": version,
    "release_date": releaseDate,
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
