"""
OBOFoundry ontology metadata fetcher.

Fetches the ontologies.yml metadata file from OBOFoundry daily and returns
a list of ontology entries with their source URLs (ontology_purl).
"""

import logging
from typing import Any, Dict, List

import aiohttp
import yaml

logger = logging.getLogger(__name__)

OBOFOUNDRY_YAML_URL = "https://purl.obolibrary.org/meta/ontologies.yml"


async def fetch_obofoundry_ontologies(*, report=None) -> List[Dict[str, Any]]:
    """
    Fetch and parse the OBOFoundry ontologies.yml metadata file.

    Returns a list of dicts with standardised fields. Obsolete entries and
    entries without an ontology_purl are skipped.
    """
    if report is not None:
        report["state"] = "unavailable_or_invalid_response"
    logger.info("Fetching OBOFoundry metadata from %s", OBOFOUNDRY_YAML_URL)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                OBOFOUNDRY_YAML_URL,
                timeout=aiohttp.ClientTimeout(total=120),
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        "Failed to fetch OBOFoundry YAML: HTTP %s", resp.status
                    )
                    return []
                raw_text = await resp.text()
    except Exception as e:
        logger.error("Error fetching OBOFoundry metadata: %s", e)
        return []

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        logger.error("Failed to parse OBOFoundry YAML: %s", e)
        return []

    if not isinstance(data, dict) or not isinstance(data.get("ontologies"), list):
        return []
    ontologies = []
    for entry in data.get("ontologies", []):
        if not isinstance(entry, dict) or entry.get("is_obsolete", False):
            continue

        purl = entry.get("ontology_purl")
        if not purl:
            continue

        if purl.startswith("http://purl.obolibrary.org/"):
            purl = "https://" + purl[len("http://"):]

        ont_id = entry.get("id", "").lower().strip()
        if not ont_id:
            continue

        license_info = entry.get("license", {})
        license_url = ""
        if isinstance(license_info, dict):
            license_url = license_info.get("url", "")

        ontologies.append(
            {
                "ontology_id": ont_id,
                "name": entry.get("title", ont_id),
                "description": entry.get("description", ""),
                "source": "obofoundry",
                "source_url": purl,
                "homepage": entry.get("homepage", ""),
                "license": license_url,
                "contact": entry.get("contact", []),
            }
        )

    if report is not None:
        report.update(state="ok", discovered=len(ontologies))
    logger.info("Fetched %d active OBOFoundry ontologies", len(ontologies))
    return ontologies
