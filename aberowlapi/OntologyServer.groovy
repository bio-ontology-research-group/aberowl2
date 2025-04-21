// Add println right at the beginning of execution flow
println "--- OntologyServer.groovy execution started --- (Grapes Disabled Test)"

/*
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
    @Grab(group='net.sourceforge.owlapi', module='owlapi-api', version='4.3.2'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-apibinding', version='4.3.2'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-impl', version='4.3.2'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-parsers', version='4.3.2'),
    @Grab(group='org.codehaus.gpars', module='gpars', version='1.1.0'),
    @Grab(group='com.google.guava', module='guava', version='19.0'),
    @Grab(group='ch.qos.reload4j', module='reload4j', version='1.2.18.5'),
    @GrabExclude(group='xml-apis', module='xml-apis'),
    @GrabExclude(group='log4j', module='log4j'),
    @Grab(group='aopalliance', module='aopalliance', version='1.0'),
    @Grab(group='javax.el', module='javax.el-api', version='3.0.0'),
    @GrabConfig(systemClassLoader=true)
])
*/

// Imports must come after @Grapes annotations (Commented out)
/*
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
*/

// Set Jetty logging (can be noisy)
// Log.setLog(new StdErrLog())

println "--- Imports and Grapes section skipped (Grapes Disabled Test) ---"

/* // Commented out startServer function definition
def startServer(def ontologyFilePath, def port) {

    println "Starting Jetty server process..."
    // ... rest of function commented out ...
}
*/


// --- Script Execution Start ---
println "--- Executable script part started --- (Grapes Disabled Test)"

if (args.length < 1) {
    System.err.println("FATAL ERROR: Ontology file path argument missing.")
    System.exit(1) // Exit if no ontology path is provided
} else {
    def ontologyArg = args[0]
    println "OntologyServer.groovy received argument: ${ontologyArg} (Type: ${ontologyArg.getClass().getName()})"
}


println "--- Skipping server start call --- (Grapes Disabled Test)"
/* // Commented out server start call
try {
    // startServer(ontologyArg, 8080) // Use port 8080
} catch (Exception e) {
    println("FATAL ERROR during startServer call: ${e.getMessage()}")
    e.printStackTrace(System.err)
    System.exit(1) // Exit if server startup fails critically
}
*/

println "--- OntologyServer.groovy script finished --- (Grapes Disabled Test)"
// The script itself finishes immediately

