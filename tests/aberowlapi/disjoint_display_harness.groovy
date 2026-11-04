/**
 * Real OWLAPI regression checks for class disjoint-expression rendering.
 * Uses only cached dependencies; run with the offline Grape config documented
 * in README.md. A nonzero exit records each failed behavior check.
 */
import groovy.grape.Grape
import groovy.json.JsonOutput

import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions

// ---- dependencies ----------------------------------------------------------
// The worker image resolves log4j's API from reload4j; a developer cache may
// only hold log4j itself. Try the worker's set first.
def common = [
    [group: 'net.sourceforge.owlapi', module: 'owlapi-distribution', version: '4.5.29'],
    [group: 'org.semanticweb.elk', module: 'elk-owlapi', version: '0.4.3'],
    [group: 'org.codehaus.gpars', module: 'gpars', version: '1.1.0'],
    [group: 'org.slf4j', module: 'slf4j-nop', version: '1.7.25'],
    // Match the worker's explicit pin rather than OWLAPI's newer transitive dependency.
    [group: 'com.google.guava', module: 'guava', version: '19.0', force: true],
]
def variants = [
    [deps: common + [[group: 'ch.qos.reload4j', module: 'reload4j', version: '1.2.18.5']],
     excludes: [[group: 'log4j', module: 'log4j'], [group: 'xml-apis', module: 'xml-apis']]],
    [deps: common, excludes: [[group: 'xml-apis', module: 'xml-apis']]],
]
def loader = this.class.classLoader
def grabErrors = []
def grabbed = variants.any { variant ->
    try {
        // Into the system class loader, as OntologyServer.groovy's @GrabConfig does.
        Grape.grab([systemClassLoader: true, excludes: variant.excludes], *variant.deps)
        return true
    } catch (Throwable t) {
        grabErrors << t.getMessage()
        return false
    }
}
if (!grabbed) {
    System.err.println("Cannot resolve OWLAPI/ELK/GPars from the Grapes cache:\n  " + grabErrors.join("\n  "))
    System.exit(2)
}


def rmClass = loader.loadClass('src.RequestManager')
def iriClass = loader.loadClass('org.semanticweb.owlapi.model.IRI')
def owlManagerClass = loader.loadClass('org.semanticweb.owlapi.apibinding.OWLManager')
def formatClass = loader.loadClass('org.semanticweb.owlapi.formats.FunctionalSyntaxDocumentFormat')
def manager = owlManagerClass.createOWLOntologyManager()
def factory = manager.getOWLDataFactory()
def iri = { String name -> iriClass.create('http://example.org/disjoint-display#' + name) }
def cls = { String name -> factory.getOWLClass(iri(name)) }
def classes = ['binary', 'binaryother', 'nary', 'second', 'third', 'anonymous',
               'target', 'excluded', 'same', 'sameother', 'plain', 'empty', 'parent', 'alias']
def ontology = manager.createOntology(iriClass.create('http://example.org/disjoint-display'))
classes.each { manager.addAxiom(ontology, factory.getOWLDeclarationAxiom(cls(it))) }
def disjoint = { List expressions -> manager.addAxiom(ontology,
    factory.getOWLDisjointClassesAxiom(new HashSet(expressions))) }
disjoint([cls('binary'), cls('binaryother')])
disjoint([cls('nary'), cls('second'), cls('third')])
def property = factory.getOWLObjectProperty(iri('part'))
disjoint([cls('anonymous'), factory.getOWLObjectSomeValuesFrom(property, cls('target'))])
disjoint([cls('anonymous'), factory.getOWLObjectComplementOf(cls('excluded'))])
disjoint([cls('same'), cls('sameother')])
['same', 'sameother'].each { name -> manager.addAxiom(ontology,
    factory.getOWLAnnotationAssertionAxiom(factory.getRDFSLabel(), iri(name), factory.getOWLLiteral('Shared'))) }
// A genuinely unsatisfiable class still shows its disjointness with owl:Thing.
disjoint([cls('empty'), factory.getOWLThing()])
manager.addAxiom(ontology, factory.getOWLSubClassOfAxiom(cls('binary'), cls('parent')))
manager.addAxiom(ontology, factory.getOWLEquivalentClassesAxiom(cls('binary'), cls('alias')))
def directory = java.nio.file.Files.createTempDirectory('aberowl-disjoint-display-').toRealPath()
System.setProperty('aberowl.dataRoot', directory.toString())
def file = directory.resolve('fixture.owl').toFile()
manager.saveOntology(ontology, formatClass.newInstance(), iriClass.create(file))
def worker = rmClass.newInstance()
def cases = [], failures = [], details = [:]
def check = { String name, Closure assertion ->
    try { assertion(); cases << [name:name, passed:true] }
    catch (Throwable error) { failures << name; cases << [name:name, passed:false, error:error.toString()] }
}
try {
    worker.loadOntology('fixture', file.toString(), 'elk')
    worker.createReasoner('fixture')
    def served = worker.ontologies.get('fixture')
    def axiomsBefore = new HashSet(served.getAxioms())
    def classCount = served.getClassesInSignature(true).size()
    def info = { String name -> worker.toInfo('fixture', cls(name), true, null) }
    ['binary','nary','anonymous','same','plain','empty'].each { details[it] = info(it) }
    check('binary disjointness retains only the other class') {
        assert details.binary.Disjoint == ['binaryother']
    }
    check('n-ary disjointness retains every other member') {
        assert details.nary.Disjoint.toSet() == ['second','third'].toSet()
        assert details.nary.Disjoint.size() == 2
    }
    check('anonymous restrictions and complements remain visible') {
        assert details.anonymous.Disjoint.toSet() == ['part some target','not ( excluded )'].toSet()
        assert details.anonymous.Disjoint.size() == 2
    }
    check('distinct classes with identical labels are not filtered by label') {
        assert details.same.Disjoint == ['Shared']
    }
    check('classes without disjoint axioms stay empty') {
        assert details.plain.Disjoint == []
    }
    check('disjointness with owl:Thing remains explicit') {
        assert details.empty.Disjoint == ['Thing']
    }
    check('parent and equivalent expressions remain available') {
        assert details.binary.SubClassOf == ['parent']
        assert details.binary.Equivalent.contains('alias')
    }
    check('annotations-only requests do not gain logical fields') {
        def annotations = worker.toInfo('fixture', cls('binary'), false, null)
        assert !annotations.containsKey('Disjoint') && !annotations.containsKey('SubClassOf')
    }
    check('rendering leaves ontology axioms and class counts unchanged') {
        assert axiomsBefore == new HashSet(served.getAxioms())
        assert classCount == served.getClassesInSignature(true).size()
    }
    def output = [success:failures.isEmpty(), checks:cases, failed:failures,
                  class_count:classCount, axiom_count:served.getAxiomCount(), details:details]
    if (args.length) new File(args[0]).text = JsonOutput.prettyPrint(JsonOutput.toJson(output))
    println 'DISJOINT_DISPLAY_RESULT=' + JsonOutput.toJson(output)
} finally { worker.disposeOntology('fixture') }
System.exit(failures.isEmpty() ? 0 : 1)
