from aberowlapi.server_manager import OntologyServerManager
from aberowlapi.util import release_port
import gevent
gevent.monkey.patch_all()
## KEEP THE 'import requests' after the 'import gevent' and 'gevent.monkey.patch_all()'
from unittest import TestCase
import requests
import threading
import time

import os
# gevent.config.loop = "default"

class TestSparql(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.BASE_URL = "http://localhost:88/api"
                        
    def test_sparql(cls):
        query = """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
	PREFIX owl: <http://www.w3.org/2002/07/owl#> 
        SELECT DISTINCT ?class  
	WHERE { ?class rdf:type owl:Class . } 
	ORDER BY ?class 
        """
        response = requests.get(f"{cls.BASE_URL}/api/sparql.groovy", params={"query": query})
        cls.assertEqual(response.status_code, 200)
        cls.assertIn("Country", str(response.content))

class TestSubclass(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.BASE_URL = "http://localhost:88/api"
                        
    def test_sparql(cls):
        query = """PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
	PREFIX owl: <http://www.w3.org/2002/07/owl#> 
        SELECT DISTINCT ?class  
	WHERE {
        VALUES ?class { OWL superclass <> <> { cheesypizza } } .
        } 
	ORDER BY ?class 
        """
        response = requests.get(f"{cls.BASE_URL}/api/sparql.groovy", params={"query": query})
        cls.assertEqual(response.status_code, 200)
        cls.assertIn("Pizza", str(response.content))


        
    
