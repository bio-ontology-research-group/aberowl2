// Add println right at the beginning of execution flow
println "--- OntologyServer.groovy execution started ---"

@Grapes([
    @Grab(group='org.eclipse.rdf4j', module='rdf4j-runtime', version='2.5.4'),
    @Grab(group='javax.servlet', module='javax.servlet-api', version='3.1.0'),
    @Grab(group='javax.servlet.jsp', module='javax.servlet.jsp-api', version='2.3.1'),
    @Grab(group='org.eclipse.jetty', module='jetty-server', version='9.4.7.v20170914'),
    @Grab(group='org.eclipse.jetty', module='jetty-servlet', version='9.4.7.v20170914'),
    @Grab(group='com.google.code.gson', module='gson', version='2.3.1'),
    @Grab(group='com.googlecode.json-simple', module='json-simple', version='1.1.1'),
    @Grab(group='org.slf4j', module='slf4j-nop', version='1.7.25'),
    @Grab(group='org.semanticweb.elk', module='elk-owlapi', version='0.4.3'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-distribution', version='4.5.29'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-api', version='4.5.29'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-apibinding', version='4.5.29'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-impl', version='4.5.29'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-parsers', version='4.5.29'),
    @Grab(group='net.sourceforge.owlapi', module='org.semanticweb.hermit', version='1.4.5.456'),
    @Grab(group='org.codehaus.gpars', module='gpars', version='1.1.0'),
    @Grab(group='com.google.guava', module='guava', version='19.0'),
    @Grab(group='ch.qos.reload4j', module='reload4j', version='1.2.18.5'),
    @GrabExclude(group='xml-apis', module='xml-apis'),
    @GrabExclude(group='log4j', module='log4j'),
    @Grab(group='aopalliance', module='aopalliance', version='1.0'),
    @Grab(group='javax.el', module='javax.el-api', version='3.0.0'),
    @GrabConfig(systemClassLoader=true)
])

// Imports must come after @Grapes annotations
import org.eclipse.jetty.server.Server
import org.eclipse.jetty.server.ServerConnector
import org.eclipse.jetty.servlet.*
import org.eclipse.jetty.server.handler.*
import groovy.servlet.*
import src.*
import java.util.concurrent.*
import org.eclipse.jetty.server.nio.*
import org.eclipse.jetty.util.thread.*
import org.eclipse.jetty.util.log.Log
import org.eclipse.jetty.util.log.StdErrLog
import groovy.json.*
import groovyx.gpars.GParsPool


println "--- Imports and Grapes complete ---"

/**
 * Start the Jetty server and load ontologies.
 *
 * Supports two modes:
 *   1. Single ontology: pass a file path as first argument
 *   2. Multi-ontology: pass a directory path or JSON config file as first argument
 *
 * In multi-ontology mode, the directory is scanned for .owl files, or the JSON
 * config file lists ontologies with their IDs, paths, and reasoner types.
 *
 * JSON config format:
 *   [
 *     {"id": "GO", "path": "/data/go/go.owl", "reasoner": "elk"},
 *     {"id": "HP", "path": "/data/hp/hp.owl", "reasoner": "elk"},
 *     ...
 *   ]
 */
def startServer(def ontologyArg, def port) {

    println "Starting Jetty server process..."
    Server server = new Server(port)
    if (!server) {
        System.err.println("Failed to create server, cannot open port " + port)
        System.exit(-1)
    }

    def context = new ServletContextHandler(server, '/', ServletContextHandler.SESSIONS)

    // Set resource base relative to the script's CWD (which is set to aberowlapi/ by Popen)
    context.resourceBase = '.'
    println "Setting Jetty resourceBase to: ${new File(context.resourceBase).absolutePath}"

    def localErrorHandler = new ErrorHandler()
    localErrorHandler.setShowStacks(true)
    context.setErrorHandler(localErrorHandler)

    // Register all servlets
    context.addServlet(new ServletHolder(new GroovyServlet()), '/health.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/health.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/runQuery.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/reloadOntology.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/findRoot.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/getObjectProperties.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/retrieveRSuccessors.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/retrieveAllLabels.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/getStatistics.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/sparql.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/runSparqlQuery.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/getSparqlExamples.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/elastic.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/updateOntology.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/updateStatus.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/validateOntology.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/triggerIndexing.groovy')
    // Dynamic ontology management endpoints
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/addOntology.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/removeOntology.groovy')
    context.addServlet(new ServletHolder(new GroovyServlet()), '/api/listLoadedOntologies.groovy')

    context.setAttribute('port', port)
    context.setAttribute('version', '0.2')

    println "Attempting to start Jetty server on port ${port}..."
    try {
        server.start()
        println "Server started successfully on " + server.getURI()
    } catch (Exception e) {
        println "FATAL ERROR during Jetty server start: ${e.getMessage()}"
        e.printStackTrace()
        System.exit(1)
    }

    // Initialize shared context attributes
    context.getServletContext().setAttribute("updateTasks", new ConcurrentHashMap())

    // Create the multi-ontology RequestManager
    def manager = new RequestManager()

    def ontologyFile = new File(ontologyArg)

    if (ontologyFile.isDirectory()) {
        // Multi-ontology mode: load all .owl files from the directory
        println "Multi-ontology mode: scanning directory ${ontologyFile.absolutePath}"
        def owlFiles = ontologyFile.listFiles({ dir, name -> name.endsWith('.owl') } as FilenameFilter)
        if (owlFiles == null || owlFiles.length == 0) {
            println "WARNING: No .owl files found in ${ontologyFile.absolutePath}"
        } else {
            owlFiles.each { owlFile ->
                def ontId = owlFile.name.replaceAll(/\.owl$/, '').replaceAll(/_active$/, '')
                try {
                    println "Loading ontology: ${ontId} from ${owlFile.absolutePath}"
                    manager.loadOntology(ontId, owlFile.absolutePath)
                } catch (Exception e) {
                    println "ERROR loading ${ontId}: ${e.getMessage()}"
                    e.printStackTrace()
                }
            }
            // Classify all loaded ontologies in parallel
            println "Classifying all loaded ontologies..."
            manager.createAllReasoners()
        }
    } else if (ontologyArg.endsWith('.json')) {
        // Multi-ontology mode: JSON config file
        println "Multi-ontology mode: reading config from ${ontologyArg}"
        def config = new JsonSlurper().parse(new File(ontologyArg))
        config.each { entry ->
            def ontId = entry.id
            def ontPath = entry.path
            def reasonerType = entry.reasoner ?: "elk"
            try {
                println "Loading ontology: ${ontId} from ${ontPath} with reasoner: ${reasonerType}"
                manager.loadOntology(ontId, ontPath, reasonerType)
            } catch (Exception e) {
                println "ERROR loading ${ontId}: ${e.getMessage()}"
                e.printStackTrace()
            }
        }
        println "Classifying all loaded ontologies..."
        manager.createAllReasoners()
    } else {
        // Single ontology mode (backward compatibility)
        println "Single ontology mode: ${ontologyArg}"
        String ontId = System.getenv("ONTOLOGY_ID")
        if (!ontId) {
            // Derive from filename
            ontId = ontologyFile.name.replaceAll(/\.owl$/, '').replaceAll(/_active$/, '')
        }
        String reasonerType = System.getenv("REASONER_TYPE") ?: "elk"

        if (!ontologyFile.exists()) {
            println "ERROR: Ontology file does not exist at path: ${ontologyArg} (absolute: ${ontologyFile.absolutePath})"
        } else {
            println "Verified: Ontology file exists at path: ${ontologyArg}"
        }

        println "Loading ontology: ${ontId} from ${ontologyArg} with reasoner: ${reasonerType}"
        try {
            manager.loadOntology(ontId, ontologyArg, reasonerType)
            manager.createReasoner(ontId)
            println "Successfully loaded and classified ${ontId}"
        } catch (Exception e) {
            println "ERROR loading ${ontId}: ${e.getMessage()}"
            e.printStackTrace()
            // Retry once
            println "Retrying..."
            try {
                manager.loadOntology(ontId, ontologyArg, reasonerType)
                manager.createReasoner(ontId)
                println "Retry successful for ${ontId}"
            } catch (Exception e2) {
                println "Retry also failed for ${ontId}: ${e2.getMessage()}"
                e2.printStackTrace()
            }
        }
    }

    def loadedCount = manager.listOntologies().size()
    if (loadedCount > 0) {
        context.getServletContext().setAttribute("manager", manager)
        println "RequestManager set in context with ${loadedCount} ontologies."
    } else {
        println "ERROR: No ontologies loaded. API calls will fail."
        // Still set the empty manager so servlets don't NPE
        context.getServletContext().setAttribute("manager", manager)
    }

    println "Ontology loading sequence complete. Jetty server is running."
}


// --- Script Execution Start ---
println "--- Executable script part started ---"

if (args.length < 1) {
    System.err.println("FATAL ERROR: Ontology file/directory path argument missing.")
    System.exit(1)
}
def ontologyArg = args[0]
println "OntologyServer.groovy received argument: ${ontologyArg} (Type: ${ontologyArg.getClass().getName()})"

println "Proceeding to start server with ontology: ${ontologyArg}"
try {
    startServer(ontologyArg, 8080) // Use port 8080
} catch (Exception e) {
    println("FATAL ERROR during startServer call: ${e.getMessage()}")
    e.printStackTrace(System.err)
    System.exit(1)
}

println "OntologyServer.groovy script main execution finished. Server should be running in background threads."
// The script itself finishes, but the Jetty server thread keeps the process alive.
