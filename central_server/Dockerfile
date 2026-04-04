FROM python:3.11-slim

WORKDIR /code

COPY ./central_server/requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY ./central_server/app /code/app
COPY ./central_server/mcp_ontology_server.py /code/mcp_ontology_server.py
COPY ./central_server/mcp_sparql_server.py /code/mcp_sparql_server.py
COPY ./central_server/config /code/config

# Create config directory if not present
RUN mkdir -p /code/config

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
