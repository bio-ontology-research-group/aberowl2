import json
import os
import signal
import logging
import time
from gevent.subprocess import Popen, PIPE

# Load configuration (previously Django settings)
ABEROWL_SERVER_URL = os.getenv('ABEROWL_SERVER_URL', 'http://localhost/')

logging.basicConfig(level=logging.INFO)

class OntologyServerManager:
    def __init__(self, ontology):
        self.processes = {}
        self.loaded = set()
        self.proc = None

        # Handle termination signals
        signal.signal(signal.SIGTERM, self.stop_subprocesses)
        signal.signal(signal.SIGINT, self.stop_subprocesses)
        signal.signal(signal.SIGQUIT, self.stop_subprocesses)

        self.ontology = ontology
        
    def stop_subprocesses(self, signum, frame):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        logging.info("Ontology server stopped.")
        exit(0)

    def run(self):
        """Starts the API server for a single ontology."""
        # data = dict()
        # for ont in self.ontologies:
        # ontIRI = ABEROWL_SERVER_URL + self.ontology
        # data.update({'ontId': self.ontology["acronym"], 'ontIRI': ontIRI})

        # data = json.dumps(data)

        env = os.environ.copy()
        env['JAVA_OPTS'] = '-Xmx128g -Xms8g -XX:+UseParallelGC'

        self.proc = Popen(
            ['groovy', 'OntologyServer.groovy', self.ontology],
            cwd='aberowlapi/', stdin=PIPE, stdout=PIPE,
            universal_newlines=True, env=env
        )

        # self.proc.stdin.write(data)
        self.proc.stdin.close()

        for line in self.proc.stdout:
            line = line.strip()
            logging.info(line)

            if line.startswith('Finished loading'):
                oid = line.split()[2]
                if oid not in self.loaded:
                    self.loaded.add(oid)
                    logging.info(f"Ontology {oid} successfully loaded.")

            if line.startswith('Unloadable ontology'):
                oid = line.split()[2]
                logging.error(f"Ontology {oid} is unloadable.")

        self.proc.stdout.close()
        self.proc.wait()

if __name__ == "__main__":
    manager = OntologyServerManager()
    manager.run()
