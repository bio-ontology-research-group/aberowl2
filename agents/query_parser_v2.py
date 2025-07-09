from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
import os
import json
import re

from camel.models import ModelFactory
from camel.types import ModelPlatformType
from camel.agents import ChatAgent

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


model = ModelFactory.create(
    model_platform=ModelPlatformType.OPENROUTER,
    model_type="deepseek/deepseek-chat-v3-0324:free",
    api_key=OPENROUTER_API_KEY,
    model_config_dict={"temperature": 0.0, "max_tokens": 100000},
)


class Query(BaseModel):
    query: str

def detect_query_type(query: str):
    """
    Detect query type with defaults to subeq/supeq unless explicitly excluded
    """
    query_lower = query.lower()
    
    # Keywords for different query types
    keywords = {
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
    
    # Check for explicit exclusions first
    exclude_equivalent = any(indicator in query_lower for indicator in keywords["exclusion_indicators"])
    
    # Check for query type indicators
    has_subclass = any(indicator in query_lower for indicator in keywords["subclass_indicators"])
    has_superclass = any(indicator in query_lower for indicator in keywords["superclass_indicators"])
    has_equivalent = any(indicator in query_lower for indicator in keywords["equivalent_indicators"])
    
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
