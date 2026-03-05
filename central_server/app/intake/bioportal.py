"""
BioPortal ontology metadata fetcher.

Uses the BioPortal REST API to enumerate all available ontologies and their
download URLs. Ontologies already registered from OBOFoundry are skipped.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

import aiohttp

logger = logging.getLogger(__name__)

BIOPORTAL_API_URL = "https://data.bioontology.org"
BIOPORTAL_API_KEY = "7LWB1EK24e8Pj7XorQdG9FnsxQA3H41VDKIxN1BeEv5n"

# Max concurrent requests to BioPortal to avoid rate limiting
_CONCURRENCY_LIMIT = 5


async def _get_json(
    session: aiohttp.ClientSession, url: str, params: Optional[Dict] = None
) -> Optional[Any]:
    """GET a BioPortal API endpoint and return parsed JSON, or None on error."""
    base_params = {"apikey": BIOPORTAL_API_KEY}
    if params:
        base_params.update(params)
    try:
        async with session.get(
            url,
            params=base_params,
            timeout=aiohttp.ClientTimeout(total=60),
            allow_redirects=True,
        ) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            if resp.status == 404:
                return None
            logger.warning("BioPortal GET %s returned HTTP %s", url, resp.status)
            return None
    except asyncio.TimeoutError:
        logger.warning("BioPortal GET %s timed out", url)
        return None
    except Exception as e:
        logger.error("BioPortal GET %s error: %s", url, e)
        return None


async def _fetch_all_ontologies(session: aiohttp.ClientSession) -> List[Dict]:
    """Fetch the paginated list of all BioPortal ontologies."""
    all_onts = []
    url = f"{BIOPORTAL_API_URL}/ontologies"
    params = {"include": "name,acronym,description,links", "pagesize": 100, "page": 1}

    while url:
        data = await _get_json(session, url, params)
        if not data:
            break
        collection = data.get("collection", [])
        all_onts.extend(collection)
        # Follow nextPage link if present
        links = data.get("links", {})
        next_page = links.get("nextPage")
        if next_page and next_page != url:
            url = next_page
            params = {}  # URL already contains params
        else:
            break

    return all_onts


async def _fetch_download_url(
    session: aiohttp.ClientSession, acronym: str, semaphore: asyncio.Semaphore
) -> Optional[str]:
    """Fetch the download URL for the latest submission of an ontology."""
    async with semaphore:
        url = f"{BIOPORTAL_API_URL}/ontologies/{acronym}/latest_submission"
        data = await _get_json(
            session, url, {"include": "submissionStatus,download,version,released"}
        )
        if not data:
            return None
        download = data.get("download")
        if isinstance(download, str) and download.startswith("http"):
            return download
        return None


async def fetch_bioportal_ontologies(exclude_ids: Set[str]) -> List[Dict[str, Any]]:
    """
    Fetch ontology metadata from BioPortal.

    Args:
        exclude_ids: Set of lowercase ontology IDs already registered from
                     OBOFoundry. These will be skipped.

    Returns:
        List of ontology dicts with standardised fields.
    """
    logger.info("Fetching BioPortal ontology list (excluding %d OBO IDs)", len(exclude_ids))
    semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

    async with aiohttp.ClientSession() as session:
        raw_list = await _fetch_all_ontologies(session)
        if not raw_list:
            logger.warning("BioPortal returned an empty ontology list")
            return []

        logger.info("BioPortal: %d ontologies total before dedup", len(raw_list))

        # Filter out already-known OBO ontologies
        candidates = [
            o for o in raw_list
            if o.get("acronym", "").lower() not in exclude_ids
        ]
        logger.info("BioPortal: %d candidates after OBO dedup", len(candidates))

        # Fetch download URLs concurrently with rate limiting
        tasks = [
            _fetch_download_url(session, o["acronym"], semaphore)
            for o in candidates
        ]
        download_urls = await asyncio.gather(*tasks, return_exceptions=True)

        ontologies = []
        for ont, download_url in zip(candidates, download_urls):
            if isinstance(download_url, Exception) or not download_url:
                continue
            acronym = ont.get("acronym", "")
            ont_id = acronym.lower()
            ontologies.append(
                {
                    "ontology_id": ont_id,
                    "name": ont.get("name", acronym),
                    "description": ont.get("description", ""),
                    "source": "bioportal",
                    "source_url": download_url,
                    "homepage": f"https://bioportal.bioontology.org/ontologies/{acronym}",
                    "license": "",
                }
            )

    logger.info("BioPortal: %d ontologies with download URLs", len(ontologies))
    return ontologies
