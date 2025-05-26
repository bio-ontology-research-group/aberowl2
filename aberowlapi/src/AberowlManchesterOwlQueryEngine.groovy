package src

import org.eclipse.rdf4j.repository.sparql.SPARQLRepository;
import org.eclipse.rdf4j.http.client.SPARQLProtocolSession;
import org.eclipse.rdf4j.query.QueryLanguage;
import org.eclipse.rdf4j.repository.RepositoryConnection;
import org.eclipse.rdf4j.query.TupleQueryResultHandler;
import org.eclipse.rdf4j.query.resultio.sparqljson.SPARQLResultsJSONWriter;
import org.eclipse.rdf4j.query.TupleQuery;

import java.io.ByteArrayOutputStream;

/**
 * Aberowl manchester owl query engine process the query and retrieve 
 * results from a Sparql endpoint provided in query itself. Query processing
 * includes parsing the query to extract aberowl manchester owl query elements given
 * in the query, then retrieving classes using the element from ontologies loaded in
 * aberowl repository. Later, rewriting the given sparql query after replacing list of
 * class uris with the aberowl mancherter owl query snippet in sparql query. 
 *
 * Lastly, running the sparql query on extracted sparql endpoint. 
 **/
public class AberowlManchesterOwlQueryEngine {

    public def expandAndExecQuery(def manager, def sparql) {
        AberowlManchesterOwlParser parser = new AberowlManchesterOwlParser();
        AberowlManchesterOwlQuery query = parser.parseSparql(sparql);
        if (query != null){
	    def classes = this.executeQuery(manager, query);
            def queryString;

	    if (classes != null && classes.size() > 0) { 
		def commaJoinedClassesIriList;
		if (query.isInValueFrame()) {
		    commaJoinedClassesIriList = this.toClassIRIString(classes, " ")
		} else {
		    commaJoinedClassesIriList = this.toClassIRIString(classes, ", ")
		}
		queryString = parser.replaceAberowlManchesterOwlFrame(sparql, commaJoinedClassesIriList);
	    } else {
		// queryString = parser.removeAberowlManchesterOwlFrame(sparql),;
		queryString = parser.replaceAberowlManchesterOwlFrame(sparql, '');
	    }

	    return ["query": queryString, "endpoint": query.sparqlServiceUrl];
	}else {
            return ["query": sparql, "endpoint": ""];
            // return ["query": sparql, "endpoint": query.sparqlServiceUrl];
        
	}
	// return executeSparql(query, queryString);
    }

    // private def executeSparql(AberowlManchesterOwlQuery query, String sparql) {
    //     SPARQLRepository repo = new SPARQLRepository(query.sparqlServiceUrl);
    //     repo.initialize();
    //     RepositoryConnection conn;
    //     try {
    //         conn = repo.getConnection();
    //         TupleQuery tupleQuery = conn.prepareTupleQuery(QueryLanguage.SPARQL, sparql);
    //         def out = new ByteArrayOutputStream();
    //         TupleQueryResultHandler jsonWriter = new SPARQLResultsJSONWriter(out);
	// 		tupleQuery.evaluate(jsonWriter);
    //         return out.toString("UTF-8")
    //     } finally {
    //         conn.close();
    //     }
    // }

    private def executeQuery(def manager, AberowlManchesterOwlQuery query) {
        def out = manager.runQuery(query.query, query.queryType, true, true, false)
	return out;
        // if (query.getOntologyIri() != null && !query.getOntologyIri().isEmpty() && manager != null) {
            // def out = manager.runQuery(query.query, query.queryType, true, true, false);
            // return out;
        // } else {
            // def res = []
            // GParsPool.withPool {
                // managers.values().eachParallel { manager ->
                    // def out = manager.runQuery(query.query, query.queryType, true, true, false)
                    // res.addAll(out)
                // }
            // }
            // return res;
        // }
    }

    private def toClassIRIString(def classes, def delimiter) {
        return classes.collect{it.owlClass}.join(delimiter);
    }
}
