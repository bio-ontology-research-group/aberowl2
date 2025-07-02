import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


from camel.models import ModelFactory
from camel.types import ModelPlatformType
from camel.agents import ChatAgent
from camel.toolkits import FunctionTool
import os

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


context = "You are a helpful assistant that can parse a query in natural language and detect what is the entity to query about and what the type of query is. Query types can be: 'superclass', 'subclass', 'equivalent'. Return your answer in the format: {'query': 'entity', 'type': 'query_type'}. If you cannot determine the query type, return 'unknown'."

model = ModelFactory.create(
    model_platform=ModelPlatformType.OPENROUTER,
    model_type="deepseek/deepseek-chat-v3-0324:free",
    api_key=OPENROUTER_API_KEY,
    model_config_dict={"temperature": 0.3, "max_tokens": 100000},
    
)

from pydantic import BaseModel

class QueryInput(BaseModel):
    input: str

@app.post("/process")
async def process_string(data: QueryInput):
    result = query_parser(data.input)
    
    # If result is already a dictionary, return it directly
    if isinstance(result, dict):
        return result
    
    # If result is a string, try to extract and parse JSON-like content
    if isinstance(result, str):
        try:
            import json
            import re
            
            # Try to extract JSON-like content using regex
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                # Get the JSON-like string
                json_str = json_match.group(0)
                
                # Replace single quotes with double quotes for JSON compatibility
                # This is a simple approach and might not work for all cases
                json_str = json_str.replace("'", '"')
                
                try:
                    # Try to parse the modified string
                    parsed_result = json.loads(json_str)
                    return parsed_result
                except json.JSONDecodeError as e:
                    print(f"JSON decode error: {e}")
        except Exception as e:
            print(f"Error parsing result: {e}")
    
    # If all parsing attempts fail, return a default structure
    return {"query": "unknown", "type": "unknown"}


def test_query_parser():
    
    query = "What are the superclasses of cheesypizza?"
    agent = ChatAgent(context, model=model)

    print("Testing query:", query)
    prompt = f"Parse the following query and determine the entity and type of query:\n\n{query}"
    response = agent.step(prompt)
    interpretation = response.msgs[0].content
    print(f"Agent's interpretation: {interpretation}")

    agent.reset()

def query_parser(query):
    import json
    import re
    
    agent = ChatAgent(context, model=model)
    # Update the prompt to explicitly request JSON format with double quotes
    prompt = f"""Parse the following query and determine the entity and type of query:

{query}

Return your answer in valid JSON format with double quotes, like this example:
{{"query": "entity_name", "type": "query_type"}}"""

    response = agent.step(prompt)
    interpretation = response.msgs[0].content
    
    # Try to extract structured data from the interpretation
    try:
        # Check if the interpretation contains a JSON-like structure
        json_match = re.search(r'\{.*\}', interpretation, re.DOTALL)
        if json_match:
            # Get the JSON-like string
            json_str = json_match.group(0)
            
            # Replace single quotes with double quotes for JSON compatibility
            json_str = json_str.replace("'", '"')
            
            try:
                # Try to parse the modified string
                parsed_data = json.loads(json_str)
                return parsed_data
            except json.JSONDecodeError:
                print(f"Failed to parse JSON: {json_str}")
    except Exception as e:
        print(f"Error parsing interpretation: {e}")
    
    # If parsing fails, create a structured response
    # Extract potential entity and type using regex
    entity_match = re.search(r'entity["\']?\s*[:=]\s*["\']([^"\']+)["\']', interpretation, re.IGNORECASE)
    type_match = re.search(r'type["\']?\s*[:=]\s*["\']([^"\']+)["\']', interpretation, re.IGNORECASE)
    
    entity = entity_match.group(1) if entity_match else "unknown"
    query_type = type_match.group(1) if type_match else "unknown"
    
    return {"query": entity, "type": query_type}

    
if __name__ == "__main__":
    # test_query_parser()
    uvicorn.run(app, host="0.0.0.0", port=8000)
