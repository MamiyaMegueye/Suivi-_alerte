"""
Référence = plus petite valeur entre la médiane et le cycle précédent.
Base DuckDB JETABLE.   .venv\\Scripts\\python.exe -m tests.test_reference_min

Secteur 501 (cas Borne Fontaine 8 de SEBKHA) : abonné 7001 facture 40, 40,
puis 20 en 09/2026 et 21 en 11/2026. Médiane 40, mais N-2 = 20 :
référence 20, pas de baisse (+1).
Secteur 502 (cas du pic, abonné 15196540) : 29, 29, 225 puis 17.
Référence 29 (médiane, plus petite que 225) : baisse -12.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp()) / "test_refmin.duckdb"
os.environ["BAISSE_DB"] = str(_TMP)
os.environ["WATCHER_ACTIF"] = "0"

from src import config, db, queries  # noqa: E402

assert Path(config.DB_PATH) == _TMP
ok = True


def verifier(nom, cond, detail=""):
    global ok
    print(("  OK    " if cond else "  ÉCHEC ") + nom
          + (f"  ({detail})" if detail and not cond else ""))
    ok &= bool(cond)


def main():
    print(f"Base de test : {_TMP}\n")
    db.initialiser()
    db.executer("INSERT INTO ref_centre VALUES (10, 'CENTRE TEST', 2)")
    fid = [0]
    for sect, abn, vols in ((501, 7001, (40, 40, 20, 21)), (502, 7002, (29, 29, 225, 17))):
        db.executer("INSERT INTO ref_secteur VALUES (?, ?, ?, 10, '1')", [sect, f"S{sect}", str(sect)])
        for m, v in zip((5, 7, 9, 11), vols):
            fid[0] += 1
            db.executer(
                "INSERT INTO raw_facture_m (FACT_ID, FACT_NUM, STR_ID, SECT_ID, ABN_ID, "
                "FACT_ANNEE, FACT_PERIODE, TYPE_FACT, FAT_QTE_CONS, FLAGANNULLE) "
                "VALUES (?, ?, 10, ?, ?, 2026, ?, 0, ?, 0)", [fid[0], fid[0], sect, abn, m, v])
        for m in (9, 11):
            fid[0] += 1
            db.executer(
                "INSERT INTO raw_cloture_secteur (IDCLOTURE, STR_ID, SECT_ID, ANNEE, MOIS, "
                "NBABO, NBTRAITER, POURCTRAITER, VOLUMEFACT, VOLUMESTIM, CLOTURE) "
                "VALUES (?, 10, ?, 2026, ?, 1, 1, 100, 0, 0, 1)", [fid[0], sect, m])

    s = {x["secteur_code"]: x for x in queries.secteurs(2026, 11, seulement_baisses=False)}
    a, b = s.get(501, {}), s.get(502, {})
    verifier("501 : référence 20 (et non la médiane 40), écart +1",
             (a.get("vol_p"), a.get("ecart")) == (20, 1), f"{a.get('vol_p')} / {a.get('ecart')}")
    verifier("501 : n'apparaît pas dans les baisses",
             501 not in {x["secteur_code"] for x in queries.secteurs(2026, 11)})
    verifier("502 : pic neutralisé, référence 29, écart -12",
             (b.get("vol_p"), b.get("ecart")) == (29, -12), f"{b.get('vol_p')} / {b.get('ecart')}")
    d = {x["reference"]: x for x in queries.abonnes(2026, 11, 10, 501)["lignes"]}
    verifier("détail 7001 : référence 20, écart +1",
             (d.get(7001, {}).get("vol_p"), d.get(7001, {}).get("ecart")) == (20, 1), str(d.get(7001)))
    db.fermer()
    print("\nRÉSULTAT :", "TOUT EST OK" if ok else "AU MOINS UN ÉCHEC")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
