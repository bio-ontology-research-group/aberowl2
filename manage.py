import os
import click
# Import uvicorn only when needed to avoid dependency issues
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

 
if __name__ == "__main__":
    cli()
