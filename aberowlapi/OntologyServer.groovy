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

Log.setLog(new StdErrLog())

def startServer(def ontologyFilePath, def port) {

    Server server = new Server(port)
    if (!server) {
	System.err.println("Failed to create server, cannot open port.")
	System.exit(-1)
    }
    
    def context = new ServletContextHandler(server, '/', ServletContextHandler.SESSIONS)
    context.resourceBase = '.'

    def localErrorHandler = new ErrorHandler()
    localErrorHandler.setShowStacks(true)
    context.setErrorHandler(localErrorHandler)
    context.resourceBase = '.'
    context.addServlet(GroovyServlet, '/health.groovy')
    context.addServlet(GroovyServlet, '/api/runQuery.groovy')
    context.addServlet(GroovyServlet, '/api/reloadOntology.groovy')
    context.addServlet(GroovyServlet, '/api/findRoot.groovy')
    context.addServlet(GroovyServlet, '/api/getObjectProperties.groovy')
    context.addServlet(GroovyServlet, '/api/retrieveRSuccessors.groovy')
    context.addServlet(GroovyServlet, '/api/retrieveAllLabels.groovy')
    context.addServlet(GroovyServlet, '/api/sparql.groovy')
    context.setAttribute('port', port)
    context.setAttribute('version', '0.2')
    server.start()
    println "Server started on " + server.getURI()
    def manager

    def tryAgain = false

    String ontId = ontologyFilePath.split("/")[-1]

    // IRI ontIRI = IRI.create(new File(ontologyFilePath))

    manager = RequestManager.create(ontId, ontologyFilePath)
    if (manager == null) {
        tryAgain = true
    }
    
    if (tryAgain) {
	manager = RequestManager.create(ontId, ontologyFilePath)
	if (manager == null){
	    println("Can't start " + ontId)
	}   
    }
    context.setAttribute("manager", manager)
}

def data = System.in.newReader().getText()
def slurper = new JsonSlurper()
// def ontology = slurper.parseText(data)
// println data
// def ontology = "data/pizza.owl"
def ontology = args[0]
startServer(ontology, 8000)
