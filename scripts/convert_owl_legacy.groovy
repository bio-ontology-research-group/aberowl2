// Convert an OWL file that only the legacy OWLAPI (4.2.3, as run by the old
// aberowl) can parse into RDF/XML, which the current worker's OWLAPI 4.5.29
// reads fine. Use for ontologies that fail to load on 4.5.x due to its stricter
// Manchester-syntax grammar (e.g. gene-cds: annotation sections placed after
// axiom sections, which 4.2.3 tolerated and 4.5.29 rejects).
//
//   groovy convert_owl_legacy.groovy <input.owl> <output.owl>
//
// Missing imports are ignored (SILENT) so a dead owl:imports URL can't stall
// the conversion.
@Grapes([
  @Grab('net.sourceforge.owlapi:owlapi-apibinding:4.2.3'),
  @Grab('net.sourceforge.owlapi:owlapi-impl:4.2.3'),
  @Grab('net.sourceforge.owlapi:owlapi-parsers:4.2.3')
])
import org.semanticweb.owlapi.apibinding.OWLManager
import org.semanticweb.owlapi.model.*
import org.semanticweb.owlapi.io.FileDocumentSource
import org.semanticweb.owlapi.formats.RDFXMLDocumentFormat

if (args.length < 2) {
    System.err.println "usage: convert_owl_legacy.groovy <input.owl> <output.owl>"
    System.exit(2)
}
def mgr = OWLManager.createOWLOntologyManager()
def config = new OWLOntologyLoaderConfiguration()
        .setMissingImportHandlingStrategy(MissingImportHandlingStrategy.SILENT)
def ont = mgr.loadOntologyFromOntologyDocument(new FileDocumentSource(new File(args[0])), config)
println "loaded with OWLAPI 4.2.3: ${ont.getAxiomCount()} axioms, ${ont.getClassesInSignature().size()} classes"
mgr.saveOntology(ont, new RDFXMLDocumentFormat(), IRI.create(new File(args[1])))
println "saved RDF/XML -> ${args[1]}"
