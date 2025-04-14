#!/usr/bin/env python3
"""
Example script to query all classes in the ontology using SPARQL.
This script connects to the Virtuoso SPARQL endpoint and retrieves all classes.
"""

import json
import time
import sys
import requests
from SPARQLWrapper import SPARQLWrapper, JSON

def check_endpoint_availability(endpoint_url="http://localhost:8890/sparql", max_retries=5):
    """
    Check if the SPARQL endpoint is available.
    
    Args:
        endpoint_url (str): URL of the SPARQL endpoint
        max_retries (int): Maximum number of retries
        
    Returns:
        bool: True if endpoint is available, False otherwise
    """
    print(f"Checking if SPARQL endpoint {endpoint_url} is available...")
    
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
            print(f"Retrying in 5 seconds...")
            time.sleep(5)
    
    print(f"SPARQL endpoint is not available after {max_retries} attempts.")
    return False

def query_ontology_classes(endpoint_url="http://localhost:8890/sparql"):
    """
    Query all classes in the ontology using SPARQL.
    
    Args:
        endpoint_url (str): URL of the SPARQL endpoint
        
    Returns:
        list: List of classes with their URIs and labels
    """
    # Initialize the SPARQL wrapper with the endpoint URL
    sparql = SPARQLWrapper(endpoint_url)
    
    # Set the query to retrieve all classes and their labels if available
    query = """
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>
    
    SELECT ?class ?label
    WHERE {
        ?class rdf:type owl:Class .
        OPTIONAL { ?class rdfs:label ?label . FILTER(LANG(?label) = 'en') }
    }
    ORDER BY ?class
    LIMIT 100
    """
    
    # Try a simpler query first to check if the endpoint is working
    test_query = """
    SELECT ?s ?p ?o WHERE {
        ?s ?p ?o
    } LIMIT 5
    """
    
    # Set the return format to JSON
    sparql.setReturnFormat(JSON)
    
    # First try a simple query to see if the endpoint is working
    try:
        print("Testing endpoint with a simple query...")
        sparql.setQuery(test_query)
        test_results = sparql.query().convert()
        if len(test_results["results"]["bindings"]) > 0:
            print("Simple query successful, endpoint is working.")
        else:
            print("Simple query returned no results. Database might be empty.")
    except Exception as e:
        print(f"Error executing simple test query: {e}")
        print("The SPARQL endpoint might not be properly configured or the database is empty.")
        return []
    
    # Now try the actual class query
    sparql.setQuery(query)
    
    # Execute the query and convert the results to JSON
    try:
        print("Executing query for owl:Class instances...")
        results = sparql.query().convert()
        classes = []
        
        # Process the results
        for result in results["results"]["bindings"]:
            class_info = {
                "uri": result["class"]["value"]
            }
            
            if "label" in result:
                class_info["label"] = result["label"]["value"]
            else:
                class_info["label"] = None
                
            classes.append(class_info)
            
        return classes
    except Exception as e:
        print(f"Error executing SPARQL query: {e}")
        print("Try checking if the ontology was properly loaded into the database.")
        return []

def main():
    """Main function to execute the query and display results."""
    print("Checking SPARQL endpoint availability...")
    if not check_endpoint_availability():
        print("Cannot connect to SPARQL endpoint. Make sure the Virtuoso server is running.")
        sys.exit(1)
    
    print("Querying all classes in the ontology...")
    classes = query_ontology_classes()
    
    if classes:
        print(f"Found {len(classes)} classes:")
        for i, class_info in enumerate(classes[:10], 1):  # Show only first 10 for brevity
            label = class_info["label"] if class_info["label"] else "No label"
            print(f"{i}. {label} ({class_info['uri']})")
        
        if len(classes) > 10:
            print(f"... and {len(classes) - 10} more classes")
    else:
        print("No classes found or error occurred.")
        print("Possible issues:")
        print("1. The ontology file might not be properly loaded into Virtuoso")
        print("2. The SPARQL endpoint might not be correctly configured")
        print("3. The ontology might not contain any owl:Class declarations")
        print("\nTry running: docker-compose logs virtuoso")

if __name__ == "__main__":
    main()
