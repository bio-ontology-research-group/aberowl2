@Grapes([
    @Grab(group='org.elasticsearch.client', module='elasticsearch-rest-client', version='7.17.10'), // Updated version
    @Grab(group='org.elasticsearch.client', module='elasticsearch-rest-high-level-client', version='7.17.10'), // Updated version
    @Grab(group='org.semanticweb.elk', module='elk-owlapi', version='0.4.2'), // Keep for now
    @Grab(group='net.sourceforge.owlapi', module='owlapi-api', version='4.2.3'), // Keep for now
    @Grab(group='net.sourceforge.owlapi', module='owlapi-apibinding', version='4.2.3'), // Keep for now
    @Grab(group='net.sourceforge.owlapi', module='owlapi-impl', version='4.2.3'), // Keep for now
    @Grab(group='net.sourceforge.owlapi', module='owlapi-parsers', version='4.2.3'), // Keep for now
    @Grab(group='org.slf4j', module='slf4j-nop', version='1.7.25'),
    @Grab(group='ch.qos.reload4j', module='reload4j', version='1.2.18.5'),
    @Grab(group='org.apache.commons', module='commons-lang3', version='3.12.0'), // Added missing dependency
    @GrabExclude(group='log4j', module='log4j'),
])


import groovy.json.*

// Added import for StringEscapeUtils
import org.apache.commons.lang3.StringEscapeUtils

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
import org.elasticsearch.index.query.QueryBuilders // Use QueryBuilders static methods
import org.elasticsearch.common.unit.TimeValue;

import java.nio.*
import java.nio.file.*
import java.util.*
// import org.apache.logging.log4j.* // Removed dependency to log4j library if possible
import java.net.URL
import java.util.Base64 // Use java.util.Base64 for encoding

println "--- [DEBUG] IndexElastic.groovy script started ---"

urls = args[0].split(",")
username = args[1]
password = args[2]
ontologyIndexName = args[3]
owlClassIndexName = args[4]
fileName = args[5]
skip_embbedding = args[6]

println "[DEBUG] Args: urls=${urls}, user=${username ?: 'none'}, pass=${password ? '***' : 'none'}, ontologyIdx=${ontologyIndexName}, classIdx=${owlClassIndexName}, file=${fileName}, skipEmbed=${skip_embbedding}"

esUrls = new ArrayList<URL>();
hosts = new HttpHost[urls.length];
idx=0

for (String url:urls) {
	esUrl= new URL(url)
	hosts[idx] = new HttpHost(esUrl.getHost(), esUrl.getPort(), esUrl.getProtocol());
    println "[DEBUG] Parsed ES Host: ${hosts[idx]}"
	idx++;
}

RestHighLevelClient esClient = null // Define variable outside try
RestClientBuilder restClientBuilder = null
println "[DEBUG] Building Elasticsearch RestClientBuilder..."

try {
    if (!username.isEmpty() &&  !password.isEmpty()) {
        println "[DEBUG] Using username/password authentication for Elasticsearch."
        final CredentialsProvider credentialsProvider =
            new BasicCredentialsProvider();
        credentialsProvider.setCredentials(AuthScope.ANY,
            new UsernamePasswordCredentials(username, password));

        restClientBuilder = RestClient.builder(hosts) // Assign to restClientBuilder
            .setHttpClientConfigCallback(new RestClientBuilder.HttpClientConfigCallback() {
            @Override
            public HttpAsyncClientBuilder customizeHttpClient(
                    HttpAsyncClientBuilder httpClientBuilder) {
                return httpClientBuilder
                    .setDefaultCredentialsProvider(credentialsProvider);
            }
        });
    } else {
        println "[DEBUG] Using no authentication for Elasticsearch."
        // Handle potential case of multiple hosts without auth
        restClientBuilder = RestClient.builder(*hosts) // Use spread operator for varargs
    }

    println "[DEBUG] Creating RestHighLevelClient..."
    esClient = new RestHighLevelClient(restClientBuilder) // Pass builder to client constructor
    println "[DEBUG] RestHighLevelClient created successfully."
    // Optional: Test connection early? (ping might require specific privileges)
    // try { esClient.ping(RequestOptions.DEFAULT); println "[DEBUG] Elasticsearch ping successful." } catch (Exception pe) { println "[DEBUG] Elasticsearch ping failed: ${pe.message}" }

} catch (Exception e) {
    println "[FATAL] Failed to create Elasticsearch client:"
    e.printStackTrace()
    // Exit early if client creation fails
    System.exit(1)
}


def indexExists(indexName) {
    println "[DEBUG] Checking existence of index: ${indexName}"
	try {
		GetIndexRequest request = new GetIndexRequest(indexName);
        boolean exists = esClient.indices().exists(request, RequestOptions.DEFAULT);
        println "[DEBUG] Index '${indexName}' exists: ${exists}"
		return exists
	}  catch (Exception e) {
		println "[ERROR] Error checking index existence for ${indexName}:"
		e.printStackTrace();
		return false; // Assume doesn't exist on error? Or re-throw? For now, return false.
	}
}

def initIndex() {
    println "[DEBUG] Initializing indices..."
	def settings = [
	    "number_of_shards" : 1,
	    "number_of_replicas" : 0, // Use 0 replicas for single-node setup
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
            "deprecated": ["type": "boolean"], // Added deprecated field mapping
			"synonyms": ["type": "text"],
		]
		]
	]

    println "[DEBUG] Checking/creating ontology index: ${ontologyIndexName}"
    if (!indexExists(ontologyIndexName)) {
		createIndex(ontologyIndexName, ontologyIndexSettings);
    } else {
        println "[DEBUG] Index ${ontologyIndexName} already exists."
    }

    println "[DEBUG] Checking/creating class index: ${owlClassIndexName}"
	if (!indexExists(owlClassIndexName)) {
		createIndex(owlClassIndexName, classIndexSettings);
    } else {
        println "[DEBUG] Index ${owlClassIndexName} already exists."
    }
    println "[DEBUG] Index initialization finished."
}

def createIndex(indexName, settings) {
    println "[DEBUG] Attempting creation of index: ${indexName}"
	try {
		CreateIndexRequest request = new CreateIndexRequest(indexName);
		request.source(new JsonBuilder(settings).toString(), XContentType.JSON)
		CreateIndexResponse createIndexResponse = esClient.indices().create(request, RequestOptions.DEFAULT);
        if (createIndexResponse.isAcknowledged()) {
            println "[DEBUG] Index created successfully: ${indexName}"
        } else {
            // This is unusual, indicates a potential cluster issue or timeout
            println "[WARN] Index creation NOT acknowledged by Elasticsearch for: ${indexName}"
        }
	}  catch (Exception e) {
        // Log detailed error during creation
        println "[ERROR] Failed to create index ${indexName}:"
		e.printStackTrace();
        // Re-throw or exit? Re-throwing might be better for the main try-catch block
        throw e
	}
}

def deleteOntologyData(ontology) {
    println "[DEBUG] Deleting existing data for ontology '${ontology}'..."
	try {
        println "[DEBUG] Target indices for deletion based on existence: ${ontologyIndexName}, ${owlClassIndexName}"
        // Ensure indices exist before attempting deletion - might still fail if one exists but not the other
        List<String> targetIndices = []
        if (indexExists(ontologyIndexName)) targetIndices.add(ontologyIndexName)
        if (indexExists(owlClassIndexName)) targetIndices.add(owlClassIndexName)

        if (targetIndices.isEmpty()) {
             println "[DEBUG] No target indices found containing data for ontology '${ontology}'. Skipping deletion."
             return
        }
        println "[DEBUG] Deleting from indices: ${targetIndices}"

		DeleteByQueryRequest request = new DeleteByQueryRequest(targetIndices.toArray(new String[0])); // Pass existing indices
		request.setQuery(QueryBuilders.matchQuery("ontology", ontology)); // Use QueryBuilders factory method
        request.setConflicts("proceed"); // Continue even if there are version conflicts
		request.setTimeout(TimeValue.timeValueMinutes(10)); // Use TimeValue factory method
        request.setRefresh(true); // Refresh indices after delete

		def response = esClient.deleteByQuery(request, RequestOptions.DEFAULT);
		println "[DEBUG] Deletion response for ontology '${ontology}': total=${response.getTotalDocs()}, deleted=${response.getDeleted()}, took=${response.getTook()}"
        if (response.getBulkFailures().size() > 0) {
             println "[WARN] Deletion bulk failures reported: ${response.getBulkFailures()}"
        }
        if (response.getSearchFailures().size() > 0) {
            println "[WARN] Deletion search failures reported: ${response.getSearchFailures()}"
        }
	}  catch (Exception e) {
        println "[ERROR] Error deleting data for ontology '${ontology}':"
		e.printStackTrace();
        // Decide if this is fatal? Maybe just warn and continue indexing? Warn for now.
	}
}

def index(def indexName, def obj) {
    // println "[TRACE] Indexing document into ${indexName}: ${obj}" // Too verbose unless needed
	try {
		IndexRequest request = new IndexRequest(indexName)
		request.source(new JsonBuilder(obj).toString(), XContentType.JSON);
		esClient.index(request, RequestOptions.DEFAULT);
    } catch (Exception e) {
        println "[ERROR] Error indexing document into ${indexName}. Doc sample: ${obj.dump().take(200)}..." // Avoid printing huge objects
		e.printStackTrace()
        // Decide if fatal? Allow indexing to continue for other docs? Log and continue.
    }
}


void indexOntology(String fileName, def data) {
    println "[DEBUG] Starting ontology indexing process for file: ${fileName}"

    // Initialize index (create if not exists) - wrapped in try-catch in main block
    initIndex()

    def acronym = data.acronym
    println "[DEBUG] Processing ontology with acronym: ${acronym}"

    // Delete existing data for this ontology first
    deleteOntologyData(acronym)

    println "[DEBUG] Loading ontology using OWL API from file: ${fileName}..."
    OWLOntologyManager manager = OWLManager.createOWLOntologyManager()
    OWLOntology ont = null
    try {
        ont = manager.loadOntologyFromOntologyDocument(new File(fileName))
        println "[DEBUG] Ontology loaded successfully via OWL API."
    } catch (Exception e) {
        println "[FATAL] Failed to load ontology file ${fileName} using OWL API."
        e.printStackTrace()
        throw e // Re-throw to be caught by the main try-catch
    }

    OWLDataFactory fac = manager.getOWLDataFactory()
    def df = fac // Alias for brevity

    // Define annotation properties
    def identifiers = [ df.getOWLAnnotationProperty(IRI.create('http://purl.org/dc/elements/1.1/identifier')) ]
    def labels = [
	    df.getRDFSLabel(),
	    df.getOWLAnnotationProperty(IRI.create('http://www.w3.org/2004/02/skos/core#prefLabel')),
	    df.getOWLAnnotationProperty(IRI.create('http://purl.obolibrary.org/obo/IAO_0000111')) ]
    def synonyms = [
	    df.getOWLAnnotationProperty(IRI.create('http://www.w3.org/2004/02/skos/core#altLabel')),
	    df.getOWLAnnotationProperty(IRI.create('http://purl.obolibrary.org/obo/IAO_0000118')),
	    df.getOWLAnnotationProperty(IRI.create('http://www.geneontology.org/formats/oboInOwl#hasExactSynonym')),
	    df.getOWLAnnotationProperty(IRI.create('http://www.geneontology.org/formats/oboInOwl#hasSynonym')),
	    df.getOWLAnnotationProperty(IRI.create('http://www.geneontology.org/formats/oboInOwl#hasNarrowSynonym')),
	    df.getOWLAnnotationProperty(IRI.create('http://www.geneontology.org/formats/oboInOwl#hasBroadSynonym')) ]
    def definitions = [
	    df.getOWLAnnotationProperty(IRI.create('http://purl.obolibrary.org/obo/IAO_0000115')),
	    df.getOWLAnnotationProperty(IRI.create('http://www.w3.org/2004/02/skos/core#definition')),
	    df.getOWLAnnotationProperty(IRI.create('http://purl.org/dc/elements/1.1/description')),
	    df.getOWLAnnotationProperty(IRI.create('http://purl.org/dc/terms/description')) ]


    // --- Index Ontology Metadata ---
    println "[DEBUG] Preparing ontology metadata document for indexing..."
    def name = data.name
    def description = data.description
    def omap = [ ontology: acronym, name: name ]
    if (description) {
        // Ensure description is not null or empty before escaping
	    omap.description = StringEscapeUtils.escapeJava(description)
    } else {
         omap.description = "" // Index empty string if null/missing
    }

    println "[DEBUG] Indexing ontology metadata document into index '${ontologyIndexName}'..."
    try {
        index(ontologyIndexName, omap)
        println "[DEBUG] Ontology metadata indexed successfully."
    } catch (Exception e) {
         println "[ERROR] Failed to index ontology metadata for ${acronym}."
         // Decide if fatal? Maybe continue to index classes? Log and continue.
    }


    // --- Index Classes ---
    println "[DEBUG] Merging ontology imports closure..."
    OWLOntology mergedOntology = null // Use specific type
    try {
        OWLOntologyImportsClosureSetProvider mp = new OWLOntologyImportsClosureSetProvider(manager, ont)
        OWLOntologyMerger merger = new OWLOntologyMerger(mp) // Uses imports closure provider
        // Provide a unique IRI for the merged ontology
        mergedOntology = merger.createMergedOntology(manager, IRI.create("http://merged.owl/" + acronym + "/" + UUID.randomUUID()))
        println "[DEBUG] Ontology merge successful. Using merged ontology for class indexing."
    } catch (Exception e) {
        println "[WARN] Failed to merge ontology imports closure for ${acronym}. Reason: ${e.message}"
        // e.printStackTrace() // Maybe too verbose for a warning
        println "[WARN] Attempting to index classes from the main ontology document only..."
        mergedOntology = ont // Fall back to the non-merged ontology
    }


    int classCount = 0
    println "[DEBUG] Iterating through classes in signature (include imports closure: ${mergedOntology != ont})..."
    Set<OWLClass> classesToIndex = mergedOntology.getClassesInSignature(true) // Use imports closure = true
    println "[DEBUG] Found ${classesToIndex.size()} classes in signature (including imports)."

    classesToIndex.each { c -> // OWLClass
        // Skip owl:Thing and owl:Nothing explicitly
        if (c.isOWLThing() || c.isOWLNothing()) {
            // println "[TRACE] Skipping owl:Thing or owl:Nothing"
            return // groovy 'continue' equivalent in closures
        }

	    def cIRI = c.getIRI().toString()
        // println "[TRACE] Processing class: ${cIRI}"
	    def info = [
	        "owlClass": c.toString(), // e.g., <http://...>
	        "class": cIRI,
	        "ontology": acronym,
            "label": [], // Initialize lists explicitly
            "synonyms": [],
            "definition": [],
            "identifier": [],
	    ]

	    boolean hasLabel = false
	    boolean deprecated = false

        // Use EntitySearcher on the possibly merged ontology
	    EntitySearcher.getAnnotations(c, mergedOntology).each { annot ->
	        OWLAnnotationProperty aProp = annot.getProperty()
            OWLAnnotationValue value = annot.getValue()

            // Check for deprecated status using standard annotation or property characteristic
	        if (annot.isDeprecatedIRIAnnotation() || (aProp.isDeprecated() && value instanceof OWLLiteral && ((OWLLiteral)value).isBoolean() && ((OWLLiteral)value).parseBoolean())) {
		        deprecated = true
	        } else if (value instanceof OWLLiteral) { // Process only literal values for other annotations
                 def aVal = ((OWLLiteral)value).getLiteral()
                 // Avoid adding null or empty strings? Optional.
                 if (aVal != null && !aVal.isEmpty()) {
                     if (aProp in identifiers) { info["identifier"].add(aVal) }
                     else if (aProp in labels) { info["label"].add(aVal); hasLabel = true }
                     else if (aProp in definitions) { info["definition"].add(StringEscapeUtils.escapeJava(aVal)) } // Escape definitions
                     else if (aProp in synonyms) { info["synonyms"].add(aVal) }
                 }
            } // else: Ignore non-literal annotations for these properties
	    } // end annotation loop

	    info['deprecated'] = deprecated

        // Generate label from IRI fragment/path if no explicit label found
	    if (!hasLabel) {
            String generatedLabel = c.getIRI().getFragment()
            if (generatedLabel == null || generatedLabel.isEmpty()) {
                 List<String> pathSegments = c.getIRI().getPathSegments()
                 if (pathSegments != null && !pathSegments.isEmpty()) {
                     generatedLabel = pathSegments.get(pathSegments.size() - 1)
                 } else {
                     generatedLabel = cIRI // Fallback to full IRI
                 }
            }
	        info["label"].add(generatedLabel) // Add the generated label
            // println "[TRACE] No explicit label found for ${cIRI}. Using generated: ${generatedLabel}"
	    }

	    // Add embedding if available
	    if (data["embeds"] != null && data["embeds"].containsKey(cIRI)) {
		    info["embedding_vector"] = data["embeds"][cIRI];
            // println "[TRACE] Added embedding for ${cIRI}"
	    }

	    // Generate OBO-style ID
	    def oboId = generateOboId(cIRI)
	    if (oboId) { info["oboid"] = oboId }

        // Index this class info
        try {
            index(owlClassIndexName, info)
            classCount++
        } catch (Exception e) {
            println "[ERROR] Failed to index class: ${cIRI}"
            // Continue with next class
        }

        // Log progress periodically?
        if (classCount % 1000 == 0 && classCount > 0) {
            println "[DEBUG] Indexed ${classCount} classes so far..."
        }

	} // end class loop

	println "[DEBUG] Finished indexing ${classCount} classes for ontology: ${acronym}"
}

// Helper function for OBO ID generation
String generateOboId(String iri) {
    def oboId = ""
    int lastSeparator = -1
    // Find the last occurrence of '/', '#', or '?'
    lastSeparator = Math.max(iri.lastIndexOf('/'), iri.lastIndexOf('#'))
    lastSeparator = Math.max(lastSeparator, iri.lastIndexOf('?'))

    if (lastSeparator > -1 && lastSeparator < iri.length() - 1) {
        oboId = iri.substring(lastSeparator + 1)
        // Replace underscore with colon ONLY if it looks like a CURIE prefix might be present
        // This is heuristic - might need refinement based on actual IRI patterns
        if (oboId.matches("^[A-Za-z]+_\\d+$")) { // e.g., GO_0008150
            oboId = oboId.replaceFirst("_", ":") // GO:0008150
        } else {
             // If no underscore or doesn't match pattern, keep as is (e.g., just 'Pizza')
             // Or specifically handle cases? For now, only replace common OBO-style pattern.
        }
    }
    return oboId.length() > 0 ? oboId : null
}


String convertArrayToBase64(double[] array) {
    if (array == null || array.length == 0) return null
    final int capacity = Double.BYTES * array.length;
    final ByteBuffer bb = ByteBuffer.allocate(capacity);
    bb.asDoubleBuffer().put(array) // More efficient way to put doubles
    // Use java.util.Base64
    final byte[] encodedBytes = Base64.getEncoder().encode(bb.array()); // Use encode, not encodeToArray
    return new String(encodedBytes, java.nio.charset.StandardCharsets.ISO_8859_1);
}


// --- Main Execution Logic ---
def mainData = null
try {
    println "[DEBUG] Reading JSON configuration data from stdin..."
    String jsonData = System.in.newReader().getText()
    def slurper = new JsonSlurper()
    mainData = slurper.parseText(jsonData)
    println "[DEBUG] JSON data parsed. Acronym: ${mainData?.acronym}"
    if (mainData == null || mainData.acronym == null) {
         println "[FATAL] Failed to parse valid JSON data or missing 'acronym' from stdin."
         System.exit(1)
    }
} catch (Exception e) {
    println "[FATAL] Error reading or parsing JSON data from stdin:"
    e.printStackTrace()
    System.exit(1)
}


// Load Embeddings (optional)
if (skip_embbedding == null || skip_embbedding.equalsIgnoreCase("False")) {
    println "[DEBUG] Attempting to load embeddings (skip_embedding=${skip_embbedding})..."
	def embeds = [:]
    File embeddingFile = new File(fileName + ".embs")
    if (embeddingFile.exists()) {
        println "[DEBUG] Found embedding file: ${embeddingFile.getAbsolutePath()}"
        int lineNum = 0
	    embeddingFile.splitEachLine(" ") { List<String> lineParts ->
            lineNum++
            if (lineParts == null || lineParts.size() < 2) {
                println "[WARN] Skipping malformed line ${lineNum} in embedding file: ${lineParts}"
                return // groovy continue
            }
            String iri = lineParts[0]
		    double[] vector = new double[lineParts.size() - 1]
		    try {
                for (int i = 1; i < lineParts.size(); ++i) {
		            vector[i - 1] = Double.parseDouble(lineParts[i]);
		        }
                embeds[iri] = convertArrayToBase64(vector);
            } catch (NumberFormatException nfe) {
                 println "[WARN] Skipping embedding line ${lineNum} (IRI: ${iri}) due to number format error."
            } catch (Exception e) {
                 println "[WARN] Skipping embedding line ${lineNum} (IRI: ${iri}) due to unexpected error: ${e.message}"
            }
	    }
        println "[DEBUG] Loaded ${embeds.size()} embeddings from file."
	    mainData["embeds"] = embeds
    } else {
        println "[DEBUG] Embedding file not found: ${embeddingFile.getAbsolutePath()}. No embeddings will be indexed."
    }
} else {
    println "[DEBUG] Skipping embedding loading as requested."
}

// --- Run the main indexing logic ---
try {
    indexOntology(fileName, mainData)
} catch (Exception e) {
    println "[FATAL] Uncaught exception during main ontology indexing routine."
    e.printStackTrace()
    // Signal failure
    System.exit(1)
} finally {
    println "[DEBUG] Closing Elasticsearch client..."
    try {
        if (esClient != null) { // Check if client was successfully created
            esClient.close()
            println "[DEBUG] Elasticsearch client closed."
        } else {
            println "[DEBUG] Elasticsearch client was null, nothing to close."
        }
    } catch (IOException ioe) {
        println "[ERROR] Error closing Elasticsearch client:"
        ioe.printStackTrace()
    } catch (Exception e) {
        println "[ERROR] Unexpected error closing Elasticsearch client:"
         e.printStackTrace()
    }
}

println "--- [DEBUG] IndexElastic.groovy script finished successfully ---"
// Ensure script exits with 0 on success
System.exit(0)

