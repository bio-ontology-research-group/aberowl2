from aberowlapi.server_manager import OntologyServerManager
from aberowlapi.util import release_port
import gevent
import gevent.monkey
gevent.monkey.patch_all()
## KEEP THE 'import requests' after the 'import gevent' and 'gevent.monkey.patch_all()'
from unittest import TestCase
import requests
import threading
import time

import os
# gevent.config.loop = "default"

class TestObjectProperties(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.BASE_URL = "http://localhost:8080"
        cls.manager = OntologyServerManager(os.path.abspath("data/pizza.owl"))
        cls.server_greenlet = gevent.spawn(cls.manager.run)
        time.sleep(10)

    @classmethod
    def tearDownClass(cls):
        cls.server_greenlet.kill(block=True, timeout=10)  # Increase timeout for cleanup
        
        # Make sure the server has time to shut down properly
        time.sleep(2)
        
        release_port(8080)

        
    def test_getObjectProperties(cls):
        response = requests.get(f"{cls.BASE_URL}/api/getObjectProperties.groovy")
        assert response.status_code == 200
        assert "ok" in str(response.content)


