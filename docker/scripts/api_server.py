import os
import sys
from aberowlapi.server_manager import OntologyServerManager

def main():
    """Starts the ontology API server.

    Accepts either:
      - A single OWL file path (single-ontology mode)
      - A directory containing OWL files (multi-ontology mode)
      - A JSON config file listing ontologies (multi-ontology mode)
    """
    if len(sys.argv) > 1:
        ontology_path = sys.argv[1]
    else:
        # Check for ONTOLOGY_PATH env var
        ontology_path = os.getenv('ONTOLOGY_PATH')
        if not ontology_path:
            raise ValueError("Ontology path must be provided as an argument or via ONTOLOGY_PATH env var.")

    abs_path = os.path.abspath(ontology_path)
    if not os.path.exists(abs_path):
        print(f"Error: Ontology file/directory not found: {abs_path}")
        sys.exit(1)

    if os.path.isdir(abs_path):
        print(f"Starting API server in multi-ontology mode with directory: {abs_path}")
    elif abs_path.endswith('.json'):
        print(f"Starting API server in multi-ontology mode with config: {abs_path}")
    else:
        print(f"Starting API server with ontology: {abs_path}")

    manager = OntologyServerManager(abs_path)
    manager.run()

if __name__ == "__main__":
    main()
