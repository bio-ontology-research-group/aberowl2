# Licences and third-party material

This document records locally available licensing evidence. The repository's
[LICENSE](../LICENSE) is the BSD 3-Clause licence, copyright 2025 Bio-Ontology
Research Group. Preserve that notice and its conditions for project software. Do not
apply it automatically to upstream software, ontology content or model-generated
responses.

## Bundled Pizza ontology

Both `data/pizza.owl` and `examples/selfhost/ontologies/pizza.owl` declare Creative
Commons Attribution 3.0 (CC BY 3.0) in their `terms:license` metadata. They identify
version 2.0, version IRI `http://www.co-ode.org/ontologies/pizza/2.0.0`, and
contributors Nick Drummond, Alan Rector, Matthew Horridge, Chris Wroe and Robert
Stevens. The files retain these annotations and the description crediting the
Manchester University Pizza Tutorial. This release cleanup preserves the ontology
content and licence. The ontology's licence is distinct from the code's BSD licence.

## Dependencies

Dependency manifests identify upstream packages; those packages keep their own
licences. In particular, `central_server/frontend/package-lock.json` records MIT,
Apache-2.0, BlueOak-1.0.0, ISC, Python-2.0, CC-BY-4.0, BSD-2-Clause, BSD-3-Clause,
MPL-2.0 and 0BSD licence labels for its dependency entries. These labels record
licence metadata. Consult the upstream licence texts for their conditions.

The selected source package does not bundle `node_modules`, a Python virtual
environment, downloaded Java jars or container images. Installation and builds
retrieve additional third-party components. Their distributions and licence notices
govern those components; a binary or container redistribution needs its own complete
notice inventory. The Python and Groovy dependency declarations do not themselves
provide a verified upstream licence inventory.

## Evaluation data and saved responses

Gold terms, identifiers, class universes and tool-response excerpts derive from
third-party ontologies and services. The reasoning sets cover GO, CL and SO; the
grounding dataset covers further ontologies identified in each gold row. An IRI or
registry date does not establish redistribution permission for all associated labels,
definitions or other text. The experimental artifacts do not include original
per-ontology licence snapshots.

We obtained the saved model answers through OpenRouter from multiple model providers.
The files preserve evaluation evidence, but do not provide a verified redistribution
licence for all generated text or quoted third-party content. The archive does not
include the provider/model licences and service terms that applied at collection.
This document does not assert ownership of that content or grant rights on behalf of
upstream authors or providers.

The project software licence does not establish a uniform licence for all
third-party material in this archive.
