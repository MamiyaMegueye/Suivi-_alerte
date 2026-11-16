"""
Lance le calcul des objectifs.

    python lancer.py                    extraction (Oracle + DuckDB) puis modèle
    python lancer.py --sans-extraction  modèle seul, sur les CSV déjà extraits
"""
import sys

import extraction
import modele_objectifs

if __name__ == "__main__":
    if "--sans-extraction" not in sys.argv:
        extraction.main()
    modele_objectifs.main()
