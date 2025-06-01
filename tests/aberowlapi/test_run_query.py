from aberowlapi.server_manager import OntologyServerManager
from aberowlapi.util import release_port
from unittest import TestCase
import requests
import threading
import time
import os

class TestRunQuery(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.BASE_URL = "http://localhost:88/api"
                        
    def test_run_query(cls):
        query = "cheesypizza"
        type_ = "superclass"
        direct = "false"
        labels = "true"
        axiom = "false"
        response = requests.get(f"{cls.BASE_URL}/api/runQuery.groovy", params={"query": query, "type": type_, "direct": direct, "labels": labels, "axiom": axiom})
        print(response.content)
        cls.assertEqual(response.status_code, 200)
        cls.assertIn("pizza", str(response.content))

