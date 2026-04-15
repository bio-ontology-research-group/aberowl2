#!/usr/bin/env python3
"""
Download additional ontologies from known direct URLs.
These are ontologies commonly found on BioPortal but available from
their original sources without authentication.
"""

import subprocess
import sys
from pathlib import Path

# Direct download URLs for ontologies not yet downloaded
EXTRA_ONTOLOGIES = {
    "edam": "http://edamontology.org/EDAM.owl",
    "sio": "https://raw.githubusercontent.com/MaastrichtU-IDS/semanticscience/master/ontology/sio.owl",
    "bao": "http://www.bioassayontology.org/bao/bao_complete.owl",
    "clo": "https://purl.obolibrary.org/obo/clo.owl",
    "provo": "http://www.w3.org/ns/prov-o",
    "gfo": "https://raw.githubusercontent.com/Onto-Med/GFO/main/gfo-basic.owl",
    "kisao": "https://raw.githubusercontent.com/SED-ML/KiSAO/deploy/kisao.owl",
    "teddy": "https://raw.githubusercontent.com/COMBINE-org/TEDDY/master/TEDDY.owl",
    "ido": "https://purl.obolibrary.org/obo/ido.owl",
    "vo": "https://purl.obolibrary.org/obo/vo.owl",
    "ico": "https://purl.obolibrary.org/obo/ico.owl",
    "ncro": "https://purl.obolibrary.org/obo/ncro.owl",
    "ogg": "https://purl.obolibrary.org/obo/ogg.owl",
    "txpo": "https://purl.obolibrary.org/obo/txpo.owl",
    "cdao": "https://purl.obolibrary.org/obo/cdao.owl",
    "mfoem": "https://purl.obolibrary.org/obo/mfoem.owl",
    "opmi": "https://purl.obolibrary.org/obo/opmi.owl",
    "vto": "https://purl.obolibrary.org/obo/vto.owl",
    "to": "https://purl.obolibrary.org/obo/to.owl",
    "pw": "https://purl.obolibrary.org/obo/pw.owl",
    "flopo": "https://purl.obolibrary.org/obo/flopo.owl",
    "ohmi": "https://purl.obolibrary.org/obo/ohmi.owl",
    "oae": "https://purl.obolibrary.org/obo/oae.owl",
    "foodon": "https://purl.obolibrary.org/obo/foodon.owl",
    "ecto": "https://purl.obolibrary.org/obo/ecto.owl",
    "maxo": "https://purl.obolibrary.org/obo/maxo.owl",
    "ncbitaxon": "https://purl.obolibrary.org/obo/ncbitaxon.owl",
    "bto": "https://purl.obolibrary.org/obo/bto.owl",
    "mpath": "https://purl.obolibrary.org/obo/mpath.owl",
    "hancestro": "https://purl.obolibrary.org/obo/hancestro.owl",
    "eupath": "https://purl.obolibrary.org/obo/eupath.owl",
    "apollo_sv": "https://purl.obolibrary.org/obo/apollo_sv.owl",
    "mcro": "https://purl.obolibrary.org/obo/mcro.owl",
    "phipo": "https://purl.obolibrary.org/obo/phipo.owl",
    "symp": "https://purl.obolibrary.org/obo/symp.owl",
    "trans": "https://purl.obolibrary.org/obo/trans.owl",
    "exo": "https://purl.obolibrary.org/obo/exo.owl",
    "mmo": "https://purl.obolibrary.org/obo/mmo.owl",
    "cmo": "https://purl.obolibrary.org/obo/cmo.owl",
    "mi": "https://purl.obolibrary.org/obo/mi.owl",
    "ms": "https://purl.obolibrary.org/obo/ms.owl",
    "uo": "https://purl.obolibrary.org/obo/uo.owl",
    "go-plus": "https://purl.obolibrary.org/obo/go/extensions/go-plus.owl",
}


def main():
    dest_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/data/aberowl/ontologies")

    existing = set()
    for d in dest_dir.iterdir():
        if d.is_dir():
            owl = d / f"{d.name}.owl"
            if owl.exists() and owl.stat().st_size > 1000:
                existing.add(d.name)

    to_download = {k: v for k, v in EXTRA_ONTOLOGIES.items() if k not in existing}
    print(f"Already have {len(existing)} ontologies")
    print(f"Will download {len(to_download)} additional ontologies")
    print("=" * 60)

    ok, failed = 0, 0
    for ont_id, url in sorted(to_download.items()):
        ont_dir = dest_dir / ont_id
        ont_dir.mkdir(parents=True, exist_ok=True)
        owl_path = ont_dir / f"{ont_id}.owl"

        print(f"  {ont_id:15s} ...", end="", flush=True)
        try:
            result = subprocess.run(
                ["curl", "-fSL", "--max-time", "300", "-o", str(owl_path), url],
                capture_output=True, text=True, timeout=320,
            )
            if result.returncode == 0 and owl_path.exists() and owl_path.stat().st_size > 100:
                size_mb = owl_path.stat().st_size / 1024 / 1024
                print(f" {size_mb:.1f} MB")
                ok += 1
            else:
                owl_path.unlink(missing_ok=True)
                print(f" FAILED")
                failed += 1
        except Exception as e:
            owl_path.unlink(missing_ok=True)
            print(f" ERROR: {str(e)[:50]}")
            failed += 1

    print()
    print(f"Downloaded: {ok}, Failed: {failed}")
    total = len(existing) + ok
    print(f"Total ontologies available: {total}")


if __name__ == "__main__":
    main()
