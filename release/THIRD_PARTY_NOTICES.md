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
Manchester University Pizza Tutorial.

## Dependencies

Dependency manifests identify upstream packages; those packages keep their own
licences. In particular, `central_server/frontend/package-lock.json` records MIT,
Apache-2.0, BlueOak-1.0.0, ISC, Python-2.0, CC-BY-4.0, BSD-2-Clause, BSD-3-Clause,
MPL-2.0 and 0BSD licence labels. Consult the upstream licence texts for their conditions.

The source package does not bundle `node_modules`, a Python virtual
environment, downloaded Java jars or container images. Installation and builds
retrieve additional third-party components. Their distributions and licence notices
govern those components; a binary or container redistribution needs its own complete
notice inventory. The Python and Groovy dependency declarations do not provide a verified upstream
licence inventory.

## Evaluation data and saved responses

### Redistribution check (5 October 2026)

This check covers `aberowl2-v2.0.1-release.zip` in
[Zenodo record 22837022](https://zenodo.org/records/22837022). The archive contains
ontology-derived gold terms, identifiers, class universes, saved tool responses
and model answers. The check supports retaining these records with the stated
attributions, based on the source providers' declared licences and reuse policies.
The deposited ZIP remains unchanged.

### Ontology-derived material

We acknowledge these sources of grounding gold data. The
[OBO Foundry registry](https://obofoundry.org/registry/ontologies.jsonld) lists the
following licences for the 13 gold-data ontologies. These current declarations
may differ from those at collection.

- **CC BY 4.0:** [Gene Ontology (GO)](https://obofoundry.org/ontology/go.html),
  [Chemical Entities of Biological Interest (ChEBI)](https://obofoundry.org/ontology/chebi.html),
  [Cell Ontology (CL)](https://obofoundry.org/ontology/cl.html),
  [Mondo Disease Ontology](https://obofoundry.org/ontology/mondo.html),
  [Sequence Ontology (SO)](https://obofoundry.org/ontology/so.html),
  [Ontology for Biomedical Investigations (OBI)](https://obofoundry.org/ontology/obi.html),
  [Basic Formal Ontology (BFO)](https://obofoundry.org/ontology/bfo.html), and
  [Information Artifact Ontology (IAO)](https://obofoundry.org/ontology/iao.html).
- **CC BY 3.0:** [Uberon](https://obofoundry.org/ontology/uberon.html) and
  [Phenotype and Trait Ontology (PATO)](https://obofoundry.org/ontology/pato.html).
- **CC0 1.0:** [Symptom Ontology (SYMP)](https://obofoundry.org/ontology/symp.html)
  and [Relation Ontology (RO)](https://obofoundry.org/ontology/ro.html).
- **HPO custom licence:** Human Phenotype Ontology Consortium;
  release **2026-02-16**, verified in AberOWL's production OWL file.
  [Licence](https://hpo.jax.org/app/license);
  Gargano et al., *The Human Phenotype Ontology in 2024: phenotypes around the world*,
  [doi:10.1093/nar/gkad1005](https://doi.org/10.1093/nar/gkad1005).

[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode.en) and
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/legalcode) permit sharing
subject to their attribution and notice requirements; adaptations also require
appropriate identification. Preserve supplied attribution, licence links and
source identifiers. Benchmark records select and reformat source content and
include synthetic negative prompts; they are not official ontology releases.
The archive omits the full source ontology files and annotations, which limits
historical provenance. Historical HPO versions used per call remain unrecorded.

We also acknowledge the ontology projects whose class IRIs accompany saved
definitions; licence labels come from the same registry check.
Imported text retains its original source attribution and terms.

- **CC BY 3.0:** [Genomic Epidemiology Ontology (GENEPIO)](https://obofoundry.org/ontology/genepio.html), [Mouse pathology ontology (MPATH)](https://obofoundry.org/ontology/mpath.html), [The Ontology of Genes and Genomes (OGG)](https://obofoundry.org/ontology/ogg.html), [Planarian Phenotype Ontology (PLANP)](https://obofoundry.org/ontology/planp.html), [The Statistical Methods Ontology (STATO)](https://obofoundry.org/ontology/stato.html), [Xenopus Anatomy Ontology (XAO)](https://obofoundry.org/ontology/xao.html), [Xenopus Phenotype Ontology (XPO)](https://obofoundry.org/ontology/xpo.html), [Zebrafish anatomy and development ontology (ZFA)](https://obofoundry.org/ontology/zfa.html).
- **CC BY 4.0:** [Ascomycete phenotype ontology (APO)](https://obofoundry.org/ontology/apo.html), [BRENDA tissue / enzyme source (BTO)](https://obofoundry.org/ontology/bto.html), [Chemical Methods Ontology (CHMO)](https://obofoundry.org/ontology/chmo.html), [VEuPathDB ontology (EUPATH)](https://obofoundry.org/ontology/eupath.html), [Drosophila gross anatomy (FBBT)](https://obofoundry.org/ontology/fbbt.html), [FlyBase Controlled Vocabulary (FBCV)](https://obofoundry.org/ontology/fbcv.html), [Food Ontology (FOODON)](https://obofoundry.org/ontology/foodon.html), [Fission Yeast Phenotype Ontology (FYPO)](https://obofoundry.org/ontology/fypo.html), [Molecular Interactions Controlled Vocabulary (MI)](https://obofoundry.org/ontology/mi.html), [Mammalian Phenotype Ontology (MP)](https://obofoundry.org/ontology/mp.html), [NCI Thesaurus OBO Edition (NCIT)](https://obofoundry.org/ontology/ncit.html), [Ontology of Adverse Events (OAE)](https://obofoundry.org/ontology/oae.html), [Ontology for General Medical Science (OGMS)](https://obofoundry.org/ontology/ogms.html), [Obstetric and Neonatal Ontology (ONTONEO)](https://obofoundry.org/ontology/ontoneo.html), [Provisional Cell Ontology (PCL)](https://obofoundry.org/ontology/pcl.html), [PRotein Ontology (PRO) (PR)](https://obofoundry.org/ontology/pr.html), [Plant Trait Ontology (TO)](https://obofoundry.org/ontology/to.html), [C. elegans phenotype (WBPHENOTYPE)](https://obofoundry.org/ontology/wbphenotype.html).
- **CC0 1.0:** [Clinical measurement ontology (CMO)](https://obofoundry.org/ontology/cmo.html), [Core Ontology for Biology and Biomedicine (COB)](https://obofoundry.org/ontology/cob.html), [Human Disease Ontology (DOID)](https://obofoundry.org/ontology/doid.html), [Environmental conditions, treatments and exposures ontology (ECTO)](https://obofoundry.org/ontology/ecto.html), [Environment Ontology (ENVO)](https://obofoundry.org/ontology/envo.html), [Ontology of Biological Attributes (OBA)](https://obofoundry.org/ontology/oba.html), [Unified Phenotype Ontology (uPheno) (UPHENO)](https://obofoundry.org/ontology/upheno.html).

Source-specific credits for matched definition excerpts:

- The two disease descriptions beside an OMIM-linked identifier match
  [UniProt DI-00509](https://rest.uniprot.org/diseases/DI-00509.tsv).
  We credit the UniProt Consortium; [UniProt's licence](https://rest.uniprot.org/help/license)
  applies CC BY 4.0 to copyrightable database content.
- We credit Health Level Seven International for the matched relationship text. The specific [ActRelationshipType](https://terminology.hl7.org/en/CodeSystem-v3-ActRelationshipType.html)
  and [RoleLinkType](https://terminology.hl7.org/en/CodeSystem-v3-RoleLinkType.html)
  sources carry THO's CC0 designation for this text.
- The MedlinePlus excerpt matches the [After Surgery health-topic summary](https://medlineplus.gov/aftersurgery.html).
  Credit: MedlinePlus, U.S. National Library of Medicine. Its
  [reuse policy](https://medlineplus.gov/about/using/usingcontent/) identifies
  these summaries as public-domain content, separately from licensed material.
- We credit Dr Edit Hlaszny PhD for Vitis vinifera ontology material,
  copyright 2026, under its [CC BY 4.0 licence](https://hlaszny.com/vvo/LICENSE).
  Exact historical term versions remain unrecorded.
- Other matched sources include the [Experimental Factor Ontology](https://github.com/EBISPOT/efo),
  [Semanticscience Integrated Ontology](https://github.com/MaastrichtU-IDS/semanticscience),
  [Software Ontology](https://github.com/allysonlister/swo), and imported
  GO, PATO, Uberon and NCI/NICHD text. We acknowledge those projects and preserve
  original-source distinctions.
- We credit the U.S. National Library of Medicine for the MeSH adenylate-cyclase
  and thalamus excerpts, whose wording matches its
  [2014 MeSH release](https://nlmpubs.nlm.nih.gov/projects/mesh/2014/asciimesh/d2014.bin).
  Under the [MeSH terms](https://www.nlm.nih.gov/databases/download/terms_and_conditions_mesh.html),
  these historical excerpts do not reflect the most current or accurate NLM data.
  NLM does not endorse this benchmark.
- We credit the National Cancer Institute for the NCI excerpts on
  [interferon alfacon-1](https://www.cancer.gov/publications/dictionaries/cancer-drug/def/interferon-alfacon-1),
  malignant muscle neoplasm (C4883) and childhood alveolar soft-part sarcoma
  (C8092). The two disease definitions match
  [NCI Thesaurus 10.01d](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/archive/2010/10.01d_Release/Thesaurus_10.01d.FLAT.zip).
  NCI's [text reuse policy and stated exceptions](https://www.cancer.gov/policies/copyright-reuse)
  apply; the saved excerpts retain historical wording.
- We credit the National Institutes of Health for CRISP's
  [rod-cell definition, 1112-1874](https://web.archive.org/web/20091223034048id_/http://crisp.cit.nih.gov/Thesaurus/00007194.htm).
  The original NIH page matches the excerpt and carries no third-party notice.
  [NIH's copyright guidance](https://www.nih.gov/about-nih/frequently-asked-questions)
  supports reuse of this text.

For the EFO/BTO definition excerpts, we rely on BTO's declared
[CC BY 4.0 licence](https://github.com/BRENDA-Enzymes/BTO/blob/master/LICENSE.MD)
and preserve the credits in its
[source file](https://github.com/BRENDA-Enzymes/BTO/blob/master/bto.obo):
Dorland's Medical Dictionary/MerckSource for dorsal raphe nucleus (BTO:0002434),
Merriam-Webster for thalamus (BTO:0001365), and the PAE Virtual Glossary derived
from WCB/McGraw-Hill textbooks for apical meristem (BTO:0000034). We found no
specific licence exclusion for these definitions in the BTO distribution.

### Saved model outputs

The archive contains generated answers from DeepSeek V3.2, Qwen3.6-35B-A3B,
Gemini 3.5 Flash, Llama 4 Scout and GPT-OSS-20B, obtained through OpenRouter.
The archive excludes model weights. We checked these primary sources:

- [OpenRouter terms](https://openrouter.ai/terms), sections 5 and 6: output rights
  depend on the applicable model terms; OpenRouter provides no uniform output
  licence covering every routed provider.
- [DeepSeek V3.2 licence](https://huggingface.co/deepseek-ai/DeepSeek-V3.2/blob/main/LICENSE):
  MIT for the model repository material.
- [Qwen3.6-35B-A3B licence](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/main/LICENSE)
  and [GPT-OSS-20B documentation](https://developers.openai.com/api/docs/models/gpt-oss-20b):
  Apache 2.0 for the respective models.
- [Llama 4 Community Licence](https://dev.meta.ai/llama/llama4/license):
  governs Llama materials and their use. The archive distributes saved answers,
  not Llama weights or a newly trained model.
- [Gemini API terms](https://ai.google.dev/gemini-api/terms), “Use of Generated
  Content”, and [Google Cloud service terms](https://cloud.google.com/terms/service-terms),
  “Generative AI Services”: Google does not claim ownership of original generated
  content/new intellectual property in outputs; third-party rights still apply.

Several model-provider terms support output use or ownership. Five hosting
providers in the saved routing records have clauses on benchmark disclosure,
platform testing or AI development:
[AtlasCloud AUP section 7](https://www.atlascloud.ai/acceptable-use),
[Darkbloom section 7](https://www.darkbloom.ai/terms),
[SiliconFlow section 3.4(l)](https://docs.siliconflow.com/en/legals/terms-of-service),
[StreamLake international section 3.1(k)](https://www.streamlake.ai/document/DOC/mgkchnd89grpt1961fw),
and [Parasail section 2.2(h,m)](https://www.parasail.io/legal/terms-of-service).
Their scope varies. Applicability of each provider's terms remains unconfirmed.

The experiments benchmark how language models use AberOWL tools on ontology
tasks, including task accuracy. They do not measure or compare hosting-provider
features such as speed, latency, throughput or reliability. Provider routing is
only partially recorded: grounding routes were not logged, three reasoning run
files record per-run sets of provider names without a call-to-call mapping, and
two reasoning run files record no provider field. This distinction does not
establish which provider terms applied to the historical calls or whether those
terms permit publication of the saved answers and results.
