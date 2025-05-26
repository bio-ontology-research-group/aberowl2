import logging
from SPARQLWrapper import SPARQLWrapper, JSON

logger = logging.getLogger(__name__)

class SPARQLClient:
    """Client for executing SPARQL queries against a SPARQL endpoint."""
    
    def __init__(self, endpoint_url):
        """Initialize the SPARQL client.
        
        Args:
            endpoint_url: URL of the SPARQL endpoint
        """
        self.endpoint_url = endpoint_url
        self.sparql = SPARQLWrapper(endpoint_url)
        self.sparql.setReturnFormat(JSON)
    
    def query(self, query_string):
        """Execute a SPARQL query.
        
        Args:
            query_string: SPARQL query string
            
        Returns:
            Query results as a dictionary
        """
        self.sparql.setQuery(query_string)
        try:
            results = self.sparql.query().convert()
            return results
        except Exception as e:
            logger.error(f"SPARQL query failed: {e}")
            return {"error": str(e)}
    
    def get_classes(self):
        """Get all classes in the ontology."""
        query = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        
        SELECT DISTINCT ?class ?label
        WHERE {
            { ?class rdf:type owl:Class }
            OPTIONAL { ?class rdfs:label ?label }
        }
        ORDER BY ?class
        """
        return self.query(query)
    
    def get_properties(self):
        """Get all properties in the ontology."""
        query = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        
        SELECT DISTINCT ?property ?label
        WHERE {
            { ?property rdf:type owl:ObjectProperty }
            UNION
            { ?property rdf:type owl:DatatypeProperty }
            OPTIONAL { ?property rdfs:label ?label }
        }
        ORDER BY ?property
        """
        return self.query(query)
    
    def get_individuals(self):
        """Get all individuals in the ontology."""
        query = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        
        SELECT DISTINCT ?individual ?label
        WHERE {
            { ?individual rdf:type owl:NamedIndividual }
            OPTIONAL { ?individual rdfs:label ?label }
        }
        ORDER BY ?individual
        """
        return self.query(query)
    
    def get_class_hierarchy(self):
        """Get the class hierarchy in the ontology."""
        query = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        
        SELECT DISTINCT ?class ?superClass
        WHERE {
            ?class rdfs:subClassOf ?superClass .
            ?class rdf:type owl:Class .
            ?superClass rdf:type owl:Class .
        }
        ORDER BY ?class
        """
        return self.query(query)
