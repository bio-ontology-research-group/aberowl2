// Standalone OWL inspector. Reports class / property / individual / axiom
// counts for an OWL file, using OWLAPI 4.5.29 (same as the workers).
//
// Run from inside a worker container (which already has OWLAPI grapes
// available), e.g.:
//   docker exec aberowl-worker-1 groovy /scripts/inspect_ontology.groovy /data/ddss/ddss.owl
//
// Output is a single JSON line on stdout so it's easy to parse.

@Grab('net.sourceforge.owlapi:owlapi-distribution:4.5.29')
@Grab('org.semanticweb.elk:elk-owlapi:0.4.3')
import org.semanticweb.owlapi.apibinding.OWLManager
import org.semanticweb.owlapi.model.*
import org.semanticweb.owlapi.util.DLExpressivityChecker

if (args.length < 1) {
    System.err.println("usage: inspect_ontology.groovy <path-to.owl>")
    System.exit(2)
}
def path = args[0]
def file = new File(path)
if (!file.exists()) {
    System.err.println("file not found: ${path}")
    System.exit(1)
}

def manager = OWLManager.createOWLOntologyManager()
def ontology = manager.loadOntologyFromOntologyDocument(file)

def classes        = ontology.getClassesInSignature(true).size()
def individuals    = ontology.getIndividualsInSignature(true).size()
def object_props   = ontology.getObjectPropertiesInSignature(true).size()
def data_props     = ontology.getDataPropertiesInSignature(true).size()
def annot_props    = ontology.getAnnotationPropertiesInSignature(true).size()
def axioms         = ontology.getAxiomCount()
def logical_axioms = ontology.getLogicalAxiomCount()
def tbox = ontology.getTBoxAxioms(org.semanticweb.owlapi.model.parameters.Imports.INCLUDED).size()
def abox = ontology.getABoxAxioms(org.semanticweb.owlapi.model.parameters.Imports.INCLUDED).size()
def rbox = ontology.getRBoxAxioms(org.semanticweb.owlapi.model.parameters.Imports.INCLUDED).size()

def dl = "?"
try {
    dl = new DLExpressivityChecker([ontology]).getDescriptionLogicName()
} catch (Exception e) { /* ignore */ }

def file_size = file.length()

println groovy.json.JsonOutput.toJson([
    path: path,
    file_size: file_size,
    classes: classes,
    individuals: individuals,
    object_props: object_props,
    data_props: data_props,
    annotation_props: annot_props,
    axioms: axioms,
    logical_axioms: logical_axioms,
    tbox: tbox,
    abox: abox,
    rbox: rbox,
    dl_expressivity: dl,
])
