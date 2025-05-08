import os
import sys
from aberowlapi.server_manager import OntologyServerManager

def main():
    """Starts the ontology API server with the default ontology path."""
    # Check if ontology path is provided as an argument
    if len(sys.argv) > 1:
        ontology_path = sys.argv[1]
    else:
        raise ValueError("Ontology path must be provided as an argument.")
    
    abs_path = os.path.abspath(ontology_path)
    if not os.path.exists(abs_path):
        print(f"Error: Ontology file not found: {abs_path}")
        sys.exit(1)
        
    print(f"Starting API server with ontology: {abs_path}")
    manager = OntologyServerManager(abs_path)
    manager.run()

if __name__ == "__main__":
    main()
