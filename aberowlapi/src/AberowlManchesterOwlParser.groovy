package src

import org.semanticweb.owlapi.manchestersyntax.parser.ManchesterOWLSyntaxParserImpl
import org.semanticweb.owlapi.model.OWLClassExpression
import org.semanticweb.owlapi.model.OWLDataFactory
import org.semanticweb.owlapi.model.OWLOntology
import org.semanticweb.owlapi.util.ShortFormProvider

import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class AberowlManchesterOwlParser {

    private static final String VALUE_FRAME_REGEX =
        "(VALUES|values)[\\s]+\\?[A-Za-z0-9]+[\\s]+\\{[\\s]*(OWL|owl){1}[\\s]*(superclass|subclass|equivalent|supeq|subeq|realize){1}[\\s]*\\<.*\\>[\\s]+\\<.*\\>[\\s]+\\{[\\s]+[\\d\\w\\\'\\\"\\<\\>:._~\\/\\-\\?\\#\\[\\]\\@\\!\\\$\\&\\(\\)\\*\\+\\,\\;\\=\\s]+[\\s]+\\}[\\s]*[\\.]*[\\s]*\\}[\\s]*[\\.]*";
    private static final String FILTER_FRAME_REGEX =
        "(FILTER|filter)[\\r\\n\\s]+[\\(]{1}[\\r\\n\\s]+\\?[A-Za-z0-9]+[\\s]+in+[\\s]+\\([\\r\\n\\s]*(OWL|owl){1}[\\s]*(superclass|subclass|equivalent|supeq|subeq|realize){1}[\\s]*\\<.+\\>[\\s]+[\\r\\n\\s]*\\<.*\\>[\\s]+[\\r\\n\\s]*\\{[\\r\\n\\s]+.+[\\r\\n\\s]+\\}[\\r\\n\\s]*\\)[\\r\\n\\s]*\\)[\\s]*[\\.]*";

    private static final String QUERY_REGEX =
	"(OWL|owl){1}[\\s]*(superclass|subclass|equivalent|supeq|subeq|realize){1}[\\s]*\\<([^>]*)>[\\s]+[\\r\\n\\s]*\\<([^>]*)\\>[\\s]+[\\r\\n\\s]*\\{[\\r\\n\\s]*([\\w\\s\']+)[\\r\\n\\s]*\\}[\\s]*[\\.]*"

    // "(OWL|owl){1}[\\s]*(superclass|subclass|equivalent|supeq|subeq|realize){1}[\\s]*\\<([\\w]+:\\/\\/[\\w\\.\\/]+)>[\\s]+[\\r\\n\\s]*\\<([\\w-]*)\\>[\\s]+[\\r\\n\\s]*\\{[\\r\\n\\s]+([\\w\\s\']+)[\\r\\n\\s]+\\}[\\s]*[\\.]*";

    private static final String VARIABLE_REGEX = "\\?[A-Za-z0-9]+"

    private final ManchesterOWLSyntaxParserImpl delegate;

    public AberowlManchesterOwlParser(OWLOntology ontology, ShortFormProvider shortFormProvider) {
        OWLDataFactory dataFactory = ontology.getOWLOntologyManager().getOWLDataFactory();
        this.delegate = new ManchesterOWLSyntaxParserImpl(dataFactory, shortFormProvider);
        delegate.setDefaultOntology(ontology);
    }

    public OWLClassExpression parse(final String manchesterOwlQuery) {
        return delegate.parse(manchesterOwlQuery);
    }

    public AberowlManchesterOwlQuery parseForSparql(final String manchesterOwlQuery) {
        Pattern pattern = Pattern.compile(QUERY_REGEX);
        Matcher matcher = pattern.matcher(manchesterOwlQuery);
        if (matcher.find()) {
            AberowlManchesterOwlQuery query = new AberowlManchesterOwlQuery();
            query.setQueryType(matcher.group(2));
            query.setSparqlServiceUrl(matcher.group(3));
            query.setOntologyIri(matcher.group(4));
            query.setQuery(matcher.group(5));
            def var = manchesterOwlQuery.findAll(VARIABLE_REGEX);
            if (var.size() > 0) {
                query.setVariable(var[0]);
            }

            return query;
        }
        return null;
    }

    public AberowlManchesterOwlQuery parseSparql(final String sparqlQuery) {
        String manchesterOwlQuery = matchManchesterOwlValueFrame(sparqlQuery);
        if (!manchesterOwlQuery.trim().isEmpty()) {
            def query = parseForSparql(manchesterOwlQuery);
            query.setInValueFrame(true);
            return query;
        } else {
            manchesterOwlQuery = matchManchesterOwlFilterFrame(sparqlQuery); 
            if (!manchesterOwlQuery.trim().isEmpty()) {
                return parseForSparql(manchesterOwlQuery);
	    }	    
            return null;
        }
    }
    

    public String matchManchesterOwlValueFrame(final String sparql) {
        if (sparql == null) {
            throw new RuntimeException("SPARQL query is null");
        }

        
        Pattern pattern = Pattern.compile(VALUE_FRAME_REGEX);
        Matcher matcher = pattern.matcher(sparql);
        if (matcher.find()) {
            return matcher.group(0);
        }

        
	// If no match is found, return an empty string
	//
	return "";
    }

    public String matchManchesterOwlFilterFrame(final String sparql) {
        if (sparql == null) {
            throw new RuntimeException("SPARQL query is null");
        }

        Pattern pattern = Pattern.compile(FILTER_FRAME_REGEX);
        Matcher matcher = pattern.matcher(sparql);
        if (matcher.find()) {
            return matcher.group(0);
        }
        return "";
    }

    public String replaceAberowlManchesterOwlFrame(final String sparql, final String replaceWith) {
        if (sparql == null) {
            throw new RuntimeException("SPARQL query is null");
        }
        if (replaceWith.trim().isEmpty()) {
            return sparql.replaceAll(QUERY_REGEX, "''");
        }
        return sparql.replaceAll(QUERY_REGEX, replaceWith);
    }

    public String removeAberowlManchesterOwlFrame(String sparql) {
        if (sparql == null) {
            throw new RuntimeException("SPARQL query is null");
        }

        sparql = sparql.replaceAll(VALUE_FRAME_REGEX, "").replaceAll(FILTER_FRAME_REGEX, "");
        def manchesterOwlVar = findNonRepeating(sparql.findAll(VARIABLE_REGEX));
        return manchesterOwlVar != null ? sparql.replace(manchesterOwlVar, "") : sparql;
    }

    private def findNonRepeating(def arr) { 
        for (int i = 0; i < arr.size(); i++) { 
            int j;
            for (j = 0; j < arr.size(); j++) 
                if (i != j && arr[i] == arr[j]) 
                    break; 
            if (j == arr.size()) 
                return arr[i]; 
        } 
  
        return null; 
    } 
    
}
