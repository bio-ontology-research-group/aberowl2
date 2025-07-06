import groovy.json.JsonOutput
import src.RequestManager
import org.semanticweb.owlapi.model.OWLOntology

response.setContentType("application/json")

def manager = servletContext.getAttribute("manager")

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

def classCount = ontology.getClassesInSignature().size()

def result = [
    "class_count": classCount
]

out << JsonOutput.toJson(result)
