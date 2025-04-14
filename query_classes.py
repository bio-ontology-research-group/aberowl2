#!/usr/bin/env python3
"""
Example script to query all classes in the ontology using SPARQL.
This script connects to the Virtuoso SPARQL endpoint and retrieves all classes.
"""

import json
from SPARQLWrapper import SPARQLWrapper, JSON

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
    
    # Set the return format to JSON
    sparql.setReturnFormat(JSON)
    sparql.setQuery(query)
    
    # Execute the query and convert the results to JSON
    try:
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
        return []

def main():
    """Main function to execute the query and display results."""
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

if __name__ == "__main__":
    main()
