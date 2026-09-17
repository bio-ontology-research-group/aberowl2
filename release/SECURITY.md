# Credentials in the release

The exporter selects release files from the explicit allowlist in `contents.json`.
Private environment files, local keys, logs and `.git` must not be included. Run the
redacted check before export:

```bash
python3 release/check_credentials.py
python3 tests/test_release_credentials.py -v
```

The check reports paths, line numbers and categories only. It uses known token
patterns and source checks; passing it is not proof that arbitrary secrets are
absent. The clean-export validation repeats this check on the selected files.

Set `BIOPORTAL_API_KEY` in the operator's environment. `scripts/fleet_report.py` also
accepts the legacy `BIOPORTAL_APIKEY` variable. Without a key, the code skips
BioPortal requests or returns a configuration error; public OBO downloads remain
available. Existing valid downloads remain reusable. Offline paper reproduction does
not require credentials. BioPortal requests carry authentication in headers. The
changed download failure paths redact the configured key or report only the error
type. Do not enable shell tracing or publish process listings containing curl
arguments.

Earlier Git revisions and previous copies may contain embedded BioPortal
credentials. The current source reads credentials from the operator's environment.
Removing a key from source does not revoke it.

## File integrity

To verify the included files:

```bash
python3 release/checksums.py --check
```

`SHA256SUMS` covers every selected file except itself. It identifies the files in the
current working snapshot.
