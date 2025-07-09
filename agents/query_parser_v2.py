from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
import os
import json
import re
from typing import Optional
import logging

from elasticsearch import AsyncElasticsearch

from camel.models import ModelFactory
from camel.types import ModelPlatformType
from camel.agents import ChatAgent

app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Elasticsearch client
ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")
ES_INDEX = os.getenv("ES_INDEX", "ontology_entities")
es = AsyncElasticsearch(ES_HOST)


model = ModelFactory.create(
    model_platform=ModelPlatformType.OPENROUTER,
    model_type="deepseek/deepseek-chat-v3-0324:free",
    api_key=OPENROUTER_API_KEY,
    model_config_dict={"temperature": 0.0, "max_tokens": 100000},
)


class Query(BaseModel):
    query: str

class ESSearchQuery(BaseModel):
    entity: str
    entity_type: Optional[str] = None

# Keywords for different query types
QUERY_TYPE_KEYWORDS = {
    "subclass_indicators": [
        "subclass", "subclasses", "type of", "types of", "kind of", "kinds of",
        "specific", "specialized", "subtypes", "children of", "derived from",
        "examples of", "instances of", "forms of", "varieties of"
    ],
    "superclass_indicators": [
        "superclass", "superclasses", "generalization", "generalizations",
        "parent of", "parents of", "broader than", "general", "abstract",
        "encompasses", "subsumes"
    ],
    "equivalent_indicators": [
        "equivalent", "same as", "exactly", "precisely", "defined as",
        "means the same as", "is the same as", "equals"
    ],
    "exclusion_indicators": [
        "strict subclass", "proper subclass", "only subclass",
        "strict superclass", "proper superclass", "only superclass",
        "not equivalent", "not the same", "excluding equivalent",
        "proper subset", "strict subset"
    ]
}

def detect_query_type(query: str):
    """
    Detect query type with defaults to subeq/supeq unless explicitly excluded
    """
    query_lower = query.lower()
    
    # Check for explicit exclusions first
    exclude_equivalent = any(indicator in query_lower for indicator in QUERY_TYPE_KEYWORDS["exclusion_indicators"])
    
    # Check for query type indicators
    has_subclass = any(indicator in query_lower for indicator in QUERY_TYPE_KEYWORDS["subclass_indicators"])
    has_superclass = any(indicator in query_lower for indicator in QUERY_TYPE_KEYWORDS["superclass_indicators"])
    has_equivalent = any(indicator in query_lower for indicator in QUERY_TYPE_KEYWORDS["equivalent_indicators"])
    
    # Determine query type
    if has_equivalent and not exclude_equivalent:
        return "equivalent"
    elif has_subclass and exclude_equivalent:
        return "subclass"
    elif has_superclass and exclude_equivalent:
        return "superclass"
    elif has_subclass:
        return "subeq"  # Default: subclass or equivalent
    elif has_superclass:
        return "supeq"  # Default: superclass or equivalent
    else:
        # Default when no clear indicators
        return "subeq"


async def call_llm(prompt: str):
    context = "You are a helpful assistant that parses natural language queries about ontologies and returns JSON."
    agent = ChatAgent(context, model=model)
    response = await run_in_threadpool(agent.step, prompt)
    interpretation = response.msgs[0].content

    try:
        # Extract JSON from the response
        json_match = re.search(r'\{.*\}', interpretation, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            json_str = json_str.replace("'", '"')
            parsed_result = json.loads(json_str)
            return parsed_result
        else:
            return {"error": "Failed to parse LLM response", "raw_response": interpretation}
    except Exception as e:
        return {"error": str(e), "raw_response": interpretation}


async def search_entity_in_es(entity: str, entity_type: Optional[str] = None):
    """
    Searches for an entity in ElasticSearch using multiple strategies.
    """
    query_body = {
        "size": 5,
        "query": {
            "bool": {
                "should": [
                    {"match_phrase": {"label": {"query": entity, "boost": 10}}},
                    {"match": {"label": {"query": entity, "fuzziness": "AUTO"}}},
                    {"match": {"synonyms": entity}}
                ],
                "minimum_should_match": 1
            }
        }
    }

    if entity_type:
        query_body["query"]["bool"]["filter"] = [
            {"term": {"type": entity_type.lower()}}
        ]

    logger.info(f"Elasticsearch query: {json.dumps(query_body, indent=2)}")
    try:
        response = await es.search(index=ES_INDEX, body=query_body)
        
        candidates = []
        for hit in response['hits']['hits']:
            source = hit['_source']
            candidates.append({
                "label": source.get("label"),
                "uri": source.get("uri"),
                "type": source.get("type"),
                "score": hit['_score']
            })
        
        return {"found": len(candidates) > 0, "candidates": candidates}

    except Exception as e:
        return {"found": False, "candidates": [], "error": str(e)}


def _to_camel_case(s: str) -> str:
    parts = re.split(r'[\s_]+', s)
    if not parts:
        return ""
    return parts[0].lower() + ''.join(x.title() for x in parts[1:])

def generate_property_variations(prop_name: str) -> list[str]:
    """Generates likely variations of a property name, including some inverse patterns."""
    prop_name = prop_name.lower()
    variations = {prop_name}
    
    # Handle multi-word properties like "part of"
    if ' ' in prop_name or '_' in prop_name:
        camel_case = _to_camel_case(prop_name)
        variations.add(camel_case)
        variations.add(f"has{camel_case.title()}")
        variations.add(prop_name.replace(' ', '_'))
        # Specific inverse pattern for "part of" -> "hasPart"
        if "part of" in prop_name:
            variations.add("hasPart")
    else: # Single word like "topping"
        variations.add(f"has{prop_name.title()}")
        variations.add(f"has_{prop_name}")
        variations.add(f"is{prop_name.title()}")

    return list(variations)


async def validate_all_entities(extraction_result: dict):
    """
    Validates entities from LLM extraction against Elasticsearch.
    """
    validated_entities = {
        "classes": {},
        "properties": {}
    }
    unmatched_entities = {
        "classes": [],
        "properties": []
    }

    # Validate classes
    potential_classes = extraction_result.get("potential_classes", [])
    for p_class in potential_classes:
        result = await search_entity_in_es(p_class, "class")
        if result.get("found"):
            # Take the top candidate
            validated_entities["classes"][p_class] = result["candidates"][0]
        else:
            unmatched_entities["classes"].append(p_class)

    # Validate properties
    potential_properties = extraction_result.get("potential_properties", [])
    for p_prop in potential_properties:
        variations = generate_property_variations(p_prop)
        found_prop = False
        for var in variations:
            result = await search_entity_in_es(var, "property")
            if result.get("found"):
                validated_entities["properties"][p_prop] = result["candidates"][0]
                found_prop = True
                break # Found a match, move to next potential property
        if not found_prop:
            unmatched_entities["properties"].append(p_prop)
            
    return {
        "validated_entities": validated_entities,
        "unmatched_entities": unmatched_entities
    }


MANCHESTER_GENERATION_PROMPT = """
Your task is to convert a natural language query into a Manchester OWL Syntax class expression.
You MUST use ONLY the provided validated entities. Do not invent new classes or properties.
Use the entity labels provided in the JSON, not the original words from the query.

- For simple conjunctions, use 'and'.
- For disjunctions, use 'or' and group with parentheses.
- For property restrictions, use the format: 'PROPERTY QUANTIFIER CLASS'.
  - Common quantifiers: some, only, min, max, exactly, value.
- If a cardinality is given without a class (e.g., "at least 3 parts"), use 'Thing' as the class.

---
EXAMPLE 1:
Natural Language Query: "pizzas with cheese or mushroom toppings"
Validated Entities:
{{
  "classes": {{
    "pizza": {{"label": "Pizza"}},
    "cheese": {{"label": "Cheese"}},
    "mushroom": {{"label": "Mushroom"}}
  }},
  "properties": {{
    "hasTopping": {{"label": "hasTopping"}}
  }}
}}

Resulting Manchester Expression:
Pizza and (hasTopping some (Cheese or Mushroom))

---
EXAMPLE 2:
Natural Language Query: "cars with exactly 4 wheels"
Validated Entities:
{{
  "classes": {{
    "car": {{"label": "Car"}},
    "wheel": {{"label": "Wheel"}}
  }},
  "properties": {{
    "hasPart": {{"label": "hasPart"}}
  }}
}}

Resulting Manchester Expression:
Car and (hasPart exactly 4 Wheel)

---
EXAMPLE 3:
Natural Language Query: "red or blue vehicles"
Validated Entities:
{{
  "classes": {{
    "vehicle": {{"label": "Vehicle"}},
    "red": {{"label": "Red"}},
    "blue": {{"label": "Blue"}}
  }},
  "properties": {{}}
}}

Resulting Manchester Expression:
Vehicle and (Red or Blue)

---
EXAMPLE 4:
Natural Language Query: "animals that eat only plants"
Validated Entities:
{{
  "classes": {{
    "animal": {{"label": "Animal"}},
    "plant": {{"label": "Plant"}}
  }},
  "properties": {{
    "eats": {{"label": "eats"}}
  }}
}}

Resulting Manchester Expression:
Animal and (eats only Plant)

---
Now, generate the Manchester expression for the following:

Natural Language Query: "{query}"
Validated Entities:
{validation_json}

Resulting Manchester Expression:
"""

async def generate_candidate_manchester(query: str, extraction: dict, validation: dict):
    """
    Generates a candidate Manchester query using an LLM, constrained by validated entities.
    """
    # We only need the labels for the prompt, not the full validation dict
    simple_validation = {
        "classes": {k: {"label": v["label"]} for k, v in validation["validated_entities"]["classes"].items()},
        "properties": {k: {"label": v["label"]} for k, v in validation["validated_entities"]["properties"].items()}
    }
    
    validation_json = json.dumps(simple_validation, indent=2)
    
    final_prompt = MANCHESTER_GENERATION_PROMPT.format(query=query, validation_json=validation_json)

    # We don't want a JSON response here, just the raw text of the expression.
    context = "You are a helpful assistant that converts natural language to Manchester OWL Syntax."
    agent = ChatAgent(context, model=model)
    response = await run_in_threadpool(agent.step, final_prompt)
    candidate = response.msgs[0].content.strip()

    if candidate:
        return {
            "success": True,
            "candidate": candidate,
            "unmatched_entities": validation.get("unmatched_entities")
        }
    else:
        return {
            "success": False,
            "candidate": "",
            "unmatched_entities": validation.get("unmatched_entities"),
            "error": "LLM failed to generate a candidate expression."
        }


@app.post("/parse_v2")
async def parse_v2(query: Query):
    """
    Runs the full parsing pipeline and returns a concise result.
    """
    # 1. Identify potential entities
    extraction_result = await identify_entities(query)
    if "error" in extraction_result:
        return {"error": "Extraction failed", "details": extraction_result}
    
    # 2. Validate entities against ES
    validation_result = await validate_all_entities(extraction_result)
    if "error" in validation_result:
        return {"error": "Validation failed", "details": validation_result}
        
    # 3. Generate Manchester candidate
    generation_result = await generate_candidate_manchester(query.query, extraction_result, validation_result)
    
    return {
        "query": query.query,
        "query_type": extraction_result.get("query_type"),
        "manchester_expression": generation_result.get("candidate"),
        "unmatched_entities": generation_result.get("unmatched_entities"),
        "validation_details": validation_result
    }


@app.post("/parse_v2_debug")
async def parse_v2_debug(query: Query):
    """
    Full pipeline with intermediate steps: identify, validate, and generate Manchester query.
    """
    # 1. Identify potential entities
    extraction_result = await identify_entities(query)
    if "error" in extraction_result:
        return {"error": "Extraction failed", "details": extraction_result}
    
    # 2. Validate entities against ES
    validation_result = await validate_all_entities(extraction_result)
    if "error" in validation_result:
        return {"error": "Validation failed", "details": validation_result}
        
    # 3. Generate Manchester candidate
    generation_result = await generate_candidate_manchester(query.query, extraction_result, validation_result)
    
    return {
        "extraction": extraction_result,
        "validation": validation_result,
        "generation": generation_result
    }


@app.post("/parse_v2_validate")
async def validate_query_entities(query: Query):
    """
    Full pipeline: identify potential entities and then validate them.
    """
    # 1. Identify potential entities
    extraction_result = await identify_entities(query)
    if "error" in extraction_result:
        return extraction_result
    
    # 2. Validate entities against ES
    validation_result = await validate_all_entities(extraction_result)
    
    return {
        "extraction": extraction_result,
        "validation": validation_result
    }


# Enhanced prompt with query type examples
QUERY_TYPE_PROMPT = """
Extract entities and determine query type from natural language.

QUERY TYPE EXAMPLES:

1. SUBEQ (Subclass or Equivalent - DEFAULT):
   - "pizzas with cheese" → Find all pizzas with cheese (including equivalent definitions)
   - "red cars" → Find all things that are red cars
   - "things with 4 wheels" → Find all classes that have 4 wheels

2. SUBCLASS (Strict Subclass Only):
   - "strict subclasses of vehicle" → Only proper subclasses, not Vehicle itself
   - "proper types of pizza" → Only specializations of Pizza
   - "kinds of animals (not equivalent)" → Exclude equivalent classes

3. SUPEQ (Superclass or Equivalent):
   - "generalizations of Car" → Find superclasses of Car (including Car itself)
   - "what encompasses Pizza" → Find classes that subsume Pizza
   - "broader categories than Dog" → Find superclasses

4. SUPERCLASS (Strict Superclass Only):
   - "strict superclasses of Pizza" → Only proper superclasses
   - "proper generalizations of Car" → Exclude Car itself

5. EQUIVALENT:
   - "things defined exactly as red cars" → Only equivalent definitions
   - "what is the same as Pizza with cheese" → Find equivalent classes
   - "classes equivalent to Animal that eats meat" → Exact matches only

EXAMPLES WITH ANALYSIS:

Query: "types of pizzas with cheese topping"
{{
  "potential_classes": ["pizza", "cheese", "topping"],
  "potential_properties": ["hasTopping", "topping", "with"],
  "query_type": "subeq",
  "query_type_reasoning": "'types of' indicates subclass, no exclusion mentioned, default to subeq"
}}

Query: "strict subclasses of red vehicles"
{{
  "potential_classes": ["vehicle", "red"],
  "potential_properties": [],
  "query_type": "subclass",
  "query_type_reasoning": "'strict subclasses' explicitly excludes equivalents"
}}

Query: "what is equivalent to cars that use electricity"
{{
  "potential_classes": ["car", "electricity"],
  "potential_properties": ["use", "uses"],
  "query_type": "equivalent",
  "query_type_reasoning": "'equivalent to' explicitly requests equivalent classes only"
}}

Query: "animals that eat plants"
{{
  "potential_classes": ["animal", "plant"],
  "potential_properties": ["eat", "eats"],
  "query_type": "subeq",
  "query_type_reasoning": "No query type specified, default to subeq"
}}

Query: "generalizations of pizza (not including pizza itself)"
{{
  "potential_classes": ["pizza"],
  "potential_properties": [],
  "query_type": "superclass",
  "query_type_reasoning": "'not including pizza itself' excludes equivalent"
}}

Now analyze: "{query}"
"""

@app.post("/parse_v2_identify")
async def identify_entities(query: Query):
    # Use pattern detection
    query_type = detect_query_type(query.query)
    
    # Get LLM analysis with enhanced prompt
    prompt = QUERY_TYPE_PROMPT.format(query=query.query)
    llm_result = await call_llm(prompt)
    
    # Override LLM's query type if pattern detection is more reliable
    if "error" not in llm_result:
        llm_result["query_type"] = query_type
        llm_result["query_type_source"] = "pattern_detection"
    
    return llm_result


@app.post("/test_es_search")
async def test_es_search(query: ESSearchQuery):
    """
    Test endpoint for searching entities in Elasticsearch.
    """
    return await search_entity_in_es(query.entity, query.entity_type)


# Add helper function to provide additional structure hints
def analyze_query_patterns(query: str):
    """
    Pre-process query to identify structural patterns
    """
    patterns = {
        "has_with": "with" in query or "having" in query,
        "has_that": "that" in query,
        "has_only": "only" in query,
        "has_some": "some" in query or "any" in query,
        "has_all": "all" in query or "every" in query,
        "has_cardinality": any(phrase in query for phrase in 
                              ["at least", "at most", "exactly", "more than", "less than"]),
        "has_disjunction": " or " in query,
        "has_conjunction": " and " in query,
        "has_negation": "not" in query or "no" in query,
        "query_markers": {
            "subclass": "subclass" in query or "types of" in query,
            "superclass": "superclass" in query or "generalization" in query,
            "equivalent": "same as" in query or "equivalent" in query,
            "instances": "instances of" in query or "individuals" in query
        }
    }
    
    # Extract potential sub-expressions (text between certain markers)
    import re
    sub_expressions = []
    
    # Pattern for "X or Y" expressions
    or_pattern = re.findall(r'(\w+\s+or\s+\w+)', query)
    sub_expressions.extend(or_pattern)
    
    # Pattern for "that/which [verb]" clauses
    clause_pattern = re.findall(r'(?:that|which)\s+(\w+.*?)(?:\s+and|\s+or|$)', query)
    sub_expressions.extend(clause_pattern)
    
    return {
        "patterns": patterns,
        "sub_expressions": sub_expressions
    }

def merge_hints(patterns, llm_result):
    """
    A simple function to combine hints from pattern analysis and LLM extraction.
    """
    # This is a placeholder implementation.
    # It can be made more sophisticated later.
    combined = {
        "query_type": llm_result.get("query_type", "unknown"),
        "structure_hints": llm_result.get("structure_hints", []),
        "detected_patterns": patterns.get("patterns", {}),
        "detected_sub_expressions": patterns.get("sub_expressions", [])
    }
    if "error" in llm_result:
        combined["llm_error"] = llm_result["error"]
    return combined

@app.post("/parse_v2_identify_debug")
async def identify_entities_debug(query: Query):
    # First get pattern analysis
    patterns = analyze_query_patterns(query.query)
    
    # Then get LLM analysis
    llm_result = await identify_entities(query)
    
    # Combine results
    return {
        "query": query.query,
        "pattern_analysis": patterns,
        "llm_extraction": llm_result,
        "combined_hints": merge_hints(patterns, llm_result)
    }


@app.post("/test_query_types")
async def test_query_type_detection():
    test_cases = [
        # Default subeq cases
        ("pizzas with cheese", "subeq"),
        ("red cars", "subeq"),
        ("things that have 4 wheels", "subeq"),
        
        # Explicit subclass
        ("strict subclasses of Vehicle", "subclass"),
        ("proper types of Pizza", "subclass"),
        ("only subclasses of Animal", "subclass"),
        
        # Explicit superclass
        ("strict superclasses of Car", "superclass"),
        ("proper generalizations of Pizza", "superclass"),
        
        # Default supeq
        ("generalizations of Car", "supeq"),
        ("what encompasses Pizza", "supeq"),
        ("broader than Dog", "supeq"),
        
        # Equivalent
        ("equivalent to red cars", "equivalent"),
        ("same as Pizza with cheese", "equivalent"),
        ("exactly animals that fly", "equivalent"),
        
        # Complex cases
        ("types of pizzas (not equivalent)", "subclass"),
        ("generalizations of Car including Car itself", "supeq"),
        ("what is a kind of vehicle", "subeq")
    ]
    
    results = []
    for query, expected in test_cases:
        detected = detect_query_type(query)
        llm_result = await identify_entities(Query(query=query))
        
        results.append({
            "query": query,
            "expected": expected,
            "pattern_detected": detected,
            "llm_detected": llm_result.get("query_type"),
            "correct": detected == expected,
            "entities": llm_result.get("potential_classes", [])
        })
    
    return {
        "test_results": results,
        "accuracy": sum(r["correct"] for r in results) / len(results)
    }


@app.post("/debug_query_type")
async def debug_query_type(query: Query):
    query_lower = query.query.lower()
    
    # Detailed keyword matching
    keyword_matches = {
        "subclass_keywords": [kw for kw in QUERY_TYPE_KEYWORDS["subclass_indicators"] 
                              if kw in query_lower],
        "superclass_keywords": [kw for kw in QUERY_TYPE_KEYWORDS["superclass_indicators"] 
                                if kw in query_lower],
        "equivalent_keywords": [kw for kw in QUERY_TYPE_KEYWORDS["equivalent_indicators"] 
                                if kw in query_lower],
        "exclusion_keywords": [kw for kw in QUERY_TYPE_KEYWORDS["exclusion_indicators"] 
                               if kw in query_lower]
    }
    
    pattern_type = detect_query_type(query.query)
    llm_result = await identify_entities(query)
    
    return {
        "query": query.query,
        "keyword_matches": keyword_matches,
        "pattern_detection_result": pattern_type,
        "llm_result": llm_result.get("query_type"),
        "llm_reasoning": llm_result.get("query_type_reasoning"),
        "final_query_type": pattern_type,
        "explanation": f"Detected as {pattern_type} because: {keyword_matches}"
    }


@app.post("/test_pipeline")
async def test_pipeline():
    """
    Runs a suite of test cases through the full parsing pipeline.
    """
    test_cases = [
        "pizzas with cheese topping",
        "vehicles that have exactly 4 wheels",
        "red or blue cars",
        "animals that eat only plants",
        "things with at least 3 parts"
    ]
    
    results = []
    for q_str in test_cases:
        query = Query(query=q_str)
        # Re-using the debug endpoint logic to get all steps
        pipeline_result = await parse_v2_debug(query)

        if "error" in pipeline_result:
            results.append({"query": q_str, "error": pipeline_result})
            continue

        extraction = pipeline_result.get("extraction", {})
        validation = pipeline_result.get("validation", {})
        generation = pipeline_result.get("generation", {})
        
        unmatched = validation.get("unmatched_entities", {})
        is_valid = not unmatched.get("classes") and not unmatched.get("properties")
        
        results.append({
            "query": q_str,
            "extracted_entities": extraction,
            "es_validation_results": validation,
            "generated_manchester_expression": generation.get("candidate"),
            "is_valid": is_valid
        })
    
    return {"test_results": results}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
