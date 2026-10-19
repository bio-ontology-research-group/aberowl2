"""
BioPortal ontology metadata fetcher.

Uses the BioPortal REST API to enumerate all available ontologies and their
download URLs. Ontologies already registered from OBOFoundry are skipped.
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit, quote

import aiohttp

logger = logging.getLogger(__name__)

BIOPORTAL_API_URL = "https://data.bioontology.org"
# Credentials must be supplied by the operator; never bundle a fallback key.
BIOPORTAL_API_KEY = os.getenv("BIOPORTAL_API_KEY", "")

# Max concurrent requests to BioPortal to avoid rate limiting
_CONCURRENCY_LIMIT = 5


async def _get_json(
    session: aiohttp.ClientSession, url: str, params: Optional[Dict] = None
) -> Optional[Any]:
    """GET a BioPortal API endpoint and return parsed JSON, or None on error."""
    if not BIOPORTAL_API_KEY:
        logger.warning("BioPortal access skipped: BIOPORTAL_API_KEY is not configured")
        return None
    if not _api_url(url):
        logger.warning("BioPortal rejected an invalid API navigation URL")
        return None
    base_params = {}
    if params:
        base_params.update(params)
    try:
        async with session.get(
            url,
            params=base_params,
            headers={"Authorization": f"apikey token={BIOPORTAL_API_KEY}"},
            timeout=aiohttp.ClientTimeout(total=60),
            allow_redirects=False,
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
        logger.error("BioPortal GET failed (%s)", type(e).__name__)
        return None


def _api_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in ("http", "https") and parsed.hostname == "data.bioontology.org"
                and parsed.port in (None, 80, 443) and not parsed.username
                and not parsed.password and not parsed.query and not parsed.fragment):
            return "https://data.bioontology.org" + parsed.path
    except ValueError:
        pass
    return None


def _download_link(data):
    if not isinstance(data, dict):
        return None
    links = data.get("links") or {}
    return _api_url(links.get("download")) or _api_url(data.get("download"))


async def _fetch_all_ontologies(session: aiohttp.ClientSession, report=None) -> List[Dict]:
    """Accept the ontology array and paged collection responses."""
    all_onts = []
    url = f"{BIOPORTAL_API_URL}/ontologies"
    params = {"include": "name,acronym,description", "display_links": "true"}
    seen = set()
    while url:
        marker = (url, tuple(sorted(params.items())))
        if marker in seen:
            if report is not None:
                report["state"] = "pagination_cycle"
            return []
        seen.add(marker)
        data = await _get_json(session, url, params)
        if isinstance(data, list):
            all_onts.extend(o for o in data if isinstance(o, dict))
            break
        if not isinstance(data, dict) or not isinstance(data.get("collection"), list):
            if report is not None:
                report["state"] = "unavailable_or_invalid_response"
            return []
        all_onts.extend(o for o in data["collection"] if isinstance(o, dict))
        next_page = (data.get("links") or {}).get("nextPage")
        if not next_page:
            break
        # Validate the origin before an authenticated request; preserve paging params.
        parsed = urlsplit(next_page) if isinstance(next_page, str) else None
        if not parsed or not _api_url(next_page.split("?", 1)[0]):
            if report is not None:
                report["state"] = "invalid_navigation_link"
            return []
        from urllib.parse import parse_qsl
        url = _api_url(next_page.split("?", 1)[0])
        params = dict(parse_qsl(parsed.query))
    if report is not None:
        report.update(state="ok", discovered=len(all_onts))
    return all_onts


async def _fetch_download_url(
    session: aiohttp.ClientSession, acronym: str, semaphore: asyncio.Semaphore
) -> Optional[str]:
    async with semaphore:
        url = f"{BIOPORTAL_API_URL}/ontologies/{quote(acronym, safe='')}/latest_submission"
        data = await _get_json(session, url, {"include": "version,released", "display_links": "true"})
        return _download_link(data)


async def fetch_bioportal_metadata() -> List[Dict[str, Any]]:
    """Fetch lightweight metadata (name, description, homepage) for every
    BioPortal ontology, WITHOUT resolving per-ontology download URLs.

    Used to backfill registry metadata (title/description/homepage) for
    ontologies that are not in OBO Foundry. Returns an empty list if BioPortal
    is unreachable or the API key is invalid.
    """
    async with aiohttp.ClientSession() as session:
        raw_list = await _fetch_all_ontologies(session)

    records: List[Dict[str, Any]] = []
    for o in raw_list:
        acronym = o.get("acronym", "")
        if not acronym:
            continue
        records.append(
            {
                "ontology_id": acronym.lower(),
                "name": o.get("name", acronym),
                "description": o.get("description", ""),
                "homepage": f"https://bioportal.bioontology.org/ontologies/{acronym}",
                "license": "",
            }
        )
    logger.info("BioPortal: fetched metadata for %d ontologies", len(records))
    return records


async def fetch_bioportal_ontologies(exclude_ids: Set[str], *, include_ids=None, report=None) -> List[Dict[str, Any]]:
    """
    Fetch ontology metadata from BioPortal.

    Args:
        exclude_ids: Set of lowercase ontology IDs already registered from
                     OBOFoundry. These will be skipped.

    Returns:
        List of ontology dicts with standardised fields.
    """
    logger.info("Fetching BioPortal ontology list (excluding %d OBO IDs)", len(exclude_ids))
    if not BIOPORTAL_API_KEY:
        if report is not None:
            report.update(state="missing_credentials", credential_configured=False)
        return []
    if report is not None:
        report["credential_configured"] = True
    semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)

    async with aiohttp.ClientSession() as session:
        raw_list = await _fetch_all_ontologies(session, report)
        if not raw_list:
            logger.warning("BioPortal returned an empty ontology list")
            return []

        logger.info("BioPortal: %d ontologies total before dedup", len(raw_list))

        # Filter out already-known OBO ontologies
        candidates = [
            o for o in raw_list
            if isinstance(o.get("acronym"), str) and o["acronym"]
            and o["acronym"].lower() not in exclude_ids
            and (include_ids is None or o["acronym"].lower() in include_ids)
        ]
        logger.info("BioPortal: %d candidates after OBO dedup", len(candidates))

        # Fetch download URLs concurrently with rate limiting
        async def resolve(o):
            return _download_link(o) or await _fetch_download_url(session, o["acronym"], semaphore)

        tasks = [resolve(o) for o in candidates]
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
