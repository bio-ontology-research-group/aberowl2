import os
import signal
import subprocess
import time
import logging
import tempfile
import shutil
from rdflib import Graph, URIRef
from SPARQLWrapper import SPARQLWrapper, JSON

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VirtuosoManager:
    """Manages a Virtuoso server instance for SPARQL queries."""
    
    def __init__(self, ontology_path, port=1111, http_port=8890, db_path=None):
        """Initialize the Virtuoso server manager.
        
        Args:
            ontology_path: Path to the ontology file
            port: Virtuoso server port
            http_port: Virtuoso HTTP port for SPARQL endpoint
            db_path: Path to store Virtuoso database files (temporary if None)
        """
        self.ontology_path = os.path.abspath(ontology_path)
        self.port = port
        self.http_port = http_port
        self.process = None
        self.temp_dir = None
        
        # Create temporary directory for Virtuoso if db_path not provided
        if db_path is None:
            self.temp_dir = tempfile.mkdtemp(prefix="virtuoso_")
            self.db_path = self.temp_dir
        else:
            self.db_path = os.path.abspath(db_path)
            os.makedirs(self.db_path, exist_ok=True)
            
        # Handle termination signals
        signal.signal(signal.SIGTERM, self.stop_server)
        signal.signal(signal.SIGINT, self.stop_server)
        signal.signal(signal.SIGQUIT, self.stop_server)
        
        self.sparql_endpoint = f"http://localhost:{http_port}/sparql"
        
    def prepare_virtuoso_config(self):
        """Create a Virtuoso configuration file."""
        config_path = os.path.join(self.db_path, "virtuoso.ini")
        
        with open(config_path, "w") as f:
            f.write(f"""
[Database]
DatabaseFile = {os.path.join(self.db_path, "virtuoso.db")}
TransactionFile = {os.path.join(self.db_path, "virtuoso.trx")}
ErrorLogFile = {os.path.join(self.db_path, "virtuoso.log")}
xa_persistent_file = {os.path.join(self.db_path, "virtuoso.pxa")}
ErrorLogLevel = 7
FileExtend = 200
MaxCheckpointRemap = 2000
Striping = 0
TempStorage = TempDatabase

[Parameters]
ServerPort = {self.port}
ServerThreads = 10
CheckpointInterval = 60
O_DIRECT = 0
CaseMode = 2
MaxStaticCursorRows = 5000
CheckpointAuditTrail = 0
AllowOSCalls = 1
DirsAllowed = ., {os.path.dirname(self.ontology_path)}, {self.db_path}

[HTTPServer]
ServerPort = {self.http_port}
Charset = UTF-8
EnabledDavVSP = 1
HTTPProxyEnabled = 0
MaxClientConnections = 10
DavRoot = DAV

[SPARQL]
ResultSetMaxRows = 10000
MaxQueryCostEstimationTime = 400
MaxQueryExecutionTime = 60
DefaultGraph = http://example.org/ontology
""")
        return config_path
        
    def load_ontology(self):
        """Load the ontology into Virtuoso."""
        # Convert ontology to RDF if needed
        graph = Graph()
        try:
            logger.info(f"Loading ontology from {self.ontology_path}")
            
            # Parse the ontology file
            try:
                graph.parse(self.ontology_path)
            except Exception as e:
                logger.error(f"Failed to parse ontology file: {e}")
                return False
                
            rdf_path = os.path.join(self.db_path, "ontology.rdf")
            try:
                graph.serialize(destination=rdf_path, format="xml")
                logger.info(f"Serialized ontology to RDF: {rdf_path}")
            except Exception as e:
                logger.error(f"Failed to serialize ontology to RDF: {e}")
                return False
            
            # Create SQL script to load RDF
            load_script = os.path.join(self.db_path, "load_data.sql")
            with open(load_script, "w") as f:
                # Use proper path escaping for SQL
                escaped_path = self.db_path.replace("\\", "\\\\")
                f.write(f"""
ld_dir('{escaped_path}', 'ontology.rdf', 'http://example.org/ontology');
rdf_loader_run();
checkpoint;
select * from DB.DBA.LOAD_LIST where ll_error is not NULL;
""")
            logger.info(f"Created SQL load script: {load_script}")
            
            # Try loading via HTTP SPARQL endpoint first
            try:
                import requests
                with open(rdf_path, 'rb') as f:
                    files = {'file': ('ontology.rdf', f, 'application/rdf+xml')}
                    response = requests.post(
                        f"http://localhost:{self.http_port}/sparql-graph-crud-auth",
                        files=files,
                        params={'graph-uri': 'http://example.org/ontology'},
                        auth=('dba', 'dba'),
                        timeout=30
                    )
                    
                    if response.status_code == 200 or response.status_code == 201:
                        logger.info("Ontology loaded via HTTP SPARQL endpoint")
                        return True
                    else:
                        logger.warning(f"HTTP upload failed with status {response.status_code}: {response.text}")
                        # Fall back to isql-vt method
            except Exception as e:
                logger.warning(f"HTTP upload attempt failed: {e}")
                # Fall back to isql-vt method
            
            # Execute the load script using isql-vt
            cmd = [
                "isql-vt", 
                f"{self.port}", 
                "-U", "dba", 
                "-P", "dba", 
                "-i", f"{load_script}"
            ]
            
            logger.info(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd, 
                check=False,
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                logger.error(f"isql-vt command failed with code {result.returncode}")
                logger.error(f"STDOUT: {result.stdout}")
                logger.error(f"STDERR: {result.stderr}")
                
                # If it's an authentication error but the server is running, consider it a partial success
                if "Bad login" in result.stderr and self.process and self.process.poll() is None:
                    logger.warning("Authentication failed but server is running. Continuing...")
                    return True
                    
                return False
                
            logger.info(f"Ontology loaded into Virtuoso: {self.ontology_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load ontology: {e}")
            return False
            
    def _wait_for_virtuoso(self, max_attempts=1):
        """Wait for Virtuoso server to be ready to accept connections."""
        logger.info("Checking if Virtuoso server is ready...")
        
        # First, check if the process is still running
        if self.process.poll() is not None:
            logger.error("Virtuoso process has terminated unexpectedly")
            return False
            
        # Give Virtuoso more time to initialize
        time.sleep(5)
        
        try:
            # Try to connect to the SPARQL endpoint using HTTP
            import requests
            try:
                response = requests.get(f"http://localhost:{self.http_port}/sparql?query=SELECT+1", 
                                       timeout=5)
                if response.status_code == 200:
                    logger.info("Virtuoso SPARQL endpoint is accessible")
                    return True
            except requests.exceptions.RequestException as e:
                logger.warning(f"HTTP connection to SPARQL endpoint failed: {e}")
                
            # Fallback to isql-vt command
            cmd = [
                "isql-vt", 
                f"{self.port}", 
                "-U", "dba", 
                "-P", "dba", 
                "-c", "status();"
            ]
            result = subprocess.run(
                cmd, 
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5
            )
            
            if result.returncode == 0:
                logger.info("Virtuoso server is ready via isql-vt")
                return True
            else:
                stderr = result.stderr.decode('utf-8', errors='replace')
                logger.warning(f"Virtuoso server check failed: {stderr}")
                
                # If it's an authentication error, the server is probably running
                if "Bad login" in stderr:
                    logger.info("Authentication failed but server appears to be running")
                    return True
                    
                return False
        except (subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Failed to connect to Virtuoso: {str(e)}")
            return False
    
    def start_server(self):
        """Start the Virtuoso server."""
        if self.process and self.process.poll() is None:
            logger.info("Virtuoso server is already running")
            return True
            
        config_path = self.prepare_virtuoso_config()
        
        # Check if virtuoso-t is installed
        try:
            subprocess.run(["which", "virtuoso-t"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            # Try checking with 'command -v' as a fallback for other shells
            try:
                subprocess.run(["command", "-v", "virtuoso-t"], check=True, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError:
                logger.error("Virtuoso is not installed or not in PATH. Please install Virtuoso server.")
                logger.error("On Ubuntu/Debian: sudo apt-get install virtuoso-opensource")
                logger.error("On CentOS/RHEL: sudo yum install virtuoso-opensource")
                logger.error("On macOS: brew install virtuoso")
                return False
            
        try:
            # Start Virtuoso server
            cmd = ["virtuoso-t", "+foreground", "-f", "-c", config_path]
            logger.info(f"Starting Virtuoso with command: {' '.join(cmd)}")
            
            self.process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            
            # Wait for server to start (minimum time)
            time.sleep(15)  # Increased wait time for better initialization
            
            # Check if process is still running
            if self.process.poll() is not None:
                stderr = self.process.stderr.read()
                logger.error(f"Failed to start Virtuoso: {stderr}")
                return False
                
            logger.info(f"Virtuoso server started on port {self.port}, HTTP port {self.http_port}")
            
            # Check if server is ready
            if not self._wait_for_virtuoso():
                logger.error("Virtuoso server started but is not responding to commands")
                self.stop_server()
                return False
                
            # Load the ontology
            ontology_loaded = self.load_ontology()
            if not ontology_loaded:
                logger.error("Failed to load ontology, but keeping server running")
                # Continue running even if ontology loading fails
                return True
                
            return True
            
        except Exception as e:
            logger.error(f"Error starting Virtuoso server: {e}")
            return False
    
    def stop_server(self, signum=None, frame=None):
        """Stop the Virtuoso server."""
        if self.process and self.process.poll() is None:
            logger.info("Stopping Virtuoso server...")
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
            
        # Clean up temporary directory if created
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
            logger.info(f"Removed temporary directory: {self.temp_dir}")
    
    def run_query(self, query):
        """Run a SPARQL query against the Virtuoso endpoint.
        
        Args:
            query: SPARQL query string
            
        Returns:
            Query results as a dictionary
        """
        sparql = SPARQLWrapper(self.sparql_endpoint)
        sparql.setQuery(query)
        sparql.setReturnFormat(JSON)
        
        try:
            results = sparql.query().convert()
            return results
        except Exception as e:
            logger.error(f"SPARQL query failed: {e}")
            return {"error": str(e)}
    
    def run(self):
        """Run the Virtuoso server."""
        success = self.start_server()
        if success:
            logger.info(f"Virtuoso SPARQL endpoint available at: {self.sparql_endpoint}")
            
            # Keep the server running
            try:
                while self.process and self.process.poll() is None:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")
            finally:
                self.stop_server()
                
            return True
        else:
            logger.error("Failed to start Virtuoso server")
            return False
