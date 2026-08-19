/**
 * Builds the DL-reasoning gold set OUTSIDE AberOWL.
 *
 * The service is never consulted: this loads the OWL release from disk, runs its
 * own ELK, and materialises the answer sets. That is what keeps the gold
 * independent of the system under test (the failure mode found in the IRI
 * experiment, where build_gold.py admitted items only if AberOWL resolved them,
 * so AberOWL scored 100% by construction).
 *
 * Two task types:
 *   T1  subsumption   - "all subclasses of X"        (partly reachable from memory/lookup)
 *   T2  existential   - "all classes that are R some C"  (reachable ONLY by reasoning)
 *
 * Answer sets are filtered to [MIN_ANSWERS, MAX_ANSWERS]. The upper bound is not
 * cosmetic: harness exec_tool hands the model at most TOOL_RESULT_CHARS of tool
 * output, and a truncated result would look like a reasoning failure when it is
 * really harness starvation.
 *
 * Usage:
 *   groovy build_dl_gold.groovy --owl go.owl --id go --out gold_go.jsonl [--n 20] [--seed 42]
 */
@Grapes([
    @Grab(group='org.semanticweb.elk', module='elk-owlapi', version='0.4.3'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-distribution', version='4.5.29'),
    @Grab(group='com.google.code.gson', module='gson', version='2.3.1'),
    @Grab(group='org.slf4j', module='slf4j-nop', version='1.7.25'),
])
import org.semanticweb.elk.owlapi.ElkReasonerFactory
import org.semanticweb.owlapi.apibinding.OWLManager
import org.semanticweb.owlapi.model.*
import org.semanticweb.owlapi.search.EntitySearcher
import com.google.gson.Gson

// ---------------------------------------------------------------- args
// Parsed by hand: Groovy 4 moved CliBuilder into a separate module, and a missing
// module would fail at Grapes-resolution time on a fresh host.
def o = [:]
for (int i = 0; i < args.length - 1; i++) {
    if (args[i].startsWith('--')) o[args[i].substring(2)] = args[i + 1]
}
def USAGE = '''usage: groovy build_dl_gold.groovy --owl FILE --id ID --out FILE
               [--classes FILE] [--n 20] [--seed 42] [--min 3] [--max 25]

  --owl      OWL file: the DEPLOYED release, not an older local copy
  --id       ontology id as registered in AberOWL, e.g. go
  --out      output JSONL
  --classes  ALSO write every class IRI here (the universe, for an offline
             fabrication check that needs no existence oracle)
  --min-inferred-frac  min share of T2 answers that must be inferred (default 0.5)'''
if (!o.owl || !o.id || !o.out) { System.err.println(USAGE); System.exit(1) }

int N       = (o.n    ?: '20') as int
long SEED   = (o.seed ?: '42') as long
int MIN_ANS = (o.min  ?: '3')  as int
int MAX_ANS = (o.max  ?: '25') as int
// A T2 item only supports the "reasoning was required" claim to the extent its
// answers are INFERRED rather than directly asserted, so require a real fraction.
double MIN_INF_FRAC = (o.'min-inferred-frac' ?: '0.5') as double
String ONT  = o.id

def rnd = new Random(SEED)
def gson = new Gson()

// ---------------------------------------------------------------- load + classify
System.err.println("loading ${o.owl} ...")
def man = OWLManager.createOWLOntologyManager()
def ont = man.loadOntologyFromOntologyDocument(new File(o.owl))
def df  = man.getOWLDataFactory()
System.err.println("  ${ont.getClassesInSignature(true).size()} classes; classifying with ELK ...")

long t0 = System.currentTimeMillis()
def reasoner = new ElkReasonerFactory().createReasoner(ont)
reasoner.precomputeInferences(org.semanticweb.owlapi.reasoner.InferenceType.CLASS_HIERARCHY)
System.err.println("  classified in ${(System.currentTimeMillis()-t0)/1000}s")

def nothing = df.getOWLNothing()
def thing   = df.getOWLThing()

/**
 * The entity's rdfs:label, or null.
 *
 * Strict on purpose. Some OBO releases carry a SECOND rdfs:label on a class as an
 * xref artifact (SO_0000101 is labelled both "transposable_element" and "wiki"),
 * and EntitySearcher returns them in non-deterministic order, so picking [0] can
 * silently put a garbage term in the prompt. An ambiguous label means we skip the
 * entity: gold quality matters more than yield, and there are far more candidates
 * than items needed.
 */
def labelOf = { OWLEntity e ->
    def ls = ont.getImportsClosure()
                .collectMany { it.getAnnotationAssertionAxioms(e.getIRI()) }
                .findAll { it.getProperty().isLabel() }
                .collect { it.getValue() }
                .findAll { it instanceof OWLLiteral }
                .collect { ((OWLLiteral) it).getLiteral().trim() }
                .unique()
    ls.size() == 1 ? ls[0] : null
}

/** All (non-bottom) named subclasses of a class expression. */
def subsOf = { OWLClassExpression ce ->
    reasoner.getSubClasses(ce, false).getFlattened().findAll { !it.isOWLNothing() }
}

def rows = []

// ---------------------------------------------------------------- T1: subsumption
System.err.println("T1: scanning named classes ...")
def t1pool = []
ont.getClassesInSignature(true).each { OWLClass c ->
    if (c.isOWLThing() || c.isOWLNothing()) return
    def lab = labelOf(c)
    if (!lab) return
    def subs = subsOf(c)
    if (subs.size() < MIN_ANS || subs.size() > MAX_ANS) return
    t1pool << [cls: c, label: lab, answers: subs]
}
System.err.println("  ${t1pool.size()} candidate T1 expressions")
Collections.shuffle(t1pool, rnd)
t1pool.take(N).each { cand ->
    rows << [
        task          : 'T1',
        ontology      : ONT,
        // `term` is what the shared harness passes to prompts.user_prompt();
        // `nl` is the same string kept under a self-documenting name.
        term          : "all subclasses of \"${cand.label}\"".toString(),
        nl            : "all subclasses of \"${cand.label}\"".toString(),
        manchester    : "<${cand.cls.getIRI()}>".toString(),
        query_type    : 'subclass',
        anchor_iris   : [cand.cls.getIRI().toString()],
        anchor_labels : [cand.label],
        gold_iris     : cand.answers.collect { it.getIRI().toString() }.sort(),
        gold_labels   : cand.answers.collect { labelOf(it) }.findAll { it != null }.sort(),
        n_gold        : cand.answers.size(),
        n_inferred    : null,
    ]
}

// ---------------------------------------------------------------- T2: existential
// Harvest ObjectSomeValuesFrom(R, C) that actually occur in the ontology, so the
// expressions are ones the ontology has something to say about.
System.err.println("T2: harvesting existential restrictions ...")
def seen = new HashSet<String>()
def t2pool = []
ont.getAxioms().each { OWLAxiom ax ->
    ax.getNestedClassExpressions().each { OWLClassExpression ce ->
        if (!(ce instanceof OWLObjectSomeValuesFrom)) return
        def svf = (OWLObjectSomeValuesFrom) ce
        if (svf.getProperty().isAnonymous()) return
        if (!(svf.getFiller() instanceof OWLClass)) return
        OWLObjectProperty p = svf.getProperty().asOWLObjectProperty()
        OWLClass f = (OWLClass) svf.getFiller()
        if (f.isOWLThing() || f.isOWLNothing()) return
        def key = "${p.getIRI()}|${f.getIRI()}"
        if (!seen.add(key)) return
        def pl = labelOf(p); def fl = labelOf(f)
        if (!pl || !fl) return
        t2pool << [prop: p, filler: f, plabel: pl, flabel: fl, expr: svf]
    }
}
System.err.println("  ${t2pool.size()} distinct (property, filler) pairs; classifying ...")

Collections.shuffle(t2pool, rnd)
def t2rows = []
for (cand in t2pool) {
    if (t2rows.size() >= N) break
    def answers
    try { answers = subsOf(cand.expr) } catch (Exception e) { continue }
    if (answers.size() < MIN_ANS || answers.size() > MAX_ANS) continue

    // How many answers are genuinely INFERRED rather than asserted? A member is
    // "asserted" only if it directly states subClassOf (R some C) itself. This
    // number is what lets the paper claim T2 needs a reasoner.
    int asserted = 0
    answers.each { OWLClass m ->
        def direct = EntitySearcher.getSuperClasses(m, ont).any { it.equals(cand.expr) }
        if (direct) asserted++
    }
    int inferred = answers.size() - asserted
    if (inferred < 1) continue      // pure lookup would suffice; not a reasoning item
    if ((inferred / (double) answers.size()) < MIN_INF_FRAC) continue

    t2rows << [
        task          : 'T2',
        ontology      : ONT,
        term          : "all classes that are \"${cand.plabel}\" some \"${cand.flabel}\"".toString(),
        nl            : "all classes that are \"${cand.plabel}\" some \"${cand.flabel}\"".toString(),
        manchester    : "<${cand.prop.getIRI()}> some <${cand.filler.getIRI()}>".toString(),
        query_type    : 'subclass',
        anchor_iris   : [cand.prop.getIRI().toString(), cand.filler.getIRI().toString()],
        anchor_labels : [cand.plabel, cand.flabel],
        gold_iris     : answers.collect { it.getIRI().toString() }.sort(),
        gold_labels   : answers.collect { labelOf(it) }.findAll { it != null }.sort(),
        n_gold        : answers.size(),
        n_inferred    : inferred,
        inferred_frac : (inferred / (double) answers.size()),
    ]
}
rows.addAll(t2rows)

// ---------------------------------------------------------------- emit
new File(o.out).withWriter('UTF-8') { w ->
    rows.each { w.writeLine(gson.toJson(it)) }
}

// The class universe. With this on disk, "did the model invent this IRI?" is a set
// membership test against the same release the gold came from - deterministic,
// offline, and immune to the resolver flakiness that broke the IRI experiment's
// existence oracle (302s not followed, 429 storms scored as non-existence).
if (o.classes) {
    new File(o.classes).withWriter('UTF-8') { w ->
        ont.getClassesInSignature(true)
           .findAll { !it.isOWLThing() && !it.isOWLNothing() }
           .collect { it.getIRI().toString() }
           .sort()
           .each { w.writeLine(it) }
    }
    System.err.println("wrote class universe to ${o.classes}")
}
def nT1 = rows.count { it.task == 'T1' }
def nT2 = rows.count { it.task == 'T2' }
System.err.println("wrote ${rows.size()} items to ${o.out}  (T1=${nT1}, T2=${nT2})")
if (nT1 < N) System.err.println("  WARNING: only ${nT1} T1 items (wanted ${N})")
if (nT2 < N) System.err.println("  WARNING: only ${nT2} T2 items (wanted ${N}) - ontology may be too thin")
reasoner.dispose()
