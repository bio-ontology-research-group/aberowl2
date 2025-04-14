import os
import time
import unittest
import requests
from unittest import TestCase
from aberowlapi.virtuoso_manager import VirtuosoManager

class TestVirtuosoQueries(TestCase):
    
    @classmethod
    def setUpClass(cls):
        # Use a different HTTP port to avoid conflicts with other tests
        cls.HTTP_PORT = 8892
        cls.VIRTUOSO_PORT = 1113
        
        # Start Virtuoso server with the pizza ontology
        cls.manager = VirtuosoManager(
            os.path.abspath("data/pizza.owl"),
            port=cls.VIRTUOSO_PORT,
            http_port=cls.HTTP_PORT
        )
        cls.server_started = cls.manager.start_server()
        
        # Wait for server to be fully ready
        if cls.server_started:
            time.sleep(5)  # Give extra time for the server to initialize
            
        cls.SPARQL_ENDPOINT = f"http://localhost:{cls.HTTP_PORT}/sparql"
    
    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'manager'):
            cls.manager.stop_server()
    
    def setUp(self):
        if not self.server_started:
            self.skipTest("Virtuoso server failed to start")
    
    def test_get_all_classes(self):
        """Test that we can retrieve all classes from the pizza ontology."""
        query = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        
        SELECT DISTINCT ?class
        WHERE {
            ?class rdf:type owl:Class .
            FILTER(isIRI(?class))
            FILTER(STRSTARTS(STR(?class), "http://www.co-ode.org/ontologies/pizza/"))
        }
        """
        
        response = requests.get(
            self.SPARQL_ENDPOINT,
            params={
                'query': query,
                'format': 'application/sparql-results+json'
            }
        )
        
        self.assertEqual(response.status_code, 200, f"SPARQL query failed: {response.text}")
        
        results = response.json()
        self.assertIn('results', results, "No results in SPARQL response")
        self.assertIn('bindings', results['results'], "No bindings in SPARQL results")
        
        # Pizza ontology should have multiple classes
        self.assertGreater(len(results['results']['bindings']), 5, 
                          "Expected at least 5 classes in pizza ontology")
        
        # Check for some known pizza classes
        class_uris = [binding['class']['value'] for binding in results['results']['bindings']]
        pizza_class = "http://www.co-ode.org/ontologies/pizza/pizza.owl#Pizza"
        
        self.assertIn(pizza_class, class_uris, 
                     f"Pizza class not found in results: {class_uris[:10]}")
    
    def test_class_hierarchy(self):
        """Test that we can retrieve the class hierarchy from the pizza ontology."""
        query = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        
        SELECT DISTINCT ?class ?superClass
        WHERE {
            ?class rdfs:subClassOf ?superClass .
            ?class rdf:type owl:Class .
            ?superClass rdf:type owl:Class .
            FILTER(isIRI(?class) && isIRI(?superClass))
            FILTER(STRSTARTS(STR(?class), "http://www.co-ode.org/ontologies/pizza/"))
            FILTER(STRSTARTS(STR(?superClass), "http://www.co-ode.org/ontologies/pizza/"))
        }
        LIMIT 100
        """
        
        response = requests.get(
            self.SPARQL_ENDPOINT,
            params={
                'query': query,
                'format': 'application/sparql-results+json'
            }
        )
        
        self.assertEqual(response.status_code, 200, f"SPARQL query failed: {response.text}")
        
        results = response.json()
        self.assertIn('results', results, "No results in SPARQL response")
        self.assertIn('bindings', results['results'], "No bindings in SPARQL results")
        
        # Pizza ontology should have class hierarchy relationships
        self.assertGreater(len(results['results']['bindings']), 3, 
                          "Expected at least 3 class-superclass relationships")
        
        # Print some results for debugging
        if results['results']['bindings']:
            print(f"Found {len(results['results']['bindings'])} class hierarchy relationships")
            for binding in results['results']['bindings'][:5]:
                print(f"Class: {binding['class']['value']} -> SuperClass: {binding['superClass']['value']}")
