import os
import time
import unittest
import gevent
from unittest import TestCase
from aberowlapi.virtuoso_manager import VirtuosoManager
from aberowlapi.sparql_util import SPARQLClient

class TestVirtuoso(TestCase):
    
    @classmethod
    def setUpClass(cls):
        # Use a different HTTP port to avoid conflicts with other tests
        cls.HTTP_PORT = 8891
        cls.VIRTUOSO_PORT = 1112
        
        # Start Virtuoso server with the pizza ontology
        cls.manager = VirtuosoManager(
            os.path.abspath("data/pizza.owl"),
            port=cls.VIRTUOSO_PORT,
            http_port=cls.HTTP_PORT
        )
        cls.server_greenlet = gevent.spawn(cls.manager.run)
        
        # Wait for server to start
        time.sleep(10)
        
        # Initialize SPARQL client
        cls.sparql_endpoint = f"http://localhost:{cls.HTTP_PORT}/sparql"
        cls.client = SPARQLClient(cls.sparql_endpoint)
    
    @classmethod
    def tearDownClass(cls):
        # Stop the Virtuoso server
        if cls.manager:
            cls.manager.stop_server()
        if cls.server_greenlet:
            cls.server_greenlet.kill()
    
    def test_sparql_endpoint(self):
        """Test that the SPARQL endpoint is accessible."""
        # Simple query to check if the endpoint is working
        query = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        SELECT ?s ?p ?o WHERE {
            ?s ?p ?o
        } LIMIT 5
        """
        results = self.client.query(query)
        
        # Check that we got results
        self.assertIn('results', results)
        self.assertIn('bindings', results['results'])
        self.assertTrue(len(results['results']['bindings']) > 0)
    
    def test_get_classes(self):
        """Test retrieving classes from the ontology."""
        results = self.client.get_classes()
        
        # Check that we got results
        self.assertIn('results', results)
        self.assertIn('bindings', results['results'])
        self.assertTrue(len(results['results']['bindings']) > 0)
        
        # Check for pizza-related classes in the results
        class_uris = [binding['class']['value'] for binding in results['results']['bindings']]
        pizza_classes = [uri for uri in class_uris if 'pizza' in uri.lower()]
        self.assertTrue(len(pizza_classes) > 0, "No pizza classes found in the ontology")
    
    def test_get_properties(self):
        """Test retrieving properties from the ontology."""
        results = self.client.get_properties()
        
        # Check that we got results
        self.assertIn('results', results)
        self.assertIn('bindings', results['results'])
        
        # Properties might be empty in some ontologies, so just check the structure
        self.assertIsInstance(results['results']['bindings'], list)
    
    def test_custom_query(self):
        """Test executing a custom SPARQL query."""
        # Query to find pizza toppings
        query = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        
        SELECT ?topping ?label
        WHERE {
            ?topping rdfs:subClassOf* <http://www.co-ode.org/ontologies/pizza/pizza.owl#PizzaTopping> .
            OPTIONAL { ?topping rdfs:label ?label }
        }
        LIMIT 10
        """
        results = self.client.query(query)
        
        # Check that we got results
        self.assertIn('results', results)
        self.assertIn('bindings', results['results'])
