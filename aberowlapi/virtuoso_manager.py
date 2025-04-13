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
DirsAllowed = ., {os.path.dirname(self.ontology_path)}

[HTTPServer]
ServerPort = {self.http_port}
Charset = UTF-8
""")
        return config_path
        
    def load_ontology(self):
        """Load the ontology into Virtuoso."""
        # Convert ontology to RDF if needed
        graph = Graph()
        try:
            graph.parse(self.ontology_path)
            rdf_path = os.path.join(self.db_path, "ontology.rdf")
            graph.serialize(destination=rdf_path, format="xml")
            
            # Create SQL script to load RDF
            load_script = os.path.join(self.db_path, "load_data.sql")
            with open(load_script, "w") as f:
                f.write(f"""
ld_dir('{self.db_path}', 'ontology.rdf', 'http://example.org/ontology');
rdf_loader_run();
checkpoint;
""")
            
            # Execute the load script
            cmd = [
                "isql", 
                f"{self.port}", 
                "-U", "dba", 
                "-P", "dba", 
                f"{load_script}"
            ]
            subprocess.run(cmd, check=True)
            logger.info(f"Ontology loaded into Virtuoso: {self.ontology_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load ontology: {e}")
            return False
    
    def start_server(self):
        """Start the Virtuoso server."""
        if self.process and self.process.poll() is None:
            logger.info("Virtuoso server is already running")
            return
            
        config_path = self.prepare_virtuoso_config()
        
        # Check if virtuoso-t is installed
        try:
            subprocess.run(["which", "virtuoso-t"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            logger.error("Virtuoso is not installed or not in PATH. Please install Virtuoso server.")
            logger.error("On Ubuntu/Debian: sudo apt-get install virtuoso-opensource")
            logger.error("On CentOS/RHEL: sudo yum install virtuoso-opensource")
            logger.error("On macOS: brew install virtuoso")
            return False
            
        try:
            # Start Virtuoso server
            cmd = ["virtuoso-t", "+foreground", "-f", "-c", config_path]
            self.process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Wait for server to start
            time.sleep(5)
            if self.process.poll() is not None:
                stderr = self.process.stderr.read().decode('utf-8')
                logger.error(f"Failed to start Virtuoso: {stderr}")
                return False
                
            logger.info(f"Virtuoso server started on port {self.port}, HTTP port {self.http_port}")
            
            # Load the ontology
            return self.load_ontology()
            
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
                while self.process.poll() is None:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            finally:
                self.stop_server()
        else:
            logger.error("Failed to start Virtuoso server")
