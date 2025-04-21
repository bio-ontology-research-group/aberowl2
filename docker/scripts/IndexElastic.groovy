@Grapes([
    @Grab(group='org.elasticsearch.client', module='elasticsearch-rest-client', version='7.3.1'),
    @Grab(group='org.elasticsearch.client', module='elasticsearch-rest-high-level-client', version='7.3.1'),
    @Grab(group='org.semanticweb.elk', module='elk-owlapi', version='0.4.2'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-api', version='4.2.3'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-apibinding', version='4.2.3'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-impl', version='4.2.3'),
    @Grab(group='net.sourceforge.owlapi', module='owlapi-parsers', version='4.2.3'),
    @Grab(group='org.slf4j', module='slf4j-nop', version='1.7.25'),
    @Grab(group='ch.qos.reload4j', module='reload4j', version='1.2.18.5'),
    @Grab('org.apache.commons:commons-lang3:3.12.0'), // Added for StringEscapeUtils
    @GrabExclude(group='log4j', module='log4j'),
])


import groovy.json.*

import org.apache.http.auth.AuthScope
import org.apache.http.auth.UsernamePasswordCredentials
import org.apache.http.impl.client.BasicCredentialsProvider
import org.apache.http.impl.nio.client.HttpAsyncClientBuilder;
import org.apache.http.client.CredentialsProvider
import org.apache.http.HttpHost
import org.apache.http.client.methods.*
import org.apache.http.entity.*
import org.apache.http.impl.client.*

import org.semanticweb.elk.owlapi.ElkReasonerFactory;
import org.semanticweb.elk.owlapi.ElkReasonerConfiguration
import org.semanticweb.elk.reasoner.config.*
import org.semanticweb.owlapi.apibinding.OWLManager;
import org.semanticweb.owlapi.reasoner.*
import org.semanticweb.owlapi.reasoner.structural.StructuralReasoner
import org.semanticweb.owlapi.vocab.OWLRDFVocabulary;
import org.semanticweb.owlapi.model.*;
import org.semanticweb.owlapi.io.*;
import org.semanticweb.owlapi.owllink.*;
import org.semanticweb.owlapi.util.*;
import org.semanticweb.owlapi.search.*;
import org.semanticweb.owlapi.manchestersyntax.renderer.*;
import org.semanticweb.owlapi.reasoner.structural.*

import org.elasticsearch.client.indices.*
import org.elasticsearch.action.index.IndexRequest
import org.elasticsearch.common.xcontent.XContentType;
import org.elasticsearch.client.RestClientBuilder
import org.elasticsearch.client.RestClient
import org.elasticsearch.client.RequestOptions
import org.elasticsearch.client.RestHighLevelClient
import org.elasticsearch.index.reindex.DeleteByQueryRequest
import org.elasticsearch.index.query.MatchQueryBuilder
import org.elasticsearch.common.unit.TimeValue;

import java.nio.*
import java.nio.file.*
import java.util.*
import org.apache.logging.log4j.*
import java.net.URL
import org.apache.commons.lang3.StringEscapeUtils // Added import
import java.util.Base64 // Added import

urls = args[0].split(",")
username = args[1]
password = args[2]
ontologyIndexName = args[3]
owlClassIndexName = args[4]
fileName = args[5]
skip_embbedding = args[6]

esUrls = new ArrayList<URL>();
hosts = new HttpHost[urls.length];
idx=0

for (String url:urls) {
	esUrl= new URL(url)
	hosts[idx] = new HttpHost(esUrl.getHost(), esUrl.getPort(), esUrl.getProtocol());
	idx++;
}

// Print debug info about connection
println "Connecting to Elasticsearch at: ${urls.join(', ')}"
println "Using indices: ontology=${ontologyIndexName}, class=${owlClassIndexName}"
println "Processing ontology file: ${fileName}"

restClient = null

if (!username.isEmpty() &&  !password.isEmpty()) {
	println "Using authentication with username: ${username}"
	final CredentialsProvider credentialsProvider =
		new BasicCredentialsProvider();
	credentialsProvider.setCredentials(AuthScope.ANY,
		new UsernamePasswordCredentials(username, password));

	restClient = RestClient.builder(hosts)
		.setHttpClientConfigCallback(new RestClientBuilder.HttpClientConfigCallback() {
        @Override
        public HttpAsyncClientBuilder customizeHttpClient(
                HttpAsyncClientBuilder httpClientBuilder) {
            return httpClientBuilder
                .setDefaultCredentialsProvider(credentialsProvider);
        }
    });
} else {
	println "No authentication provided, connecting without credentials"
	// Original code incorrectly used 'esUrl' which would only use the last URL from the loop
	// Corrected to use 'hosts' array which contains all specified hosts
	restClient = RestClient.builder(hosts)
}

try {
    esClient = new RestHighLevelClient(restClient)
    // Test connection
    def pingResponse = esClient.ping(RequestOptions.DEFAULT)
    println "Connected to Elasticsearch: ${pingResponse}"
} catch (Exception e) {
    println "ERROR: Failed to connect to Elasticsearch: ${e.message}"
    e.printStackTrace()
    System.exit(1)
}

def indexExists(indexName) {
	try {
		GetIndexRequest request = new GetIndexRequest(indexName);
		return  esClient.indices().exists(request, RequestOptions.DEFAULT);
	}  catch (Exception e) {
		e.printStackTrace();
		return false;
	}
}

def initIndex() {
	def settings = [
	    "number_of_shards" : 1,
	    "number_of_replicas" : 1,
	    "analysis": [
			"normalizer": [
				"aberowl_normalizer": [
				"type": "custom",
				"filter": ["lowercase",]
				]
			]
	    ]
	]

    def ontologyIndexSettings = [
		"settings" : settings,
		"mappings":[
		"properties" : [
			"name": [
			"type": "keyword", "normalizer": "aberowl_normalizer"],
			"ontology": [
			"type": "keyword", "normalizer": "aberowl_normalizer"],
			"description": ["type": "text"],
		]
		]
    ]

	def classIndexSettings = [
		"settings" : settings,
		"mappings":[
		"properties" : [
			"embedding_vector": [
				"type": "binary",
				"doc_values": true
			],
			"class": ["type": "keyword"],
			"definition": ["type": "text"],
			"identifier": ["type": "keyword"],
			"label": [
			"type": "keyword", "normalizer": "aberowl_normalizer"],
			"ontology": [
			"type": "keyword", "normalizer": "aberowl_normalizer"],
			"oboid": [
			"type": "keyword", "normalizer": "aberowl_normalizer"],
			"owlClass": ["type": "keyword"],
			"synonyms": ["type": "text"],
		]
		]
	]

    if (!indexExists(ontologyIndexName)) {
		createIndex(ontologyIndexName, ontologyIndexSettings);
    }

	if (!indexExists(owlClassIndexName)) {
		createIndex(owlClassIndexName, classIndexSettings);
    }
}

def createIndex(indexName, settings) {
	try {
		CreateIndexRequest request = new CreateIndexRequest(indexName);
		request.source(new JsonBuilder(settings).toString(), XContentType.JSON)
		CreateIndexResponse createIndexResponse = esClient.indices().create(request, RequestOptions.DEFAULT);
		println('Index created :' + indexName)
	}  catch (Exception e) {
		println "ERROR: Failed to create index ${indexName}: ${e.message}"
		e.printStackTrace();
	}
}

def deleteOntologyData(ontology) {
	try {
		DeleteByQueryRequest request = new DeleteByQueryRequest(ontologyIndexName, owlClassIndexName);
		request.setQuery(new MatchQueryBuilder("ontology", ontology));
		request.setTimeout(new TimeValue(10 * 60000));
		response = esClient.deleteByQuery(request, RequestOptions.DEFAULT);
		println("total=" + response.total + "|deletedDocs=" + response.deleted + "|searchRetries="
			+ response.searchRetries + "|bulkRetries=" + response.bulkRetries)
	}  catch (Exception e) {
		e.printStackTrace();
	}
}

def index(def indexName, def obj) {
	try {
		request = new IndexRequest(indexName)
		request.source(new JsonBuilder(obj).toString(), XContentType.JSON);
		esClient.index(request, RequestOptions.DEFAULT);
    } catch (Exception e) {
		e.printStackTrace()
    }
}


void indexOntology(String fileName, def data) {
    println "Starting to index ontology from file: ${fileName}"
    
    // Initialize index
    try {
        initIndex()
        println "Initialized Elasticsearch indices"
    } catch (Exception e) {
        println "ERROR: Failed to initialize indices: ${e.message}"
        e.printStackTrace()
        System.exit(1)
    }

    try {
        println "Loading ontology from: ${fileName}"
        File ontologyFile = new File(fileName)
        if (!ontologyFile.exists()) {
            println "ERROR: Ontology file does not exist: ${fileName}"
            System.exit(1)
        }
        
        OWLOntologyManager manager = OWLManager.createOWLOntologyManager()
        OWLOntology ont = manager.loadOntologyFromOntologyDocument(ontologyFile)
        println "Successfully loaded ontology: ${ont.getOntologyID()}"
    OWLDataFactory fac = manager.getOWLDataFactory()
    ConsoleProgressMonitor progressMonitor = new ConsoleProgressMonitor()
    OWLReasonerConfiguration config = new SimpleConfiguration(progressMonitor)
    ElkReasonerFactory f1 = new ElkReasonerFactory()
    OWLReasoner reasoner = f1.createReasoner(ont, config)
    def oReasoner = reasoner
    def df = fac

    def identifiers = [
	df.getOWLAnnotationProperty(new IRI('http://purl.org/dc/elements/1.1/identifier')),
    ]

    def labels = [
	df.getRDFSLabel(),
	df.getOWLAnnotationProperty(new IRI('http://www.w3.org/2004/02/skos/core#prefLabel')),
	df.getOWLAnnotationProperty(new IRI('http://purl.obolibrary.org/obo/IAO_0000111'))
    ]
    def synonyms = [
	df.getOWLAnnotationProperty(new IRI('http://www.w3.org/2004/02/skos/core#altLabel')),
	df.getOWLAnnotationProperty(new IRI('http://purl.obolibrary.org/obo/IAO_0000118')),
	df.getOWLAnnotationProperty(new IRI('http://www.geneontology.org/formats/oboInOwl#hasExactSynonym')),
	df.getOWLAnnotationProperty(new IRI('http://www.geneontology.org/formats/oboInOwl#hasSynonym')),
	df.getOWLAnnotationProperty(new IRI('http://www.geneontology.org/formats/oboInOwl#hasNarrowSynonym')),
	df.getOWLAnnotationProperty(new IRI('http://www.geneontology.org/formats/oboInOwl#hasBroadSynonym'))
    ]
    def definitions = [
	df.getOWLAnnotationProperty(new IRI('http://purl.obolibrary.org/obo/IAO_0000115')),
	df.getOWLAnnotationProperty(new IRI('http://www.w3.org/2004/02/skos/core#definition')),
	df.getOWLAnnotationProperty(new IRI('http://purl.org/dc/elements/1.1/description')),
	df.getOWLAnnotationProperty(new IRI('http://purl.org/dc/terms/description')),
	df.getOWLAnnotationProperty(new IRI('http://www.geneontology.org/formats/oboInOwl#hasDefinition'))
    ]


    def acronym = data.acronym
    def name = data.name
    def description = data.description

    def omap = [:]
    omap.ontology = acronym
    omap.name = name
    if (description) {
	// Use StringEscapeUtils here
	omap.description = StringEscapeUtils.escapeJson(description) // Use escapeJson for JSON context
    }

    // Delete ontology data
    deleteOntologyData(acronym)

    index(ontologyIndexName, omap)

    // Re-add all classes for this ont

    OWLOntologyImportsClosureSetProvider mp = new OWLOntologyImportsClosureSetProvider(manager, ont)
    OWLOntologyMerger merger = new OWLOntologyMerger(mp, false)
    def iOnt = merger.createMergedOntology(manager, IRI.create("http://test.owl"))

    iOnt.getClassesInSignature(true).each {
	c -> // OWLClass
	def cIRI = c.getIRI().toString()
	def info = [
	    "owlClass": c.toString(),
	    "class": cIRI,
	    "ontology": acronym,
	].withDefault { key -> [] };

	def hasLabel = false
	def deprecated = false;

	EntitySearcher.getAnnotations(c, iOnt).each { annot ->
	    def aProp = annot.getProperty()
	    if (annot.isDeprecatedIRIAnnotation()) {
		deprecated = true
	    } else
		if (aProp in identifiers) {
			if (annot.getValue() instanceof OWLLiteral) {
				def aVal = annot.getValue().getLiteral()
				info["identifier"] << aVal
			}
	    } else if (aProp in labels) {
			if (annot.getValue() instanceof OWLLiteral) {
				def aVal = annot.getValue().getLiteral()
				info["label"] << aVal
				hasLabel = true
			}
	    } else if (aProp in definitions) {
			if (annot.getValue() instanceof OWLLiteral) {
				def aVal = annot.getValue().getLiteral()
				// Use StringEscapeUtils here
				info["definition"] << StringEscapeUtils.escapeJson(aVal) // Use escapeJson for JSON context
			}
	    } else if (aProp in synonyms) {
			if (annot.getValue() instanceof OWLLiteral) {
				def aVal = annot.getValue().getLiteral()
				info["synonyms"] << aVal
			}
	    }
	}
	// Original commented out block start: if (!deprecated) {

	info['deprecated'] = deprecated // Store deprecated status
	if (!hasLabel) {
	    info["label"] << c.getIRI().getFragment().toString()
	}

	// Add an embedding to the document
	if (data["embeds"] != null && data["embeds"].containsKey(cIRI)) {
		info["embedding_vector"] = data["embeds"][cIRI];
	}

	// generate OBO-style ID for the index
	def oboId = ""
	if (cIRI.lastIndexOf('?') > -1) {
	    oboId = cIRI.substring(cIRI.lastIndexOf('?') + 1)
	} else if (cIRI.lastIndexOf('#') > -1) {
	    oboId = cIRI.substring(cIRI.lastIndexOf('#') + 1)
	} else if (cIRI.lastIndexOf('/') > -1) {
	    oboId = cIRI.substring(cIRI.lastIndexOf('/') + 1)
	}
	if (oboId.length() > 0) {
	    oboId = oboId.replaceAll("_", ":")
	    info["oboid"] = oboId
	}


	// Index the class info (Consider uncommenting the check if needed)
	// if (!deprecated) {
	    index(owlClassIndexName, info)
	// } // Original commented out block end

    } // End of classes loop

	println('Finished indexing :' + acronym)
    } // End of try block inside indexOntology method
} // End of indexOntology method

String convertArrayToBase64(double[] array) {
    final int capacity = 8 * array.length;
    final ByteBuffer bb = ByteBuffer.allocate(capacity);
    for (int i = 0; i < array.length; i++) {
	bb.putDouble(array[i]);
    }
    bb.rewind();
    final ByteBuffer encodedBB = Base64.getEncoder().encode(bb);
    return new String(encodedBB.array());
}

try {
    println "Reading data from standard input..."
    def dataText = System.in.newReader().getText()
    
    if (dataText == null || dataText.isEmpty()) {
        println "ERROR: No data received from standard input"
        System.exit(1)
    }
    
    println "Parsing JSON data..."
    def slurper = new JsonSlurper()
    def data = slurper.parseText(dataText)
    
    if (data == null) {
        println "ERROR: Failed to parse JSON data"
        System.exit(1)
    }
    
    println "Successfully parsed JSON data with keys: ${data.keySet()}"
    
    println "Checking for embeddings (skip_embedding=${skip_embbedding})..."
    if (skip_embbedding.equals("False")) {
        // Read embeddings
        def embeds = [:]
        def embeddingFile = new File(fileName + ".embs")
        println "Looking for embedding file: ${embeddingFile.path}"
        if (embeddingFile.exists()) {
            println "Reading embeddings from file: ${embeddingFile.path}"
            int lineCount = 0
            int successCount = 0
            embeddingFile.splitEachLine(" ") { it ->
                lineCount++
                if (it.size() > 1) { // Ensure there's at least a key and one value
                    double[] vector = new double[it.size() - 1]
                    try {
                        for (int i = 1; i < it.size(); ++i) {
                            vector[i - 1] = Double.parseDouble(it[i]);
                        }
                        embeds[it[0]] = convertArrayToBase64(vector);
                        successCount++
                    } catch (NumberFormatException e) {
                        println "WARN: Could not parse embedding vector line: ${it.join(' ')} - ${e.message}"
                    }
                } else {
                    println "WARN: Skipping malformed embedding line: ${it.join(' ')}"
                }
            }
            println "Processed ${lineCount} embedding lines, successfully loaded ${successCount} embeddings"
            data["embeds"] = embeds
        } else {
            println "WARN: Embedding file not found: ${embeddingFile.path}"
        }
    } else {
        println "Skipping embeddings as requested"
    }

    println "Starting indexing process..."
    indexOntology(fileName, data)
    
    println "Closing Elasticsearch client..."
    esClient.close()
    println "Elasticsearch client closed. Indexing completed successfully."
} catch (Exception e) {
    println "ERROR: Failed during indexing process: ${e.message}"
    e.printStackTrace()
    if (esClient != null) {
        try {
            esClient.close()
        } catch (Exception closeError) {
            println "ERROR: Failed to close Elasticsearch client: ${closeError.message}"
        }
    }
    System.exit(1)
}
