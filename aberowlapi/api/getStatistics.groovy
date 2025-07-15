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
def licenseIRI = IRI.create("http://purl.org/dc/terms/license")
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

def result = [
    "title": title,
    "description": description,
    "version_info": versionInfo,
    "version_iri": versionIRI,
    "license": license,
    "default_namespace": defaultNamespace,
    "obo_format_version": oboFormatVersion,
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
