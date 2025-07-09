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
    prompt = f"""
Extract potential ontology entities from this query. 
Identify words/phrases that could be:
- Classes (types of things, usually nouns)
- Properties (relationships, usually verbs or prepositions with nouns)
- Quantifiers (some, only, all, exactly, min, max)
- Query type (subclass, superclass, equivalent, instances)

Query: "{query.query}"

Return JSON:
{{
  "potential_classes": ["list", "of", "class", "candidates"],
  "potential_properties": ["list", "of", "property", "candidates"],
  "quantifiers": [{{"property": "prop_name", "quantifier": "some"}}],
  "query_type": "subclass"
}}
"""
    context = "You are a helpful assistant that parses natural language queries about ontologies and returns JSON."
    agent = ChatAgent(context, model=model)
    response = agent.step(prompt)
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
