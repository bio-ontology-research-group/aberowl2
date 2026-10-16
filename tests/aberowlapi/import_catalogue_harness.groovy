/**
 * Import catalogue harness for the worker (#126).
 *
 * Writes tiny synthetic ontologies into a temporary data root, loads them
 * through src.RequestManager, classifies them with ELK, and checks the import
 * policy end to end: the default suppression, catalogue resolution of a
 * transitive closure with cycles, strict failures, path containment, other
 * roots staying unaffected, no network or file: dereferencing, and the
 * reload/dispose lifecycle. It also checks the browsing roots an ontology
 * declares (IAO_0000700) and their owl:Thing fallback, with owl:Thing DL
 * queries still answering every top-level class.
 *
 * Run from the repository root. The Grape settings below resolve from the
 * local Grapes cache only, so the run never downloads anything:
 *
 *   JAVA_OPTS="-Dgrape.config=tests/aberowlapi/grapeConfig-offline.xml" \
 *     groovy -cp aberowlapi tests/aberowlapi/import_catalogue_harness.groovy
 *
 * Exit status is 0 when every check passes and 1 otherwise.
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

System.exit(new ImportCatalogueHarness(loader).run())


class ImportCatalogueHarness {
    static final String NS = "http://example.org/aberowl-test/"
    static final String B = NS + "b.owl"
    static final String C = NS + "c.owl"
    static final String C_V = NS + "releases/1/c.owl"
    static final String R = NS + "r.owl"
    static final String OTHER = NS + "other.owl"
    static final String CHAIN = NS + "chain.owl"
    static final String PLAIN = NS + "plain.owl"
    static final String NET = NS + "net.owl"
    static final String NET_HTTP = NS + "nethttp.owl"
    // Modelled on FLOPO: the root imports a module by its download URL, and
    // the module declares a different (PURL-style) ontology IRI.
    static final String ALIAS_ROOT = NS + "aliasroot.owl"
    static final String MODULE = NS + "imports/go_import.owl"
    static final String MODULE_V = NS + "releases/2/imports/go_import.owl"
    static final String MODULE_URL = "https://raw.example.org/repo/master/ontology/imports/go_import.owl"
    static final String MODULE_MIRROR = "http://mirror.example.org/go_import.owl"
    static final String ROOT_URL = "https://raw.example.org/repo/master/ontology/aliasroot.owl"
    static final String MODULE2 = NS + "imports/module2.owl"
    // module: cellular process (G_0001), cell death (G_0002).
    // module2: tissue death (M_0001). aliasroot: flower wilting (A_0001).
    static final String CELLULAR_PROCESS = NS + "G_0001", CELL_DEATH = NS + "G_0002"
    static final String TISSUE_DEATH = NS + "M_0001", WILTING = NS + "A_0001"
    // Browsing roots, modelled on FLOPO: pheno declares its own root terms
    // (IAO_0000700) and imports an upper ontology whose top-level classes are
    // not FLOPO's, and which declares a root term of its own.
    static final String ROOT_TERM = "http://purl.obolibrary.org/obo/IAO_0000700"
    static final String OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
    static final String UPPER = NS + "browse/upper.owl"
    static final String PHENO = NS + "browse/pheno.owl"
    static final String BADROOTS = NS + "browse/badroots.owl"
    static final String CONTINUANT = NS + "U_0001", OCCURRENT = NS + "U_0002", QUALITY = NS + "U_0003"
    static final String FLORA = NS + "F_0001", FAUNA = NS + "F_0002", IMPOSSIBLE = NS + "F_0003"
    static final String LEAF_SHAPE = NS + "F_0004", ORPHAN = NS + "F_0005", OBSOLETE = NS + "F_0006"
    static final String MISSING = NS + "F_9999"

    // B: animal (B_0001), dog (B_0002). C: organism (C_0001).
    // R: puppy (R_0001), animal owner (R_0002), puppy owner (R_0003), owns (R_0004).
    static final String ANIMAL = NS + "B_0001", DOG = NS + "B_0002", ORGANISM = NS + "C_0001"
    static final String PUPPY = NS + "R_0001", ANIMAL_OWNER = NS + "R_0002", PUPPY_OWNER = NS + "R_0003"
    static final String BEAGLE = NS + "O_0001", TERRIER = NS + "H_0001"

    final ClassLoader loader
    final Class RM
    final Class IRI_CLASS
    Path tmp, data, outside, catalogues, exports
    Map<String, String> files = [:]
    ServerSocket listener
    int passed = 0
    List<String> failures = []
    String current

    ImportCatalogueHarness(ClassLoader loader) {
        this.loader = loader
        this.RM = loader.loadClass("src.RequestManager")
        this.IRI_CLASS = loader.loadClass("org.semanticweb.owlapi.model.IRI")
    }

    int run() {
        // Real path, so fixture paths match the resolved paths in error messages.
        tmp = Files.createTempDirectory("aberowl-imports-").toRealPath()
        data = Files.createDirectory(tmp.resolve("data"))
        outside = Files.createDirectory(tmp.resolve("outside"))
        catalogues = Files.createDirectory(data.resolve("import_catalogues"))
        exports = Files.createDirectory(tmp.resolve("exports"))
        System.setProperty("aberowl.dataRoot", data.toString())
        System.setProperty("aberowl.exportRoot", exports.toString())
        listener = new ServerSocket(0, 50, InetAddress.getLoopbackAddress())
        writeFixtures()

        def tests = [
            "default policy suppresses imports"              : this.&defaultSuppressesImports,
            "default policy, ontology without imports"       : this.&defaultWithoutImports,
            "catalogue resolves the closure"                 : this.&catalogueResolvesClosure,
            "transitive import by version IRI, cycle, alias" : this.&transitiveVersionCycleAlias,
            "import URL differs from the ontology IRI"       : this.&documentUrlAlias,
            "strict: missing direct import fails"            : this.&missingDirectImportFails,
            "strict: missing transitive import fails"        : this.&missingTransitiveImportFails,
            "strict: malformed import document fails"        : this.&malformedImportFails,
            "strict: one IRI naming two files fails"         : this.&conflictingIdentitiesFail,
            "catalogue paths stay inside the data root"      : this.&pathContainment,
            "invalid catalogue files fail"                   : this.&invalidCatalogues,
            "other roots stay unaffected"                    : this.&otherRootsUnaffected,
            "no network and no file: dereferencing"          : this.&noNetworkNoFileIri,
            "reload, hot-swap manager and dispose"           : this.&reloadAndDispose,
            "failed reload keeps the serving copy"           : this.&failedReloadKeepsServingCopy,
            "validating a staged update loads like a load"   : this.&validateStagedLikeALoad,
            "staged root: entries naming the served file"    : this.&stagedRootServedIdentity,
            "validation export: merged, no imports"          : this.&validationExport,
            "finalize: served path replaces the staged one"  : this.&finalizeServedPath,
            "update servlets: paths and the task guard"      : this.&updateFilesHelpers,
            "browse: declared roots of a merged ontology"    : this.&declaredBrowseRoots,
            "browse: owl:Thing fallback"                     : this.&browseRootsFallback,
            "browse: reload and dispose"                     : this.&browseRootsLifecycle,
        ]
        tests.each { name, test ->
            current = name
            clearCatalogues()
            int before = failures.size()
            try {
                test()
            } catch (Throwable t) {
                failures << "${name}: unexpected ${t.getClass().getName()}: ${t.getMessage()}"
                t.printStackTrace()
            }
            println((failures.size() == before ? "PASS  " : "FAIL  ") + name)
        }
        listener.close()
        tmp.toFile().deleteDir()

        println "\n${passed} checks passed, ${failures.size()} failed"
        failures.each { println "  - ${it}" }
        return failures ? 1 : 0
    }

    // ---- fixtures ----------------------------------------------------------

    static String ofn(String iri, String version, List<String> imports, String header, String body) {
        """Prefix(:=<${NS}>)
Prefix(rdfs:=<http://www.w3.org/2000/01/rdf-schema#>)
Prefix(owl:=<http://www.w3.org/2002/07/owl#>)
Ontology(<${iri}>${version ? " <${version}>" : ""}
${imports.collect { "Import(<${it}>)" }.join("\n")}
Annotation(rdfs:label "${header}")
${body}
)
"""
    }

    // Like ofn, with any header annotations.
    static String ofnHeader(String iri, List<String> imports, List<String> header, String body) {
        """Prefix(:=<${NS}>)
Prefix(rdfs:=<http://www.w3.org/2000/01/rdf-schema#>)
Prefix(owl:=<http://www.w3.org/2002/07/owl#>)
Prefix(xsd:=<http://www.w3.org/2001/XMLSchema#>)
Ontology(<${iri}>
${imports.collect { "Import(<${it}>)" }.join("\n")}
${header.join("\n")}
Declaration(AnnotationProperty(<${ROOT_TERM}>))
${body}
)
"""
    }

    static String label(String local, String text) {
        "Declaration(Class(:${local}))\nAnnotationAssertion(rdfs:label :${local} \"${text}\")\n"
    }

    void writeFixture(String key, Path path, String text) {
        Files.createDirectories(path.getParent())
        path.text = text
        files[key] = path.toString()
    }

    void writeFixtures() {
        String bText = ofn(B, null, [C_V, B], "B header",
            label("B_0001", "animal") + label("B_0002", "dog") + "SubClassOf(:B_0002 :B_0001)\n")
        writeFixture("b", data.resolve("b/b.owl"), bText)
        writeFixture("c", data.resolve("c/c.owl"), ofn(C, C_V, [R], "C header",
            label("C_0001", "organism") + "Declaration(Class(:B_0001))\nSubClassOf(:B_0001 :C_0001)\n"))
        writeFixture("r", data.resolve("r/r.owl"), ofn(R, null, [B, C, R], "Root header",
            label("R_0001", "puppy") + label("R_0002", "animal owner") + label("R_0003", "puppy owner") +
            "Declaration(ObjectProperty(:R_0004))\nDeclaration(Class(:B_0001))\nDeclaration(Class(:B_0002))\n" +
            "SubClassOf(:R_0001 :B_0002)\n" +
            "EquivalentClasses(:R_0002 ObjectSomeValuesFrom(:R_0004 :B_0001))\n" +
            "SubClassOf(:R_0003 ObjectSomeValuesFrom(:R_0004 :R_0001))\n"))
        writeFixture("other", data.resolve("other/other.owl"), ofn(OTHER, null, [B], "Other header",
            label("O_0001", "beagle") + "Declaration(Class(:B_0002))\nSubClassOf(:O_0001 :B_0002)\n"))
        writeFixture("chain", data.resolve("chain/chain.owl"), ofn(CHAIN, null, [B], "Chain header",
            label("H_0001", "terrier") + "Declaration(Class(:B_0002))\nSubClassOf(:H_0001 :B_0002)\n"))
        writeFixture("plain", data.resolve("plain/plain.owl"), ofn(PLAIN, null, [], "Plain header",
            label("P_0001", "thing one") + label("P_0002", "thing two") + "SubClassOf(:P_0002 :P_0001)\n"))
        writeFixture("garbage", data.resolve("bad/garbage.owl"), "this is not an ontology {{{ ]]\n")
        writeFixture("bcopy", data.resolve("bcopy/b.owl"), bText)

        // module imports itself by a URL alias and by its version IRI, and the
        // root by a URL alias (a cycle); module2 imports module by its
        // ontology IRI and by a second alias.
        writeFixture("module", data.resolve("module/go_import.owl"), ofn(MODULE, MODULE_V,
            [MODULE_URL, MODULE_V, ROOT_URL], "Module header",
            label("G_0001", "cellular process") + label("G_0002", "cell death") + "SubClassOf(:G_0002 :G_0001)\n"))
        writeFixture("module2", data.resolve("module2/module2.owl"), ofn(MODULE2, null,
            [MODULE, MODULE_MIRROR], "Module2 header",
            label("M_0001", "tissue death") + "Declaration(Class(:G_0002))\nSubClassOf(:M_0001 :G_0002)\n"))
        writeFixture("aliasroot", data.resolve("aliasroot/aliasroot.owl"), ofn(ALIAS_ROOT, null,
            [MODULE_URL, MODULE2], "Alias root header",
            label("A_0001", "flower wilting") + "Declaration(Class(:G_0002))\nSubClassOf(:A_0001 :G_0002)\n"))

        writeFixture("upper", data.resolve("upper/upper.owl"), ofnHeader(UPPER, [],
            ["Annotation(rdfs:label \"Upper header\")", "Annotation(<${ROOT_TERM}> :U_0001)"],
            label("U_0001", "continuant") + label("U_0002", "occurrent") + label("U_0003", "quality") +
            "SubClassOf(:U_0003 :U_0001)\n"))
        // Declared root terms: two usable classes (flora phenotype is not a
        // top-level class once the import is merged), the second one again as
        // an IRI-valued literal, free text, a class the ontology lacks,
        // owl:Thing, an unsatisfiable class and a deprecated class.
        writeFixture("pheno", data.resolve("pheno/pheno.owl"), ofnHeader(PHENO, [UPPER], [
                "Annotation(rdfs:label \"Pheno header\")",
                "Annotation(<${ROOT_TERM}> :F_0001)",
                "Annotation(<${ROOT_TERM}> :F_0002)",
                "Annotation(<${ROOT_TERM}> \"${FAUNA}\")",
                "Annotation(<${ROOT_TERM}> \"not an iri\")",
                "Annotation(<${ROOT_TERM}> :F_9999)",
                "Annotation(<${ROOT_TERM}> owl:Thing)",
                "Annotation(<${ROOT_TERM}> :F_0003)",
                "Annotation(<${ROOT_TERM}> :F_0006)"],
            label("F_0001", "flora phenotype") + label("F_0002", "fauna phenotype") +
            label("F_0003", "impossible phenotype") + label("F_0004", "leaf shape") +
            label("F_0005", "orphan term") + label("F_0006", "obsolete phenotype") +
            "Declaration(Class(:U_0003))\nSubClassOf(:F_0001 :U_0003)\nSubClassOf(:F_0004 :F_0001)\n" +
            "SubClassOf(:F_0003 owl:Nothing)\n" +
            "AnnotationAssertion(owl:deprecated :F_0006 \"true\"^^xsd:boolean)\n"))
        writeFixture("badroots", data.resolve("badroots/badroots.owl"), ofnHeader(BADROOTS, [], [
                "Annotation(rdfs:label \"Bad roots header\")",
                "Annotation(<${ROOT_TERM}> :F_9999)",
                "Annotation(<${ROOT_TERM}> \"free text\")"],
            label("X_0001", "lonely") + label("X_0002", "other") + "SubClassOf(:X_0002 :X_0001)\n"))

        writeFixture("outsideB", outside.resolve("b.owl"), bText)
        writeFixture("outsideCatalogue", outside.resolve("catalogue.json"),
            JsonOutput.toJson([imports: [(B): files.b, (C): files.c]]))
        writeFixture("net", data.resolve("net/net.owl"), ofn(NET, null, [remoteIri(), fileIri()], "Net header",
            label("N_0001", "net dog") + "Declaration(Class(:B_0002))\nSubClassOf(:N_0001 :B_0002)\n"))
        writeFixture("nethttp", data.resolve("nethttp/nethttp.owl"), ofn(NET_HTTP, null, [remoteIri()], "Http header",
            label("N_0002", "http dog") + "Declaration(Class(:B_0002))\nSubClassOf(:N_0002 :B_0002)\n"))

        Files.createSymbolicLink(data.resolve("escape.owl"), outside.resolve("b.owl"))
        Files.createDirectory(data.resolve("dir.owl"))
    }

    void clearCatalogues() {
        catalogues.toFile().listFiles().each { it.delete() }
    }

    void writeCatalogue(String ontId, Map imports) {
        catalogues.resolve("${ontId}.json").text = JsonOutput.toJson([imports: imports])
    }

    void writeRawCatalogue(String ontId, String text) {
        catalogues.resolve("${ontId}.json").text = text
    }

    // ---- helpers -----------------------------------------------------------

    // An import IRI served by the harness's own listener, which only counts connections.
    String remoteIri() {
        "http://127.0.0.1:${listener.getLocalPort()}/remote.owl"
    }

    // A file: import IRI naming a real ontology outside the data root.
    String fileIri() {
        outside.resolve("b.owl").toUri().toString()
    }

    def newManager() {
        RM.getDeclaredConstructor().newInstance()
    }

    def load(m, String ontId, String key) {
        m.loadOntology(ontId, files[key], "elk")
        m.createReasoner(ontId)
        return m
    }

    def owlClass(m, String iri) {
        m.df.getOWLClass(IRI_CLASS.create(iri))
    }

    Set<String> superclasses(m, String ontId, String iri) {
        m.reasoners.get(ontId).getSuperClasses(owlClass(m, iri), false).getFlattened()
            .collect { it.getIRI().toString() } as Set
    }

    Set<String> signature(m, String ontId) {
        m.getOntology(ontId).getClassesInSignature(true).collect { it.getIRI().toString() } as Set
    }

    Map listing(m, String ontId) {
        m.listOntologies().find { it.ontologyId == ontId }
    }

    void check(boolean condition, String message) {
        if (condition) {
            passed++
        } else {
            failures << "${current}: ${message}"
        }
    }

    void expectImportFailure(String message, List<String> fragments, Closure action) {
        try {
            action()
            failures << "${current}: ${message}: no exception"
        } catch (Throwable t) {
            boolean typed = t.getClass().getName() == "src.ImportResolutionException"
            def missing = fragments.findAll { !t.getMessage()?.contains(it) }
            check(typed && !missing, "${message}: got ${t.getClass().getName()}: ${t.getMessage()} (missing ${missing})")
        }
    }

    void checkNotLoaded(m, String ontId) {
        check(!m.hasOntology(ontId), "${ontId} must not be loaded after an import failure")
        check(!m.isImportsLoaded(ontId), "${ontId} must not report imports loaded")
        check(m.getImportsPolicy(ontId) == null, "${ontId} must have no recorded import policy")
    }

    void checkNoConnection() {
        listener.setSoTimeout(300)
        try {
            listener.accept().close()
            failures << "${current}: a connection reached the import IRI's host"
        } catch (SocketTimeoutException expected) {
            passed++
        }
    }

    // ---- tests -------------------------------------------------------------

    void defaultSuppressesImports() {
        def m = load(newManager(), "rootont", "r")
        def supers = superclasses(m, "rootont", PUPPY)
        check(DOG in supers, "asserted puppy ⊑ dog is kept")
        check(!(ANIMAL in supers), "dog ⊑ animal lives only in the import, so puppy ⊑ animal must not be entailed")
        check(!(ANIMAL_OWNER in superclasses(m, "rootont", PUPPY_OWNER)),
            "puppy owner ⊑ animal owner needs the imported axiom")
        check(!m.isImportsLoaded("rootont"), "imports_loaded is false when imports were dropped")
        check(m.getImportsPolicy("rootont") == "suppressed", "policy is suppressed by default")
        check(m.getImportsDeclaredCount("rootont") == 3, "three direct imports declared")
        check(m.getImportsResolvedCount("rootont") == 0, "nothing resolved")
        def info = listing(m, "rootont")
        check(info.importsLoaded == false && info.importsPolicy == "suppressed" && info.importsDeclared == 3,
            "listOntologies reports the default policy: ${info}")
        def labelled = []
        try {
            labelled = m.runQuery("rootont", "'animal'", "subclass")
        } catch (Exception expected) {
        }
        check(labelled.isEmpty(), "the imported label 'animal' must not resolve without the import")
    }

    void defaultWithoutImports() {
        def m = load(newManager(), "plain", "plain")
        check(m.isImportsLoaded("plain"), "an ontology with no imports has its whole (empty) closure")
        check(m.getImportsPolicy("plain") == "suppressed", "still the default policy")
        check(m.getImportsDeclaredCount("plain") == 0, "no imports declared")
        check(m.getImportsResolvedCount("plain") == 0, "nothing resolved")
        check(NS + "P_0001" in superclasses(m, "plain", NS + "P_0002"), "the ontology still classifies")
    }

    void catalogueResolvesClosure() {
        writeCatalogue("rootont", [(B): files.b, (C): files.c])
        def m = load(newManager(), "rootont", "r")
        def supers = superclasses(m, "rootont", PUPPY)
        check([DOG, ANIMAL, ORGANISM].every { it in supers }, "puppy ⊑ dog ⊑ animal ⊑ organism: ${supers}")
        check(ANIMAL_OWNER in superclasses(m, "rootont", PUPPY_OWNER),
            "the logical definition of animal owner applies through the imported axiom")
        def subs = m.runQuery("rootont", "'animal'", "subclass").collect { it["class"] } as Set
        check(DOG in subs && PUPPY in subs, "the imported label 'animal' resolves: ${subs}")
        def dog = m.runQuery("rootont", "<${PUPPY}>", "superclass").find { it["class"] == DOG }
        check(dog?.label == "dog", "imported classes are displayed with their imported label: ${dog}")
        check(m.isImportsLoaded("rootont"), "imports_loaded reflects the complete closure")
        check(m.getImportsPolicy("rootont") == "catalogue", "policy is catalogue")
        check(m.getImportsResolvedCount("rootont") == 2, "B and C merged once each, the self-import is no document")
        check(m.getImportsDeclaredCount("rootont") == 3, "importsDeclared still counts direct declarations")
        def header = m.getOntology("rootont").getAnnotations().collect { it.getValue().getLiteral() } as Set
        check(header == ["Root header"] as Set, "only the root's header annotations are kept: ${header}")
        check(m.getoManager("rootont").getOntologies().size() == 2,
            "the manager keeps the root document and the merged copy only")
        check(ORGANISM in signature(m, "rootont"), "imported classes are in the merged signature")
    }

    void transitiveVersionCycleAlias() {
        // chain imports B; B imports C by its version IRI; C imports R, which
        // imports B, C and itself. C is also listed under its ontology IRI,
        // which maps to the same file and must not load it twice.
        writeCatalogue("chainroot", [(B): files.b, (C_V): files.c, (C): files.c, (R): files.r])
        def m = load(newManager(), "chainroot", "chain")
        def supers = superclasses(m, "chainroot", TERRIER)
        check([DOG, ANIMAL, ORGANISM].every { it in supers }, "terrier reaches organism through B and C: ${supers}")
        check(m.isImportsLoaded("chainroot"), "the closure is complete")
        check(m.getImportsResolvedCount("chainroot") == 3, "B, C and R merged exactly once each")
        check(m.getoManager("chainroot").getOntologies().size() == 2, "no duplicate documents left behind")
        check(PUPPY in signature(m, "chainroot"), "R, reached through the cycle, is merged")
    }

    void documentUrlAlias() {
        // Without a catalogue the module's axioms are missing.
        def plainManager = load(newManager(), "aliasroot", "aliasroot")
        check(!(CELLULAR_PROCESS in superclasses(plainManager, "aliasroot", WILTING)),
            "default: flower wilting ⊑ cellular process needs the module")
        check(!plainManager.isImportsLoaded("aliasroot"), "default: imports_loaded is false")

        // The catalogue lists the module under its download URL, a mirror URL
        // and its ontology IRI, all naming one file, and the root under a URL
        // that the module imports (a cycle through an alias).
        writeCatalogue("aliasroot", [
            (MODULE_URL): files.module, (MODULE_MIRROR): files.module, (MODULE): files.module,
            (ROOT_URL): files.aliasroot, (MODULE2): files.module2])
        def m = load(newManager(), "aliasroot", "aliasroot")
        def supers = superclasses(m, "aliasroot", WILTING)
        check(CELL_DEATH in supers && CELLULAR_PROCESS in supers,
            "flower wilting ⊑ cell death ⊑ cellular process through the aliased module: ${supers}")
        check(CELLULAR_PROCESS in superclasses(m, "aliasroot", TISSUE_DEATH),
            "module2, which imports the module by its ontology IRI, gets its axioms too")
        def subs = m.runQuery("aliasroot", "'cellular process'", "subclass").collect { it["class"] } as Set
        check(WILTING in subs && TISSUE_DEATH in subs, "the module's label resolves: ${subs}")
        check(m.isImportsLoaded("aliasroot"), "imports_loaded is true: every import, aliases included, is resolved")
        check(m.getImportsPolicy("aliasroot") == "catalogue", "policy is catalogue")
        check(m.getImportsResolvedCount("aliasroot") == 2, "the module and module2 merged once each")
        check(m.getImportsDeclaredCount("aliasroot") == 2, "two direct imports declared")
        check(m.getoManager("aliasroot").getOntologies().size() == 2, "no duplicate documents left behind")
        def header = m.getOntology("aliasroot").getAnnotations().collect { it.getValue().getLiteral() } as Set
        check(header == ["Alias root header"] as Set, "only the root's header annotations are kept: ${header}")
        def info = listing(m, "aliasroot")
        check(info.importsLoaded == true && info.importsResolved == 2, "listOntologies agrees: ${info}")
        checkNoConnection()
    }

    void missingDirectImportFails() {
        writeCatalogue("rootont", [(B): files.b])
        def m = newManager()
        expectImportFailure("an unlisted direct import", ["<${C}>", "imported by <${R}>", "not listed"]) {
            m.loadOntology("rootont", files.r, "elk")
        }
        checkNotLoaded(m, "rootont")
    }

    void missingTransitiveImportFails() {
        writeCatalogue("chainroot", [(B): files.b])
        def m = newManager()
        expectImportFailure("an unlisted transitive import", ["<${C_V}>", "imported by <${B}>", "not listed"]) {
            m.loadOntology("chainroot", files.chain, "elk")
        }
        checkNotLoaded(m, "chainroot")
    }

    void malformedImportFails() {
        writeCatalogue("rootont", [(B): files.garbage, (C): files.c])
        def m = newManager()
        expectImportFailure("an unparsable import", [files.garbage, "could not be loaded"]) {
            m.loadOntology("rootont", files.r, "elk")
        }
        checkNotLoaded(m, "rootont")
    }

    void conflictingIdentitiesFail() {
        // B is an alias for plain.owl, which declares PLAIN, but the catalogue
        // also maps PLAIN to b.owl.
        writeCatalogue("rootont", [(B): files.plain, (C): files.c, (PLAIN): files.b])
        def m = newManager()
        expectImportFailure("a file declaring an IRI the catalogue maps elsewhere",
                ["(for <${B}>)", "declares <${PLAIN}>", "the catalogue maps to ${files.b}"]) {
            m.loadOntology("rootont", files.r, "elk")
        }
        checkNotLoaded(m, "rootont")

        // C is an alias for a copy of b.owl, which declares B like b.owl does.
        // OWLAPI or the catalogue may refuse it; either way the message names
        // the file and the import that led to it.
        writeCatalogue("rootont", [(B): files.b, (C): files.bcopy])
        m = newManager()
        expectImportFailure("two files declaring the same IRI", ["${files.bcopy} (for <${C}>)"]) {
            m.loadOntology("rootont", files.r, "elk")
        }
        checkNotLoaded(m, "rootont")
    }

    void pathContainment() {
        def cases = [
            "an absolute path outside the data root": [files.outsideB, "is not under"],
            "a .. traversal out of the data root"   : [data.toString() + "/../outside/b.owl", "is not under"],
            "a symlink that escapes the data root"  : [data.resolve("escape.owl").toString(), "resolves outside"],
            "a directory"                           : [data.resolve("dir.owl").toString(), "is not a regular file"],
            "a missing file"                        : [data.resolve("missing.owl").toString(), "does not exist"],
            "a relative path"                       : ["b/b.owl", "is not an absolute path"],
            "a non-string path"                     : [42, "must be a string path"],
        ]
        if (System.getProperty("user.name") != "root") {
            Path locked = data.resolve("locked.owl")
            Files.copy(Path.of(files.b), locked)
            Files.setPosixFilePermissions(locked, PosixFilePermissions.fromString("---------"))
            cases["an unreadable file"] = [locked.toString(), "is not readable"]
        }
        cases.each { name, spec ->
            writeCatalogue("rootont", [(B): spec[0], (C): files.c])
            def m = newManager()
            expectImportFailure(name, ["<${B}>", spec[1]]) {
                m.loadOntology("rootont", files.r, "elk")
            }
            checkNotLoaded(m, "rootont")
        }
    }

    void invalidCatalogues() {
        def raw = [
            "malformed JSON"     : ['{"imports": {', "not valid JSON"],
            "an unknown key"     : ['{"imports": {}, "root": ["rootont"]}', "unknown keys"],
            "imports not a map"  : ['{"imports": []}', "\"imports\" must be an object"],
            "a top-level array"  : ['[]', "must be a JSON object"],
            "a non-string comment": ['{"imports": {}, "comment": 1}', "\"comment\" must be a string"],
            "whitespace in a key": ['{"imports": {"http://example.org/a b": "/x"}}', "contains whitespace"],
            "a relative IRI key" : ['{"imports": {"b.owl": "/x"}}', "not an absolute IRI"],
        ]
        raw.each { name, spec ->
            writeRawCatalogue("rootont", spec[0])
            def m = newManager()
            expectImportFailure(name, [spec[1]]) { m.loadOntology("rootont", files.r, "elk") }
            checkNotLoaded(m, "rootont")
        }

        clearCatalogues()
        Files.createSymbolicLink(catalogues.resolve("rootont.json"), Path.of(files.outsideCatalogue))
        def m = newManager()
        expectImportFailure("a catalogue symlinked from outside the data root", ["the catalogue file resolves outside"]) {
            m.loadOntology("rootont", files.r, "elk")
        }
        checkNotLoaded(m, "rootont")

        clearCatalogues()
        Files.createSymbolicLink(catalogues.resolve("rootont.json"), catalogues.resolve("gone.json"))
        m = newManager()
        expectImportFailure("a dangling catalogue symlink is an opt-in that fails", ["does not exist"]) {
            m.loadOntology("rootont", files.r, "elk")
        }
        checkNotLoaded(m, "rootont")
    }

    void otherRootsUnaffected() {
        // One worker, as with FLOPO and PO: the opted-in root, a root that
        // imports the same ontology without opting in, and that imported
        // ontology loaded under its own id.
        writeCatalogue("rootont", [(B): files.b, (C): files.c])
        def m = newManager()
        m.loadOntology("rootont", files.r, "elk")
        m.loadOntology("otherroot", files.other, "elk")
        m.loadOntology("b", files.b, "elk")
        m.createAllReasoners()
        check(ORGANISM in superclasses(m, "rootont", PUPPY), "the opted-in root is resolved")
        check(!(ANIMAL in superclasses(m, "otherroot", BEAGLE)),
            "B being loaded in the same worker does not supply another root's imports")
        check(!m.isImportsLoaded("otherroot") && m.getImportsPolicy("otherroot") == "suppressed",
            "a root without a catalogue keeps the default")
        check(!m.isImportsLoaded("b") && m.getImportsPolicy("b") == "suppressed",
            "the imported ontology loaded as its own root keeps the default")
        check(m.isImportsLoaded("rootont") && m.getImportsPolicy("rootont") == "catalogue",
            "the opted-in root reports its policy")
    }

    void noNetworkNoFileIri() {
        def m = load(newManager(), "netroot", "net")
        check(!m.isImportsLoaded("netroot"), "neither the http: nor the file: import is loaded")
        check(!(ANIMAL in signature(m, "netroot")), "the file: import's document was not read")
        checkNoConnection()

        // Opted in with an empty catalogue: the file: import is refused, not read.
        writeCatalogue("netroot", [:])
        def strict = newManager()
        expectImportFailure("an unlisted file: import", ["<${fileIri()}>", "not listed"]) {
            strict.loadOntology("netroot", files.net, "elk")
        }
        checkNotLoaded(strict, "netroot")

        writeCatalogue("nethttp", [:])
        def strictHttp = newManager()
        expectImportFailure("an unlisted http: import", ["<${remoteIri()}>", "not listed"]) {
            strictHttp.loadOntology("nethttp", files.nethttp, "elk")
        }
        checkNotLoaded(strictHttp, "nethttp")
        checkNoConnection()

        // Listing the http: IRI names a local file, which is read instead; the
        // IRI itself is never fetched, although the file declares another IRI.
        writeCatalogue("nethttp", [(remoteIri()): files.plain])
        def listed = load(newManager(), "nethttp", "nethttp")
        check(listed.isImportsLoaded("nethttp") && listed.getImportsResolvedCount("nethttp") == 1,
            "the listed http: import is resolved from its file")
        check(NS + "P_0001" in signature(listed, "nethttp"), "the listed file's classes are merged")
        checkNoConnection()
    }

    void reloadAndDispose() {
        writeCatalogue("rootont", [(B): files.b, (C): files.c])
        def m = load(newManager(), "rootont", "r")
        m.reloadOntology("rootont", files.r, "elk")
        check(m.getImportsPolicy("rootont") == "catalogue" && m.isImportsLoaded("rootont"),
            "a reload applies the catalogue again")
        check(ORGANISM in superclasses(m, "rootont", PUPPY), "the reloaded ontology answers with its imports")

        // updateOntology.groovy creates a fresh RequestManager when none exists
        def fresh = newManager()
        fresh.reloadOntology("rootont", files.r, "elk")
        check(fresh.getImportsPolicy("rootont") == "catalogue" && fresh.isImportsLoaded("rootont"),
            "a new manager (the hot-swap fallback) applies the same catalogue")

        writeRawCatalogue("rootont", "{")
        expectImportFailure("a reload with a broken catalogue", ["not valid JSON"]) {
            m.reloadOntology("rootont", files.r, "elk")
        }
        check(m.hasOntology("rootont") && ORGANISM in superclasses(m, "rootont", PUPPY),
            "a broken catalogue is caught before the serving copy is disposed")

        clearCatalogues()
        m.reloadOntology("rootont", files.r, "elk")
        check(m.getImportsPolicy("rootont") == "suppressed" && !m.isImportsLoaded("rootont"),
            "removing the catalogue returns the root to the default on its next load")

        m.disposeOntology("rootont")
        def leftovers = ["importsDeclared", "importsPolicies", "importsResolved", "importsUnresolved"]
            .findAll { m."${it}".containsKey("rootont") }
        check(leftovers.isEmpty(), "dispose clears the per-ontology import state: ${leftovers}")
        check(!m.isImportsLoaded("rootont") && m.getImportsPolicy("rootont") == null,
            "a disposed ontology reports nothing")
    }

    List<String> classIris(results) {
        results.collect { it["class"] as String }
    }

    List<String> thingRoots(m, String ontId) {
        classIris(m.runQuery(ontId, "<${OWL_THING}>", "subclass", true, true, false, null))
    }

    void declaredBrowseRoots() {
        writeCatalogue("pheno", [(UPPER): files.upper])
        def m = load(newManager(), "pheno", "pheno")
        check(m.isImportsLoaded("pheno"), "the upper ontology is merged")
        def browse = m.getBrowseRoots("pheno")
        check(browse.source == "declared", "declared roots are used: ${browse.source}")
        check(classIris(browse.result) == [FAUNA, FLORA],
            "only the usable declared roots, sorted by label: ${classIris(browse.result)}")
        check(browse.declared as Set == [FLORA, FAUNA, MISSING, OWL_THING, IMPOSSIBLE, OBSOLETE] as Set
                && browse.declared.size() == 6,
            "IRI values and IRI literals are read once each, free text is not: ${browse.declared}")
        check(browse.ignored as Set == [MISSING, OWL_THING, IMPOSSIBLE, OBSOLETE] as Set,
            "a missing class, owl:Thing, an unsatisfiable and a deprecated class are ignored: ${browse.ignored}")
        check(!(CONTINUANT in browse.declared), "the imported ontology's own root term is not the root's")

        // owl:Thing reasoning is untouched: every top-level class, imported or not.
        def top = thingRoots(m, "pheno")
        check([CONTINUANT, OCCURRENT, FAUNA, ORPHAN].every { it in top } && !(FLORA in top),
            "the owl:Thing query still answers every top-level class: ${top}")
        check(classIris(m.rootClassCache.get("pheno")) as Set == top as Set,
            "rootClassCache is the owl:Thing answer, not the declared roots")
        check(top.size() > browse.result.size(), "browsing narrows nothing but the starting point")

        // The imports still take part in reasoning and in displayed axioms.
        def supers = superclasses(m, "pheno", LEAF_SHAPE)
        check([FLORA, QUALITY, CONTINUANT].every { it in supers }, "leaf shape reaches the imported classes: ${supers}")
        def flora = m.runQuery("pheno", "<${FLORA}>", "equivalent", true, true, true, null).find { it["class"] == FLORA }
        check(flora?.SubClassOf?.any { it.contains("quality") },
            "flora phenotype's axioms still show its imported superclass: ${flora?.SubClassOf}")
    }

    void browseRootsFallback() {
        def plain = load(newManager(), "plain", "plain")
        def browse = plain.getBrowseRoots("plain")
        check(browse.source == "owl:Thing" && browse.declared == [] && browse.ignored == [],
            "no declared roots: owl:Thing source: ${browse}")
        check(classIris(browse.result) as Set == thingRoots(plain, "plain") as Set,
            "the fallback is exactly the owl:Thing answer")

        def bad = load(newManager(), "badroots", "badroots")
        browse = bad.getBrowseRoots("badroots")
        check(browse.source == "owl:Thing", "only unusable declared roots: owl:Thing source")
        check(browse.declared == [MISSING] && browse.ignored == [MISSING],
            "the unusable declaration is reported, the free text is dropped: ${browse}")
        check(classIris(browse.result) == [NS + "X_0001"], "the owl:Thing roots: ${classIris(browse.result)}")

        // The upper ontology served on its own uses its own declared root.
        def upper = load(newManager(), "upper", "upper")
        browse = upper.getBrowseRoots("upper")
        check(browse.source == "declared" && classIris(browse.result) == [CONTINUANT],
            "an ontology's own declaration applies when it is the root: ${browse}")

        // Declared roots do not depend on the import policy.
        def suppressed = load(newManager(), "pheno", "pheno")
        browse = suppressed.getBrowseRoots("pheno")
        check(!suppressed.isImportsLoaded("pheno") && browse.source == "declared"
                && classIris(browse.result) == [FAUNA, FLORA],
            "declared roots apply with imports suppressed too: ${classIris(browse.result)}")
    }

    void browseRootsLifecycle() {
        writeCatalogue("pheno", [(UPPER): files.upper])
        def m = load(newManager(), "pheno", "pheno")
        m.reloadOntology("pheno", files.pheno, "elk")
        def browse = m.getBrowseRoots("pheno")
        check(browse.source == "declared" && classIris(browse.result) == [FAUNA, FLORA],
            "a reload recomputes the declared roots")

        // Another document at the same id: its declarations replace the old ones.
        m.reloadOntology("pheno", files.plain, "elk")
        browse = m.getBrowseRoots("pheno")
        check(browse.source == "owl:Thing" && browse.declared == [],
            "roots from the previous document do not survive a reload: ${browse.declared}")

        m.disposeOntology("pheno")
        check(!m.declaredRootTerms.containsKey("pheno") && !m.browseRootCache.containsKey("pheno"),
            "dispose clears the browsing state")
        try {
            m.getBrowseRoots("pheno")
            failures << "${current}: a disposed ontology still answers browse roots"
        } catch (IllegalArgumentException expected) {
            passed++
        }
    }

    void failedReloadKeepsServingCopy() {
        Map valid = [(B): files.b, (C_V): files.c, (C): files.c, (R): files.r]
        writeCatalogue("chainroot", valid)
        def m = load(newManager(), "chainroot", "chain")

        Closure stillServing = { String what ->
            check(m.hasOntology("chainroot") && m.getStatus("chainroot") == "classified",
                "${what}: the old copy is still loaded and classified")
            check(ORGANISM in superclasses(m, "chainroot", TERRIER), "${what}: the old reasoner still answers")
            def subs = m.runQuery("chainroot", "'organism'", "subclass").collect { it["class"] } as Set
            check(TERRIER in subs, "${what}: label queries still work: ${subs}")
            check(m.isImportsLoaded("chainroot") && m.getImportsPolicy("chainroot") == "catalogue"
                    && m.getImportsResolvedCount("chainroot") == 3,
                "${what}: the import metadata still describes the serving copy")
        }

        // C_V, imported by B, is no longer listed.
        writeCatalogue("chainroot", [(B): files.b])
        expectImportFailure("a reload with a newly missing transitive import",
                ["<${C_V}>", "imported by <${B}>", "not listed"]) {
            m.reloadOntology("chainroot", files.chain, "elk")
        }
        stillServing("missing transitive import")

        // C_V now names an unparsable document.
        writeCatalogue("chainroot", [(B): files.b, (C_V): files.garbage])
        expectImportFailure("a reload with a malformed transitive import", [files.garbage, "could not be loaded"]) {
            m.reloadOntology("chainroot", files.chain, "elk")
        }
        stillServing("malformed transitive import")

        // The new root itself does not parse.
        writeCatalogue("chainroot", valid)
        try {
            m.reloadOntology("chainroot", files.garbage, "elk")
            failures << "${current}: reloading an unparsable root: no exception"
        } catch (Exception expected) {
            passed++
        }
        stillServing("unparsable root")

        m.reloadOntology("chainroot", files.chain, "elk")
        check(m.getStatus("chainroot") == "classified" && ORGANISM in superclasses(m, "chainroot", TERRIER),
            "a valid reload afterwards succeeds")
        check(m.getoManager("chainroot").getOntologies().size() == 2, "the reloaded manager holds no duplicates")
    }

    // validateOntology.groovy, which the update pipeline calls on a download
    // staged next to the served file before indexing or hot-swapping it.
    void validateStagedLikeALoad() {
        Path staged = data.resolve("r/r_staging.owl")
        Files.copy(new File(files.r).toPath(), staged)
        try {
            writeCatalogue("rootont", [(B): files.b, (C): files.c])
            def m = newManager()
            def summary = m.validateOntologyFile("rootont", staged.toString())
            check(summary.importsPolicy == "catalogue" && summary.importsResolved == 2 && summary.importsUnresolved == 0,
                "the root's catalogue resolves the staged copy's closure: ${summary}")
            check(summary.declaredImports == [B, C, R], "the staged root's own imports are reported: ${summary.declaredImports}")
            def withImports = summary.classCount
            check(!m.hasOntology("rootont"), "validation installs nothing")

            // A release that needs an import the catalogue lacks fails here,
            // as its load would, before anything is indexed or swapped.
            writeCatalogue("rootont", [(B): files.b])
            expectImportFailure("validation with an unlisted import", ["is not listed in the catalogue"]) {
                m.validateOntologyFile("rootont", staged.toString())
            }

            // Without a catalogue, or without an id, imports stay suppressed.
            clearCatalogues()
            summary = m.validateOntologyFile("rootont", staged.toString())
            check(summary.importsPolicy == "suppressed" && summary.importsResolved == 0,
                "no catalogue: suppressed: ${summary}")
            check(withImports > summary.classCount,
                "the imported classes were counted with the catalogue: ${withImports} vs ${summary.classCount}")
            def net = m.validateOntologyFile(null, files.net)
            check(net.importsPolicy == "suppressed" && net.importsDeclared == 2, "no id: suppressed: ${net}")
            checkNoConnection()
        } finally {
            Files.deleteIfExists(staged)
        }
    }

    // B5. The update pipeline validates and hot-swaps a staged copy,
    // /data/<id>/updates/<txn>/<id>.owl, of the served file. Catalogue entries
    // naming the served file (a cycle back to the root through a URL alias,
    // or the root's own IRI) must name the staged root, not load the served
    // file beside it.
    void stagedRootServedIdentity() {
        Path staged = data.resolve("aliasroot/updates/t1/aliasroot.owl")
        Files.createDirectories(staged.getParent())
        Files.copy(Path.of(files.aliasroot), staged)
        try {
            Map aliasCatalogue = [(MODULE_URL): files.module, (MODULE_MIRROR): files.module, (MODULE): files.module,
                                  (ROOT_URL): files.aliasroot, (MODULE2): files.module2]
            writeCatalogue("aliasroot", aliasCatalogue)
            def servedManager = newManager()
            servedManager.loadOntology("aliasroot", files.aliasroot, "elk")
            int servedCount = servedManager.getOntology("aliasroot").getClassesInSignature(true).size()

            // The defect: without the served path, the loop through ROOT_URL
            // loads the served file next to its staged copy and fails.
            expectImportFailure("a staged root without its served path", [files.aliasroot]) {
                newManager().validateOntologyFile("aliasroot", staged.toString())
            }

            def summary = newManager().validateOntologyFile("aliasroot", staged.toString(), files.aliasroot)
            check(summary.importsPolicy == "catalogue" && summary.importsResolved == 2 && summary.importsUnresolved == 0,
                "the loop through the served file resolves to the staged root: ${summary}")
            check(summary.classCount == servedCount, "the same closure as the served load: ${summary.classCount} vs ${servedCount}")

            writeCatalogue("aliasroot", aliasCatalogue + [(ALIAS_ROOT): files.aliasroot])
            summary = newManager().validateOntologyFile("aliasroot", staged.toString(), files.aliasroot)
            check(summary.importsResolved == 2, "the root's own IRI listed with the served file names the staged root")

            // The hot-swap reads the staged copy the same way.
            def m = load(newManager(), "aliasroot", "aliasroot")
            m.reloadOntology("aliasroot", staged.toString(), "elk", files.aliasroot)
            check(m.isServing("aliasroot") && CELLULAR_PROCESS in superclasses(m, "aliasroot", WILTING),
                "the swapped-in copy is classified with the module")
            check(m.getoManager("aliasroot").getOntologies().size() == 2, "no duplicate documents left behind")

            // Still strict: the root's IRI listed with any other file fails.
            writeCatalogue("aliasroot", aliasCatalogue + [(ALIAS_ROOT): files.plain])
            expectImportFailure("the root's IRI listed with another file",
                    ["declares <${ALIAS_ROOT}>", "the catalogue maps to ${files.plain}"]) {
                newManager().validateOntologyFile("aliasroot", staged.toString(), files.aliasroot)
            }
            checkNoConnection()
        } finally {
            data.resolve("aliasroot/updates").toFile().deleteDir()
        }
    }

    // B3. Indexing reads the merged ontology a hot-swap classifies, written by
    // validation with no owl:imports left, so the standalone indexer (plain
    // OWLAPI, default import handling) fetches nothing and sees every class.
    void validationExport() {
        def UF = loader.loadClass("src.UpdateFiles")
        writeCatalogue("rootont", [(B): files.b, (C): files.c])
        Path target = UF.exportFile("rootont", "txn-1")
        check(target.startsWith(exports), "the export goes under the export root: ${target}")
        def summary = newManager().validateOntologyFile("rootont", files.r, null, target)
        check(summary.exportPath == target.toString() && Files.isRegularFile(target), "the export is written: ${summary}")
        check(!target.text.contains("Import("), "the export declares no imports")

        def plain = loader.loadClass("org.semanticweb.owlapi.apibinding.OWLManager").createOWLOntologyManager()
        def exported = plain.loadOntologyFromOntologyDocument(target.toFile())
        def classes = exported.getClassesInSignature(true).collect { it.getIRI().toString() } as Set
        check(exported.getImportsDeclarations().isEmpty(), "the reloaded export has no imports")
        check(classes.size() == summary.classCount,
            "the indexer sees exactly the validated classes: ${classes.size()} vs ${summary.classCount}")
        check(ORGANISM in classes && DOG in classes, "imported classes are in the export")

        String before = target.text
        try {
            newManager().validateOntologyFile("rootont", files.r, null, target)
            failures << "${current}: an existing export was replaced"
        } catch (Exception expected) {
            passed++
        }
        check(target.text == before, "the existing export is unchanged")
        check(!Files.exists(target.resolveSibling(target.getFileName().toString() + ".part")), "no partial file is left")

        // Suppressed imports are left out of the export, so nothing is fetched.
        clearCatalogues()
        Path suppressed = UF.exportFile("netroot", "txn-2")
        def s = newManager().validateOntologyFile("netroot", files.net, null, suppressed)
        def reread = plain.loadOntologyFromOntologyDocument(suppressed.toFile())
        check(s.importsPolicy == "suppressed" && reread.getImportsDeclarations().isEmpty(),
            "a suppressed ontology exports without its imports")
        checkNoConnection()

        check(UF.deleteExport(target) && UF.deleteExport(suppressed), "exports are deleted")
        check(!Files.exists(target.getParent()), "the transaction directory goes with its export")
    }

    // B7. After a hot-swap from a staged file and its promotion, the worker
    // records the served file again, and only in place of that staged file.
    void finalizeServedPath() {
        Path staged = data.resolve("plain/updates/t1/plain.owl")
        Files.createDirectories(staged.getParent())
        Files.copy(Path.of(files.plain), staged)
        try {
            def m = load(newManager(), "plain", "plain")
            m.reloadOntology("plain", staged.toString(), "elk", files.plain)
            check(listing(m, "plain").path == staged.toString(), "after the hot-swap the staged path is recorded")
            check(m.finalizeServedPath("plain", data.resolve("other.owl").toString(), files.plain) == staged.toString(),
                "another expected path changes nothing")
            check(m.finalizeServedPath("plain", staged.toString(), files.plain) == files.plain, "the served path is recorded")
            check(listing(m, "plain").path == files.plain, "listLoadedOntologies reports the served file")
            check(m.finalizeServedPath("missing", staged.toString(), files.plain) == null
                    && !m.ontologyPaths.containsKey("missing"), "an unloaded ontology stays unloaded")
            check(m.isServing("plain"), "the classified ontology is serving")
            try {
                m.reloadOntology("plain", files.garbage, "elk", files.plain)
            } catch (Exception expected) {
            }
            check(!m.isServing("plain"), "a failed dispose-first reload reports the ontology not serving")
        } finally {
            data.resolve("plain/updates").toFile().deleteDir()
        }
    }

    void updateFilesHelpers() {
        def UF = loader.loadClass("src.UpdateFiles")
        check(UF.dataFile(files.b) == Path.of(files.b), "a file under the data root is accepted")
        [outside  : files.outsideB, traversal: data.toString() + "/../outside/b.owl",
         symlink  : data.resolve("escape.owl").toString(), directory: data.resolve("dir.owl").toString(),
         missing  : data.resolve("missing.owl").toString(), relative: "b/b.owl", root: data.toString(), empty: ""
        ].each { name, p ->
            check(UF.dataFile(p) == null, "${name} is refused: ${p}")
        }
        ["../x", "TXN", "", "a/b", "-x"].each { id ->
            check(UF.exportFile("rootont", id) == null, "transaction id '${id}' is refused")
        }
        check(UF.exportFile("../r", "t1") == null, "an unsafe ontology id is refused")
        check(UF.existingExport(files.b) == null, "a data file is not an export")
        def tasks = [a: [status: 'success', ontologyId: 'go', owlPath: '/x'],
                     b: [status: 'pending', ontologyId: 'go', owlPath: '/y']]
        check(UF.activeTask(tasks, 'go') == 'b' && UF.activeTask(tasks, 'hp') == null,
            "a pending task blocks its ontology only")
        check(UF.taskReading(tasks, '/y') == 'b' && UF.taskReading(tasks, '/x') == null,
            "only a pending task holds its file")
    }
}
