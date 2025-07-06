import groovy.json.JsonOutput
import src.RequestManager
import org.semanticweb.owlapi.model.OWLOntology
import org.semanticweb.owlapi.model.AxiomType

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

def tboxAxiomsCount = ontology.getTBoxAxioms(true).size()
def aboxAxiomsCount = ontology.getABoxAxioms(true).size()
def rboxAxiomsCount = ontology.getRBoxAxioms(true).size()

def declarationAxiomsCount = ontology.getAxioms(AxiomType.DECLARATION, true).size()

def result = [
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
