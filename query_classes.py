#!/usr/bin/env python3
"""
Simple script to query all classes in the ontology using SPARQL.
This script connects to the Virtuoso SPARQL endpoint and retrieves a list of all classes.
"""

import sys
import time
import requests
from SPARQLWrapper import SPARQLWrapper, JSON

def check_endpoint_availability(endpoint_url="http://localhost:8890/sparql", max_retries=3):
    """Check if the SPARQL endpoint is available."""
    print(f"Checking if SPARQL endpoint is available...")
    
    for i in range(max_retries):
        try:
            response = requests.get(endpoint_url)
            if response.status_code == 200:
                print(f"SPARQL endpoint is available!")
                return True
            else:
                print(f"Attempt {i+1}/{max_retries}: Endpoint returned status code {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"Attempt {i+1}/{max_retries}: Connection error: {e}")
        
        if i < max_retries - 1:
            print(f"Retrying in 3 seconds...")
            time.sleep(3)
    
    print(f"SPARQL endpoint is not available after {max_retries} attempts.")
    return False

def query_ontology_classes(endpoint_url="http://localhost:8890/sparql"):
    """Query all classes in the ontology using SPARQL."""
    # Initialize the SPARQL wrapper
    sparql = SPARQLWrapper(endpoint_url)
    sparql.setReturnFormat(JSON)
    
    # Simple query to get all classes
    query = """
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>
    
    SELECT DISTINCT ?class
    WHERE {
        ?class rdf:type owl:Class .
    }
    ORDER BY ?class
    """
    
    # Execute the query
    try:
        print("Querying for owl:Class instances...")
        sparql.setQuery(query)
        results = sparql.query().convert()
        
        # Extract class URIs from results
        classes = []
        for result in results["results"]["bindings"]:
            classes.append(result["class"]["value"])
            
        return classes
    except Exception as e:
        print(f"Error executing SPARQL query: {e}")
        return []

def main():
    """Main function to execute the query and display results."""
    if not check_endpoint_availability():
        print("Cannot connect to SPARQL endpoint. Make sure the Virtuoso server is running.")
        sys.exit(1)
    
    classes = query_ontology_classes()
    
    if classes:
        print(f"\nFound {len(classes)} classes. Printing first 10:")
        for i, class_uri in enumerate(classes[:10], 1):
            print(f"{i}. {class_uri}")
    else:
        print("No classes found or error occurred.")
        print("Possible issues:")
        print("1. The ontology file might not be properly loaded")
        print("2. The ontology might not contain any owl:Class declarations")
        print("\nTry running: docker compose logs virtuoso")

if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
