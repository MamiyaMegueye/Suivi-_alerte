"""
Convertit le fichier de production de la DC (ex. Prod_01_au_08.xlsx) en donnees/production.csv.

    python convertir_production.py "C:\\chemin\\Prod_01_au_08.xlsx"

Format attendu : pour chaque zone, une ligne d'en-tête « EXP | CENTRE | mois... | MOYENNE »,
puis une ligne par centre (code EXP, nom, volume produit en m3 par mois).
Si production.csv existe déjà, les mois du nouveau fichier remplacent les mêmes mois
et les autres mois sont gardés : on peut ajouter un fichier chaque mois.
"""
import os
import sys
from datetime import datetime

import pandas as pd

import parametres as C

ICI = os.path.dirname(os.path.abspath(__file__))
SORTIE = os.path.join(ICI, C.DOSSIER_DONNEES, C.FICHIERS["production"])


def lire_fichier_dc(chemin):
    brut = pd.read_excel(chemin, header=None)
    lignes, mois_cols = [], None
    for _, r in brut.iterrows():
        valeurs = list(r.values)
        if any(str(v).strip().upper() == "EXP" for v in valeurs):
            mois_cols = {}
            for j, v in enumerate(valeurs):
                if isinstance(v, (datetime, pd.Timestamp)):
                    mois_cols[j] = pd.Timestamp(v).strftime("%Y-%m")
            continue
        if not mois_cols:
            continue
        code = str(valeurs[1]).strip().replace(".0", "")
        if not code.isdigit():
            continue
        for j, mois in mois_cols.items():
            v = pd.to_numeric(valeurs[j], errors="coerce")
            if pd.notna(v):
                lignes.append((code, mois, float(v)))
    return pd.DataFrame(lignes, columns=["centre", "mois", "volume"])


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage : python convertir_production.py "chemin\\vers\\Prod.xlsx"')
    nouveau = lire_fichier_dc(sys.argv[1])
    if nouveau.empty:
        sys.exit("❌ Aucune donnée de production trouvée dans ce fichier.")
    if os.path.exists(SORTIE):
        ancien = pd.read_csv(SORTIE, sep=";", dtype={"centre": str})
        ancien = ancien[~ancien["mois"].isin(nouveau["mois"].unique())]
        nouveau = pd.concat([ancien, nouveau], ignore_index=True)
    nouveau = nouveau.groupby(["centre", "mois"], as_index=False)["volume"].sum()
    nouveau.sort_values(["centre", "mois"]).to_csv(SORTIE, sep=";", index=False, encoding="utf-8-sig")
    print(f"✅ {SORTIE} : {nouveau['centre'].nunique()} centres, mois "
          f"{nouveau['mois'].min()} → {nouveau['mois'].max()}, {len(nouveau)} lignes")


if __name__ == "__main__":
    main()
