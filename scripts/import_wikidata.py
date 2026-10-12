#!/usr/bin/env python3
"""Import/update the dish catalogue from Wikidata (CC0). Idempotent by QID.

    python scripts/import_wikidata.py [--no-enrich] [--images]
    docker compose exec plats python scripts/import_wikidata.py

Same code path as the "Lancer l'import" button in /admin/plats.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["import-wikidata", *sys.argv[1:]]))
