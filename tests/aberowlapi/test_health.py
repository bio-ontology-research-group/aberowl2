
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

class TestHealth(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.BASE_URL = "http://localhost:8080"
        cls.manager = OntologyServerManager(os.path.abspath("data/pizza.owl"))
        # Run the server in a background thread
        # cls.server_thread = threading.Thread(target=cls.manager.run, daemon=True)
        # cls.server_thread.start()

        
    

        # Start server using gevent's Greenlet
        cls.server_greenlet = gevent.spawn(cls.manager.run)

        
        time.sleep(6)

    @classmethod
    def tearDownClass(cls):
        cls.server_greenlet.kill(block=True, timeout=2)  # Kill the greenlet

        # cls.server_thread.join()
        release_port(8080)

        
    def test_health_check(cls):
        response = requests.get(f"{cls.BASE_URL}/health.groovy")
        assert response.status_code == 200
        assert "ok" in str(response.content)


