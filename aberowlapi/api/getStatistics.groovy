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
import src.util.Util
import java.io.StringWriter

if(!application) {
    application = request.getApplication(true)
}

def params = Util.extractParams(request)
def ontologyId = params.ontologyId ?: params.ontology

response.setContentType("application/json")

def manager = application.getAttribute("manager")

if (manager == null) {
    response.setStatus(503)
    out << JsonOutput.toJson([status: "error", message: "RequestManager not available. Ontology may still be loading."])
    return
}

// Resolve ontologyId
if (!ontologyId && manager.ontologies.size() == 1) {
    ontologyId = manager.getDefaultOntologyId()
} else if (!ontologyId) {
    // Return stats for all loaded ontologies
    def allStats = manager.listOntologies().collect { info ->
        [ontologyId: info.ontologyId, status: info.status, classCount: info.classCount, reasonerType: info.reasonerType]
    }
    out << JsonOutput.toJson([ontologies: allStats])
    return
}

if (!manager.hasOntology(ontologyId)) {
    response.setStatus(404)
    out << JsonOutput.toJson([status: "error", message: "Ontology not found: ${ontologyId}"])
    return
}

OWLOntology ontology = manager.getOntology(ontologyId)

if (ontology == null) {
    response.setStatus(503)
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

// Additional annotation IRIs to check
def dctermsTitleIRI = IRI.create("http://purl.org/dc/terms/title")
def dctermsDescIRI = IRI.create("http://purl.org/dc/terms/description")
def rdfsLabelIRI = OWLRDFVocabulary.RDFS_LABEL.getIRI()
def rdfsCommentIRI = OWLRDFVocabulary.RDFS_COMMENT.getIRI()

for (OWLAnnotation annotation : annotations) {
    def propertyIRI = annotation.getProperty().getIRI()
    def value = annotation.getValue()

    if (propertyIRI.equals(DublinCoreVocabulary.TITLE.getIRI()) || propertyIRI.equals(dctermsTitleIRI)) {
        if (value instanceof OWLLiteral) {
            title = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(rdfsLabelIRI) && !title) {
        // Use rdfs:label as fallback for title if dc:title/dcterms:title not found
        if (value instanceof OWLLiteral) {
            title = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(DublinCoreVocabulary.DESCRIPTION.getIRI()) || propertyIRI.equals(dctermsDescIRI)) {
        if (value instanceof OWLLiteral) {
            description = ((OWLLiteral) value).getLiteral()
        }
    } else if (propertyIRI.equals(rdfsCommentIRI) && !description) {
        // Use rdfs:comment as fallback description
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

def checker = new DLExpressivity(ontology)
def dlExpressivity = checker.getValue()

def exampleSuperclassLabel = manager.exampleSuperclassLabels.get(ontologyId) ?: ""
def exampleSubclassExpression = manager.exampleSubclassExpressions.get(ontologyId) ?: ""
def exampleSubclassExpressionText = manager.exampleSubclassExpressionTexts.get(ontologyId) ?: ""

def result = [
    "ontology_id": ontologyId,
    "reasoner_type": manager.reasonerTypes.get(ontologyId) ?: "unknown",
    "status": manager.getStatus(ontologyId) ?: "unknown",
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
