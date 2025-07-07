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


// Set Jetty logging (can be noisy)
// Log.setLog(new StdErrLog())

println "--- Imports and Grapes complete ---"

def startServer(def ontologyFilePath, def port) {

    println "Starting Jetty server process..."
    Server server = new Server(port)
    if (!server) {
        System.err.println("Failed to create server, cannot open port " + port)
        System.exit(-1)
    }

    def context = new ServletContextHandler(server, '/', ServletContextHandler.SESSIONS)

    // Set resource base relative to the script's CWD (which is set to aberowlapi/ by Popen)
    context.resourceBase = '.'
    // Make sure this path is correct and accessible by the groovy process
    println "Setting Jetty resourceBase to: ${new File(context.resourceBase).absolutePath}"

    def localErrorHandler = new ErrorHandler()
    localErrorHandler.setShowStacks(true)
    context.setErrorHandler(localErrorHandler)

    // Paths are relative to resourceBase ('.', which is /app/aberowlapi)
    context.addServlet(GroovyServlet, '/health.groovy')
    context.addServlet(GroovyServlet, '/api/runQuery.groovy')
    context.addServlet(GroovyServlet, '/api/reloadOntology.groovy')
    context.addServlet(GroovyServlet, '/api/findRoot.groovy')
    context.addServlet(GroovyServlet, '/api/getObjectProperties.groovy')
    context.addServlet(GroovyServlet, '/api/retrieveRSuccessors.groovy')
    context.addServlet(GroovyServlet, '/api/retrieveAllLabels.groovy')
    context.addServlet(GroovyServlet, '/api/getStatistics.groovy')
    context.addServlet(GroovyServlet, '/api/sparql.groovy')

    context.setAttribute('port', port)
    context.setAttribute('version', '0.2')

    println "Attempting to start Jetty server on port ${port}..."
    try {
        server.start()
        println "Server started successfully on " + server.getURI()
    } catch (Exception e) {
        println "FATAL ERROR during Jetty server start: ${e.getMessage()}"
        e.printStackTrace()
        System.exit(1) // Exit if server fails to start
    }

    def manager
    def tryAgain = false

    // Extract filename to use as ID
    String ontId = ontologyFilePath.tokenize('/')[-1]
    println "Extracted ontology ID: ${ontId} from path: ${ontologyFilePath}"

    // Explicitly check if the ontology file exists from Groovy's perspective
    def ontFile = new File(ontologyFilePath)
    if (!ontFile.exists()) {
         println "ERROR from Groovy: Ontology file does not exist at path: ${ontologyFilePath} (absolute: ${ontFile.absolutePath})"
         // Decide how to handle this - maybe don't start RequestManager?
         // For now, we'll let RequestManager try and fail.
    } else {
         println "Verified from Groovy: Ontology file exists at path: ${ontologyFilePath}"
    }


    println "Attempting to load ontology via RequestManager for ID: ${ontId} using path: ${ontologyFilePath}"
    try {
        manager = RequestManager.create(ontId, ontologyFilePath)
        if (manager == null) {
            println("Initial RequestManager creation returned null for ${ontId}. Retrying...")
            tryAgain = true
        } else {
             println("Initial RequestManager creation successful for ${ontId}.")
             // Print the string representation of the manager or some status?
             // println "Manager details: ${manager.toString()}" // If toString() is useful
        }
    } catch (Exception e) {
         println("Exception during initial RequestManager.create for ${ontId}: ${e.getMessage()}")
         // Print the stack trace to stderr for better debugging in Python logs
         e.printStackTrace(System.err)
         tryAgain = true // Attempt retry even on exception
    }

    if (tryAgain) {
        println("Retrying RequestManager creation for ${ontId}...")
        try {
            manager = RequestManager.create(ontId, ontologyFilePath)
            if (manager == null){
                println("Retry RequestManager creation returned null for ${ontId}. Ontology may be unloadable.")
            } else {
                 println("Retry RequestManager creation successful for ${ontId}.")
                 // println "Manager details (retry): ${manager.toString()}"
            }
        } catch (Exception e) {
             println("Exception during retry RequestManager.create for ${ontId}: ${e.getMessage()}")
             e.printStackTrace(System.err)
        }
    }

    if (manager != null) {
        context.setAttribute("manager", manager)
        println("RequestManager set in context for ${ontId}.")
    } else {
        println("ERROR: Failed to create or set RequestManager in context for ${ontId}. API calls relying on it will fail.")
        // Consider stopping the server if the manager is essential? Or let it run for health checks?
        // server.stop() // Example: Stop server if manager fails
        // System.exit(1)
    }

    // Keep the script running while the server is active.
    // server.join() // Don't join here, let the Python parent manage the process lifetime
    println "Ontology loading sequence complete. Jetty server is running."
}


// --- Script Execution Start ---
println "--- Executable script part started ---"

if (args.length < 1) {
    System.err.println("FATAL ERROR: Ontology file path argument missing.")
    System.exit(1) // Exit if no ontology path is provided
}
def ontologyArg = args[0]
println "OntologyServer.groovy received argument: ${ontologyArg} (Type: ${ontologyArg.getClass().getName()})"

// Removed blocking System.in read

println "Proceeding to start server with ontology: ${ontologyArg}"
try {
    startServer(ontologyArg, 8080) // Use port 8080
} catch (Exception e) {
    println("FATAL ERROR during startServer call: ${e.getMessage()}")
    e.printStackTrace(System.err)
    System.exit(1) // Exit if server startup fails critically
}

println "OntologyServer.groovy script main execution finished. Server should be running in background threads."
// The script itself finishes, but the Jetty server thread keeps the process alive.

