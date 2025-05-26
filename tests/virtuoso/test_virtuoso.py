import os
import time
import unittest
import gevent
from unittest import TestCase
from SPARQLWrapper import SPARQLWrapper, JSON

class TestVirtuoso(TestCase):
    @classmethod
    def setUpClass(cls):
        # Use a different HTTP port to avoid conflicts with other tests
        cls.BASE_URL = "http://localhost:88/virtuoso"
        
    
    def test_sparql_endpoint(cls):
        sparql = SPARQLWrapper(cls.BASE_URL)
        sparql.setReturnFormat(JSON)


        query = """
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        PREFIX owl: <http://www.w3.org/2002/07/owl#>
        SELECT DISTINCT ?class
        WHERE {
        ?class rdf:type owl:Class .
        }
        ORDER BY ?class
        """
        sparql.setQuery(query)
        results = sparql.query().convert()

        classes = []
        for result in results["results"]["bindings"]:
            classes.append(result["class"]["value"])

        test_class = "http://www.co-ode.org/ontologies/pizza/pizza.owl#Cajun"
        cls.assertIn(test_class, classes)
    
