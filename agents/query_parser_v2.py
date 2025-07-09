from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
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

@app.post("/parse_v2_identify")
async def identify_entities(query: Query):
    prompt = """
    Extract potential ontology entities from natural language queries for Manchester OWL syntax.
    
    EXAMPLES:
    
    Query: "red cars"
    Output: {{
      "potential_classes": ["car", "red"],
      "potential_properties": [],
      "structure_hints": ["red" could modify "car" as: "car and red" OR "car and (hasColor some red)"],
      "quantifiers": [],
      "query_type": "class_expression"
    }}
    
    Query: "cars with the quality of being red or green"
    Output: {{
      "potential_classes": ["car", "quality", "red", "green"],
      "potential_properties": ["hasQuality", "quality", "with"],
      "structure_hints": ["with" suggests AND, "red or green" is a disjunction],
      "sub_expressions": ["red or green"],
      "quantifiers": [],
      "query_type": "class_expression"
    }}
    
    Query: "pizzas that have cheese or mushroom toppings"
    Output: {{
      "potential_classes": ["pizza", "cheese", "mushroom", "topping"],
      "potential_properties": ["hasTopping", "have", "topping"],
      "structure_hints": ["that have" suggests existential quantification],
      "sub_expressions": ["cheese or mushroom"],
      "quantifiers": [{{ "property": "hasTopping", "quantifier": "some" }}],
      "query_type": "class_expression"
    }}
    
    Query: "things with at least 3 parts"
    Output: {{
      "potential_classes": ["thing", "part"],
      "potential_properties": ["hasPart", "part", "with"],
      "structure_hints": ["with" suggests property restriction],
      "quantifiers": [{{ "property": "hasPart", "quantifier": "min", "cardinality": 3 }}],
      "query_type": "class_expression"
    }}
    
    Query: "animals that eat only plants"
    Output: {{
      "potential_classes": ["animal", "plant"],
      "potential_properties": ["eat", "eats"],
      "structure_hints": ["only" is universal quantification],
      "quantifiers": [{{ "property": "eat", "quantifier": "only" }}],
      "query_type": "class_expression"
    }}
    
    Query: "subclasses of red or blue vehicles"
    Output: {{
      "potential_classes": ["vehicle", "red", "blue"],
      "potential_properties": [],
      "structure_hints": ["red or blue" modifies "vehicles"],
      "sub_expressions": ["red or blue"],
      "quantifiers": [],
      "query_type": "subclass"
    }}
    
    PATTERNS TO RECOGNIZE:
    - "with/having/that have" often indicates property restrictions (AND + some)
    - "or" creates disjunctions within sub-expressions
    - "only" indicates universal quantification
    - "at least N" indicates min cardinality
    - "exactly N" indicates exact cardinality
    - "and" between properties usually means intersection
    - Prepositions often indicate properties: "in", "on", "from", "to", "with"
    - "-ing" words often indicate properties: "having", "containing", "eating"
    
    Now analyze this query: "{query}"
    
    Return JSON with the structure shown in examples above.
    """
    
    final_prompt = prompt.format(query=query.query)
    context = "You are a helpful assistant that parses natural language queries about ontologies and returns JSON."
    agent = ChatAgent(context, model=model)
    response = agent.step(final_prompt)
    interpretation = response.msgs[0].content

    try:
        # Extract JSON from the response
        json_match = re.search(r'\{.*\}', interpretation, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            # The user's prompt example uses double quotes, so this might not be needed.
            # But it's safer to have it.
            json_str = json_str.replace("'", '"')
            parsed_result = json.loads(json_str)
            return parsed_result
        else:
            # Fallback if no JSON is found
            return {"error": "Failed to parse LLM response", "raw_response": interpretation}
    except Exception as e:
        return {"error": str(e), "raw_response": interpretation}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
