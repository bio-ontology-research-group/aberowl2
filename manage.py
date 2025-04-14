import os
import click
import uvicorn
from aberowlapi.server_manager import OntologyServerManager
from aberowlapi.virtuoso_manager import VirtuosoManager
from aberowlapi.util import release_port
@click.group()
def cli():
    """CLI tool for managing ontology API."""
    pass

@cli.command()
@click.option("--ontology", "-o", help="Ontology path")
def runontapi(ontology):
    """Starts the ontology API server."""
    abs_path = os.path.abspath(ontology)
    assert os.path.exists(abs_path), f"Ontology file not found: {abs_path}"
    manager = OntologyServerManager(abs_path)
    release_port(8080)
    manager.run()

@cli.command()
@click.option("--ontology", "-o", required=True, help="Ontology path")
@click.option("--port", "-p", default=1111, help="Virtuoso server port")
@click.option("--http-port", "-h", default=8890, help="Virtuoso HTTP port for SPARQL endpoint")
@click.option("--db-path", "-d", default=None, help="Path to store Virtuoso database files")
def runvirtuoso(ontology, port, http_port, db_path):
    """Starts a Virtuoso server for SPARQL queries with the given ontology."""
    abs_path = os.path.abspath(ontology)
    assert os.path.exists(abs_path), f"Ontology file not found: {abs_path}"
    
    if http_port == 8080:
        release_port(8080)

    release_port(port)
        
    manager = VirtuosoManager(abs_path, port=port, http_port=http_port, db_path=db_path)
    manager.run()
                    
            
# @cli.command()
# def runapi():
    # """Starts the FastAPI web server."""
    # uvicorn.run("ont_api.api:app", host="0.0.0.0", port=8080, reload=True)

if __name__ == "__main__":
    cli()
