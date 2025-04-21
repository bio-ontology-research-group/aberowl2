import json
import os
import signal
import logging
import time
import gevent # Make sure gevent is used for non-blocking IO
from gevent.subprocess import Popen, PIPE

# Load configuration (previously Django settings)
ABEROWL_SERVER_URL = os.getenv('ABEROWL_SERVER_URL', 'http://localhost/')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
        if not os.path.exists(self.ontology):
             logging.error(f"Ontology file path provided does not exist: {self.ontology}")
             # Consider exiting or raising an error here if the file must exist beforehand
        
    def stop_subprocesses(self, signum, frame):
        logging.info(f"Received signal {signum}. Stopping Groovy subprocess.")
        if self.proc and self.proc.poll() is None:
            try:
                # Attempt graceful termination first
                self.proc.terminate() 
                # Wait a short period
                try:
                     self.proc.wait(timeout=5) 
                     logging.info("Groovy subprocess terminated gracefully.")
                except gevent.Timeout:
                     logging.warning("Groovy subprocess did not terminate gracefully after 5s, killing.")
                     self.proc.kill() # Force kill if terminate didn't work
            except Exception as e:
                 logging.error(f"Error during subprocess termination: {e}. Killing.")
                 if self.proc and self.proc.poll() is None:
                       self.proc.kill() # Ensure kill if terminate logic failed
            
        logging.info("Ontology server manager stopped.")
        exit(0)

    def run(self):
        """Starts the API server for a single ontology."""
        logging.info(f"Preparing to start Groovy OntologyServer for: {self.ontology}")
        
        env = os.environ.copy()
        # Ensure JAVA_OPTS are set, provide smaller default if needed for testing
        env['JAVA_OPTS'] = os.getenv('JAVA_OPTS', '-Xmx2g -Xms512m') 
        logging.info(f"Using JAVA_OPTS: {env['JAVA_OPTS']}")

        # Command arguments
        cmd = ['groovy', 'OntologyServer.groovy', self.ontology]
        cwd = 'aberowlapi' # Set working directory for groovy process

        logging.info(f"Executing command: {' '.join(cmd)} in directory: {cwd}")

        try:
            self.proc = Popen(
                cmd,
                cwd=cwd, 
                # stdin=PIPE, # No longer needed as groovy script doesn't read stdin
                stdout=PIPE,
                stderr=PIPE, # Capture stderr as well
                universal_newlines=True, # Decode stdout/stderr as text
                env=env
            )
        except FileNotFoundError:
             logging.error(f"Error: 'groovy' command not found. Is Groovy installed and in the PATH?")
             return # Cannot proceed
        except Exception as e:
             logging.error(f"Error starting Groovy process: {e}")
             return # Cannot proceed


        # We don't need to close stdin as we didn't open it with PIPE
        # self.proc.stdin.close() 

        logging.info(f"Groovy process started (PID: {self.proc.pid}). Monitoring output...")

        # Monitor stdout and stderr using gevent select or similar non-blocking approach
        # This simple loop reads line by line, which can still block 
        # if the groovy process writes a lot without newlines or hangs internally.
        # A more robust solution might use gevent.select or separate greenlets for stdout/stderr.
        
        # Read stdout
        if self.proc.stdout:
             for line in self.proc.stdout:
                 line = line.strip()
                 if line: # Avoid logging empty lines
                    logging.info(f"[Groovy STDOUT] {line}")
    
                    # Example checks (adjust based on actual Groovy output)
                    if 'Finished loading' in line: # Adapt if log message changes
                        try:
                             oid = line.split()[-1] # Assuming last word is ID
                             if oid not in self.loaded:
                                 self.loaded.add(oid)
                                 logging.info(f"Detected loading finished for ontology: {oid}")
                        except IndexError:
                             logging.warning(f"Could not parse ontology ID from stdout line: {line}")

                    if 'Unloadable ontology' in line: # Adapt if log message changes
                         try:
                             oid = line.split()[-1]
                             logging.error(f"Detected unloadable ontology: {oid}")
                         except IndexError:
                              logging.warning(f"Could not parse ontology ID from stderr line: {line}")
                    
                    if 'Server started successfully' in line:
                         logging.info("Detected Jetty server started within Groovy process.")

        # After stdout finishes (process likely ended), capture remaining stderr
        stderr_output = ""
        if self.proc.stderr:
            stderr_output = self.proc.stderr.read().strip()
            if stderr_output:
                 logging.error(f"[Groovy STDERR] {stderr_output}")


        # Wait for process completion and get exit code
        return_code = self.proc.wait() 
        logging.info(f"Groovy process (PID: {self.proc.pid}) finished with exit code: {return_code}")
        
        if return_code != 0 and not stderr_output:
             logging.error("Groovy process exited non-zero without clear error on stderr. Check STDOUT logs above.")
        elif return_code != 0:
             logging.error("Groovy process exited non-zero. Check STDERR log above.")


if __name__ == "__main__":
    # Example of direct execution (not typical for container)
    # Requires ontology path as argument
    import sys
    if len(sys.argv) > 1:
        ont_path_arg = sys.argv[1]
        logging.info(f"Running server_manager directly with ontology: {ont_path_arg}")
        manager = OntologyServerManager(ont_path_arg)
        manager.run()
    else:
        print("Usage: python aberowlapi/server_manager.py <path_to_ontology_file>")
        # Provide a default for local testing if desired
        # default_ont = '../data/pizza.owl' # Adjust path as needed
        # if os.path.exists(default_ont):
        #      logging.info(f"No ontology path provided, using default: {default_ont}")
        #      manager = OntologyServerManager(default_ont)
        #      manager.run()
        # else:
        #      print(f"Default ontology {default_ont} not found.")

