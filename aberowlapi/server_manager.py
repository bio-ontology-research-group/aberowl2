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
        # Check path existence *inside* the run method, after Popen is setup,
        # as the path is relative to the CWD of the Popen call.
        # However, the path passed to __init__ is absolute within the container /data/...
        # So check it here.
        if not os.path.exists(self.ontology):
             logging.error(f"Ontology file path provided to ServerManager does not exist: {self.ontology}")
             # Should ideally raise an exception or prevent run()
             # raise FileNotFoundError(f"Ontology file not found: {self.ontology}")

    def stop_subprocesses(self, signum, frame):
        logging.info(f"Received signal {signum}. Stopping Groovy subprocess.")
        if self.proc and self.proc.poll() is None:
            try:
                 # Flush streams before terminating
                 if self.proc.stdout: self.proc.stdout.flush()
                 if self.proc.stderr: self.proc.stderr.flush()

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
                 # Ensure kill if terminate logic failed or process already dead
                 if self.proc and self.proc.poll() is None:
                       self.proc.kill()

        logging.info("Ontology server manager stopped.")
        # Use os._exit for immediate exit within signal handler if needed
        os._exit(0) # Use os._exit in signal handler to avoid potential issues with exit()

    def run(self):
        """Starts the API server for a single ontology."""
        logging.info(f"Preparing to start Groovy OntologyServer for: {self.ontology}")
        if not os.path.exists(self.ontology):
             logging.error(f"Ontology file path does not exist inside container: {self.ontology}. Aborting run.")
             return # Prevent Popen if file is missing

        env = os.environ.copy()
        # Ensure JAVA_OPTS are set, provide smaller default if needed for testing
        env['JAVA_OPTS'] = os.getenv('JAVA_OPTS', '-Xmx2g -Xms512m')
        logging.info(f"Using JAVA_OPTS: {env['JAVA_OPTS']}")

        # Command arguments - Added '-d' for debug output
        cmd = ['groovy', '-d', 'OntologyServer.groovy', self.ontology]
        cwd = 'aberowlapi' # Set working directory for groovy process

        logging.info(f"Executing command: {' '.join(cmd)} in directory: {cwd}")

        try:
            # Using gevent's Popen
            self.proc = Popen(
                cmd,
                cwd=cwd,
                # stdin=PIPE, # No longer needed
                stdout=PIPE,
                stderr=PIPE, # Capture stderr as well
                universal_newlines=True, # Decode stdout/stderr as text
                env=env,
                bufsize=1 # Line buffered
            )
        except FileNotFoundError:
             logging.error(f"Error: 'groovy' command not found. Is Groovy installed and in the PATH?")
             return # Cannot proceed
        except Exception as e:
             logging.error(f"Error starting Groovy process: {e}", exc_info=True) # Log traceback
             return # Cannot proceed


        logging.info(f"Groovy process started (PID: {self.proc.pid}). Monitoring output...")

        # --- Non-blocking output reading using gevent ---
        # Create greenlets to read stdout and stderr concurrently
        stdout_reader = gevent.spawn(self._read_stream, self.proc.stdout, "[Groovy STDOUT]")
        stderr_reader = gevent.spawn(self._read_stream, self.proc.stderr, "[Groovy STDERR]")

        # Wait for both readers to finish (which happens when the streams close)
        gevent.joinall([stdout_reader, stderr_reader], raise_error=True)

        # Wait for process completion and get exit code
        return_code = self.proc.wait()
        logging.info(f"Groovy process (PID: {self.proc.pid}) finished with exit code: {return_code}")

        if return_code != 0:
                logging.error(f"Groovy process exited non-zero ({return_code}). Check logs above for errors.")


    def _read_stream(self, stream, prefix):
        """Reads lines from a stream and logs them with a prefix."""
        if stream is None:
             logging.warning(f"{prefix} stream is None, cannot read.")
             return
        try:
            for line in stream:
                line = line.strip()
                if line: # Avoid logging empty lines
                    # Determine log level based on prefix (crude)
                    if "STDERR" in prefix or "xception" in line or "rror" in line:
                        logging.error(f"{prefix} {line}")
                    else:
                        logging.info(f"{prefix} {line}")

                    # --- Re-add specific checks if needed, based on Groovy output ---
                    if prefix == "[Groovy STDOUT]":
                        if 'Finished loading' in line:
                            # ... (parsing logic)
                             logging.info(f"Detected loading finished message: {line}")
                        elif 'Server started successfully' in line:
                             logging.info("Detected Jetty server started message.")
                        elif 'Initial RequestManager creation successful' in line:
                              logging.info("Detected RequestManager created message.")

                    if prefix == "[Groovy STDERR]":
                          if 'Unloadable ontology' in line:
                               # ... (parsing logic)
                               logging.error(f"Detected unloadable ontology message: {line}")

        except Exception as e:
            logging.error(f"Error reading {prefix} stream: {e}", exc_info=True)
        finally:
            logging.info(f"{prefix} stream reading finished.")


if __name__ == "__main__":
    # Example of direct execution (not typical for container)
    # Requires ontology path as argument
    import sys
    if len(sys.argv) > 1:
        ont_path_arg = sys.argv[1]
        logging.info(f"Running server_manager directly with ontology: {ont_path_arg}")
        manager = OntologyServerManager(ont_path_arg)
        try:
             manager.run()
        except Exception as e:
             logging.exception("Unhandled exception during direct execution.") # Log full traceback
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

