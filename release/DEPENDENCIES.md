# Dependency scope and reproduction

## Offline paper reproduction

Python 3.10 or later and its standard library are sufficient:

```bash
python3 experiments/reproduce.py --out-dir /tmp/aberowl-paper-reproduction
python3 tests/test_paper_reproduction.py -v
```

Choose an empty output directory. These commands do not require package installation,
network access, credentials, a model API or an ontology service.

## New model runs and service development

`pyproject.toml` declares the central service dependencies. Its `evaluation` extra
declares the harness's direct HTTP client dependency (`httpx`); MCP is a core
dependency. The `test` extra supplies the test tools. The evaluation READMEs describe
the external services and paid model access required for new runs. Do not put
credentials in source files or command examples.

The MCP requirement is `>=1.28.1,<2.0` in `pyproject.toml`, the root
`requirements.txt`, and `central_server/requirements.txt`. This preserves the
existing central-service compatibility boundary. The core dependencies also include
`slowapi` and `rdflib`.

The lockfile specifies MCP 1.30.0 and RDFLib 7.6.0, including RDFLib's isodate
and pyparsing dependencies.

To recreate the resolved release environment:

```bash
uv lock --check
uv sync --frozen --extra evaluation --extra test
```

Installation requires network access or an already populated package cache. This
environment passed release-candidate validation. The historical experiment
environment remains incompletely recorded. The central Docker build uses its
requirements file; see “Docker and JVM dependencies”.

The root `requirements.txt` also includes legacy application dependencies and
optional tooling. It is not the minimal offline scoring environment. The historical
plotting scripts require matplotlib separately; they are not used by the offline
reproduction entry point.

## Docker and JVM dependencies

The central Dockerfile installs `central_server/requirements.txt` rather than
`uv.lock`. Except for MCP, that file mostly uses unpinned requirements; comments
record observations from a working container. A complete historical dependency lock
is unavailable. Docker base image tags are mutable and the Python lock does not pin
them.

The worker uses `Dockerfile.api` and Groovy `@Grab` declarations, principally in
`aberowlapi/OntologyServer.groovy`. Those declarations identify OWLAPI 4.5.29, ELK
0.4.3, HermiT 1.4.5.456, and Jetty 9.4.7.v20170914. They do not constitute a complete
transitive JVM dependency lock or a frozen Docker image. Rebuilding the service may
require package registries and Maven repositories.

Do not substitute present-day resolutions or image tags for missing historical
package inventories, service commits, or ontology checksums. Offline rescoring uses
the saved responses and is independent of a service rebuild.
