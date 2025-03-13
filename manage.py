import os
import click
import uvicorn
from aberowlapi.server_manager import OntologyServerManager
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
                    
            
# @cli.command()
# def runapi():
    # """Starts the FastAPI web server."""
    # uvicorn.run("ont_api.api:app", host="0.0.0.0", port=8080, reload=True)

if __name__ == "__main__":
    cli()
