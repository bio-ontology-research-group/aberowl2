from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI()

class Query(BaseModel):
    query: str

@app.post("/parse_v2_identify")
async def identify_entities(query: Query):
    # For now, just return the input
    return {
        "raw_query": query.query,
        "entities_to_validate": {
            "potential_classes": ["Pizza", "Cheese"],  # Hardcoded for testing
            "potential_properties": ["hasTopping"],
            "potential_individuals": []
        }
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
