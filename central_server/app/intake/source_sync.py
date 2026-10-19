"""Source metadata policy. Never downloads ontology products or schedules updates."""
from urllib.parse import urlsplit

SOURCE_FIELDS = frozenset({"name", "description", "source", "source_url", "homepage", "license", "contact"})
PRIORITY = {"manual": 3, "obofoundry": 2, "bioportal": 1}


def valid_source_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme in ("http", "https") and parsed.hostname
                    and not parsed.username and not parsed.password and not parsed.fragment)
    except ValueError:
        return False


def merge_source(existing, fields, ontology_id, overwrite=False):
    """Return whether metadata changed; retain all serving and pipeline state."""
    if existing.get("update_txn") or existing.get("transfer_release_pending"):
        return False
    if not valid_source_url(fields.get("source_url")):
        return False
    old_priority = PRIORITY.get(existing.get("source"), 0)
    new_priority = PRIORITY.get(fields.get("source"), 0)
    # Unlabelled operator URLs are also preserved; re-registration must not erase them.
    if not overwrite and ((existing.get("source_url") and not old_priority)
                          or old_priority > new_priority):
        return False
    changes = {k: v for k, v in fields.items() if k in SOURCE_FIELDS}
    before = dict(existing)
    if changes.get("source_url") != existing.get("source_url"):
        existing.pop("source_etag", None)
        existing.pop("source_last_modified", None)
    existing.update(changes)
    existing.setdefault("ontology_id", ontology_id)
    return existing != before
