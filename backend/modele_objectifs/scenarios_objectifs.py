"""
COMPARAISON DES 8 SCÉNARIOS D'OBJECTIFS
=======================================
Lancement :  python scenarios_objectifs.py
Sortie    :  terminal + sorties/scenarios_objectifs_AAAA-MM.xlsx (ne modifie pas l'application)

Trois choix, deux options chacun (2 x 2 x 2 = 8 scénarios) :
  1. Pic 2026     : BRUT (meilleur mois réel) ou HORS EXCEPTIONNELS (mois > 2,5 x la médiane du
                    centre ramené à ce seuil)
  2. Règle du DG  : MINIMUM  -> objectif = max(1,2 x Pic ; Potentiel)
                    ENTRE    -> objectif = Potentiel, borné entre le niveau actuel (moyenne 12 mois)
                                et 1,2 x Pic  (les critères décident de la position du centre)
  3. Référence    : MÉDIANE  -> potentiel = niveau d'un centre typique de sa taille
                    TOP 25 % -> potentiel = niveau des 25 % de centres les plus performants

Potentiel d'un centre (par groupe : Nouakchott + Nouadhibou / Intérieur)
  - régression des encaissements moyens sur les 5 critères (abonnés actifs, gros consommateurs
    actifs, CA hors GC, CA GC, volume produit pour l'intérieur) -> « encaissement attendu »
  - performance du centre = encaissement réel / encaissement attendu
  - Potentiel = encaissement attendu x performance de référence (médiane ou 75e centile du groupe)
  Les poids des critères (décomposition de Shapley) sont affichés pour expliquer le potentiel.
"""
import itertools
import math
import os
import sys

import numpy as np
import pandas as pd

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import parametres as C  # noqa: E402

D_IN = os.path.join(ICI, getattr(C, "DOSSIER_DONNEES", "donnees"))
D_OUT = os.path.join(ICI, getattr(C, "DOSSIER_SORTIES", "sorties"))
GROUPES = {"Nouakchott + Nouadhibou": ["NKTT", "NDB"], "Intérieur": ["INTERIEUR"]}
TARIFS_GC = [str(t) for t in getattr(C, "TARIFS_GC", ["2220", "1110", "4440"])]
COEF = getattr(C, "COEF_PLAFOND", 1.2)
ANNEE = str(getattr(C, "ANNEE_PIC", 2026))
SEUIL_EXC = getattr(C, "SEUIL_EXCEPTIONNEL", 2.5) or 2.5
LIB = {"nb_abonnes": "Abonnés actifs", "nb_gc": "Gros consommateurs actifs", "ca_dom": "CA hors GC",
       "ca_gc": "CA gros consommateurs", "volume": "Volume produit"}
COL_ATT = "Mois 2026 où l'objectif aurait été atteint (%)"
EXEMPLES = ["91", "92", "66", "39", "59"]
ALERTES = []


def lire(nom):
    return pd.read_csv(os.path.join(D_IN, nom), sep=";", encoding="utf-8-sig",
                       dtype={"centre": str, "groupe_tarif": str})


def decaler(m, n):
    a, mm = map(int, m.split("-"))
    k = a * 12 + mm - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


# -------------------------------------------------------------
# Données par centre
# -------------------------------------------------------------
def donnees():
    fin = getattr(C, "MOIS_FIN")
    mois12 = [decaler(fin, -k) for k in range(11, -1, -1)]
    e = lire(C.FICHIERS["encaissements"])
    e["montant"] = pd.to_numeric(e["montant"], errors="coerce")
    E = e.pivot_table(index="centre", columns="mois", values="montant", aggfunc="sum")
    E12 = E.reindex(columns=mois12)
    m26 = [m for m in E.columns if m.startswith(ANNEE) and m <= fin]
    z = lire(C.FICHIERS["zones"]).set_index("centre")
    d = pd.DataFrame(index=sorted(set(E.index) & set(z.index), key=lambda c: int(c) if c.isdigit() else 0))
    d["nom_centre"] = z["nom_centre"]
    d["groupe"] = z["zone"].map(lambda v: next(g for g, zs in GROUPES.items() if v in zs))
    d["moyenne_12_mois"] = E12.mean(axis=1)
    d["mediane_12_mois"] = E12.median(axis=1)
    d["pic_brut"] = E[m26].max(axis=1)
    d["mois_pic_brut"] = E[m26].idxmax(axis=1)
    lim = SEUIL_EXC * d["mediane_12_mois"]
    d["pic_hors_exceptionnels"] = E[m26].clip(upper=lim, axis=0).max(axis=1)
    d["pic_exceptionnel"] = np.where(d["pic_brut"] > lim, "oui", "")
    d["mois_2026"] = len(m26)
    d["_E26"] = [E.loc[c, m26].values for c in d.index]

    # Facturation : 12 derniers mois, mois incomplets / avoirs / rattrapages traités
    f = lire(C.FICHIERS["facturation"])
    f["groupe_tarif"] = f["groupe_tarif"].astype(str).str.replace(r"\.0$", "", regex=True)
    f["ca"] = pd.to_numeric(f["ca"], errors="coerce")
    f["gc"] = f["groupe_tarif"].isin(TARIFS_GC)
    ca = {k: f[f["gc"] == v].pivot_table(index="centre", columns="mois", values="ca", aggfunc="sum")
          for k, v in (("ca_gc", True), ("ca_dom", False))}
    tot = (ca["ca_gc"].fillna(0).add(ca["ca_dom"].fillna(0), fill_value=0)).sum()
    incomplets = list(tot.index[tot < 0.5 * tot.median()])
    if incomplets:
        ALERTES.append(f"Mois de facturation incomplets exclus : {', '.join(incomplets)}")
    for k, x in ca.items():
        x = x.reindex(columns=[m for m in mois12 if m in x.columns]).copy()
        x[[m for m in incomplets if m in x.columns]] = np.nan
        x[x < 0] = np.nan
        med = x.where(x > 0).median(axis=1)
        x = x.clip(upper=(3 * med).where(med > 0), axis=0)
        d[k] = x.mean(axis=1).reindex(d.index).fillna(0.0)
    d["ca_total"] = d["ca_dom"] + d["ca_gc"]

    a = lire(C.FICHIERS["abonnes"]).set_index("centre")
    d["nb_abonnes"] = pd.to_numeric(a["nb_abonnes_actifs"], errors="coerce").reindex(d.index)
    d["nb_gc"] = pd.to_numeric(a.get("nb_gc_actifs", 0), errors="coerce").reindex(d.index).fillna(0)

    d["volume"] = 0.0
    d["volume_statut"] = ""
    try:
        p = pd.read_csv(os.path.join(D_IN, C.FICHIERS["production"]), sep=";", dtype={"centre": str})
        p["volume"] = pd.to_numeric(p["volume"], errors="coerce")
        v = p.groupby("centre")["volume"].mean()
        inter = d["groupe"] == "Intérieur"
        d.loc[inter, "volume"] = v.reindex(d.index[inter])
        d.loc[inter, "volume_statut"] = "réel"
        manq = inter & d["volume"].isna()
        if manq.any():
            r = (d.loc[inter & ~manq, "volume"] / d.loc[inter & ~manq, "nb_abonnes"]).median()
            d.loc[manq, "volume"] = r * d.loc[manq, "nb_abonnes"]
            d.loc[manq, "volume_statut"] = f"manquant, estimé ({r:.1f} m³/abonné)"
            ALERTES.append(f"Volume produit manquant, estimé : {', '.join(d.loc[manq, 'nom_centre'])}")
    except FileNotFoundError:
        ALERTES.append("production.csv absent : volume non utilisé")
    return d


# -------------------------------------------------------------
# Potentiel (régression + performance de référence) et poids de Shapley
# -------------------------------------------------------------
def ols(y, X):
    A = np.column_stack([np.ones(len(y)), X])
    b = np.linalg.lstsq(A, y, rcond=None)[0]
    return A @ b, 1 - ((y - A @ b) ** 2).sum() / ((y - y.mean()) ** 2).sum()


def shapley(d, crit):
    y = d["moyenne_12_mois"].values
    val = {S: (ols(y, d[list(S)].values)[1] if S else 0.0)
           for r in range(len(crit) + 1) for S in itertools.combinations(crit, r)}
    n = len(crit)
    phi = {}
    for j in crit:
        autres = [x for x in crit if x != j]
        s = 0.0
        for r in range(n):
            for S in itertools.combinations(autres, r):
                avec = tuple(x for x in crit if x in S or x == j)
                sans = tuple(x for x in crit if x in S)
                s += math.factorial(r) * math.factorial(n - r - 1) / math.factorial(n) * (val[avec] - val[sans])
        phi[j] = max(s, 0)
    t = sum(phi.values())
    return {j: phi[j] / t for j in crit}, val[tuple(crit)]


def potentiels(d):
    lignes = []
    for g in GROUPES:
        crit = ["nb_abonnes", "nb_gc", "ca_dom", "ca_gc"] + (["volume"] if g == "Intérieur" else [])
        m = d["groupe"] == g
        x = d[m]
        attendu, r2 = ols(x["moyenne_12_mois"].values, x[crit].values)
        # Très petits centres : la droite peut passer sous leur niveau réel ; plancher = moitié du réel
        attendu = np.maximum(attendu, 0.5 * x["moyenne_12_mois"].values)
        d.loc[m, "encaissement_attendu"] = attendu
        d.loc[m, "performance"] = x["moyenne_12_mois"].values / attendu
        w, _ = shapley(x, crit)
        for k in crit:
            lignes.append({"Groupe": g, "Critère": LIB[k], "Poids (%)": round(100 * w[k], 1), "R²": round(r2, 3)})
    for g in GROUPES:
        m = d["groupe"] == g
        d.loc[m, "perf_ref_mediane"] = d.loc[m, "performance"].median()
        d.loc[m, "perf_ref_top25"] = d.loc[m, "performance"].quantile(0.75)
    d["potentiel_mediane"] = d["encaissement_attendu"] * d["perf_ref_mediane"]
    d["potentiel_top25"] = d["encaissement_attendu"] * d["perf_ref_top25"]
    return pd.DataFrame(lignes)


# -------------------------------------------------------------
# Scénarios
# -------------------------------------------------------------
SCENARIOS = [(pic, regle, ref) for pic in ("brut", "hors exceptionnels")
             for regle in ("MINIMUM", "ENTRE") for ref in ("médiane", "top 25 %")]


def code(s):
    pic, regle, ref = s
    return f"{'S' + str(SCENARIOS.index(s) + 1)}"


def libelle(s):
    pic, regle, ref = s
    return f"Pic {pic} | règle {regle} | réf. {ref}"


def calculer(d, s):
    pic, regle, ref = s
    seuil = COEF * (d["pic_brut"] if pic == "brut" else d["pic_hors_exceptionnels"])
    pot = d["potentiel_mediane"] if ref == "médiane" else d["potentiel_top25"]
    actuel = d["moyenne_12_mois"]
    if regle == "MINIMUM":
        obj = np.maximum(seuil, pot)
        cause = np.where(pot > seuil, "potentiel (critères)", f"{COEF:g} x Pic")
    else:
        obj = np.clip(pot, actuel, np.maximum(seuil, actuel))
        cause = np.where(pot >= seuil, f"plafond {COEF:g} x Pic",
                         np.where(pot <= actuel, "niveau actuel (plancher)", "potentiel (critères)"))
    obj = np.asarray(obj, float)
    atteinte = np.array([np.mean(v >= o) for v, o in zip(d["_E26"], obj)])
    return obj, cause, atteinte


def main():
    fin = getattr(C, "MOIS_FIN")
    mois_obj = decaler(fin, 1)
    print("=" * 78)
    print(f"  COMPARAISON DES 8 SCÉNARIOS D'OBJECTIFS — {mois_obj}")
    print("=" * 78)
    d = donnees()
    poids = potentiels(d)
    print("\n  Poids des critères (Shapley) — expliquent l'encaissement attendu de chaque centre")
    for g in GROUPES:
        x = poids[poids["Groupe"] == g]
        print(f"   {g} (R² {x['R²'].iloc[0]:.2f}) : "
              + ", ".join(f"{r['Critère']} {r['Poids (%)']:.0f} %" for _, r in x.iterrows()))
    print("\n  Performance de référence (encaissement réel / attendu) :")
    for g in GROUPES:
        x = d[d["groupe"] == g]
        print(f"   {g} : médiane {x['perf_ref_mediane'].iloc[0]:.2f} | top 25 % {x['perf_ref_top25'].iloc[0]:.2f}")

    resume, tables = [], {}
    actuel = d["moyenne_12_mois"].sum()
    for s in SCENARIOS:
        obj, cause, att = calculer(d, s)
        t = d[["nom_centre", "groupe", "moyenne_12_mois", "pic_brut", "pic_hors_exceptionnels",
               "encaissement_attendu", "performance"]].copy()
        t["potentiel"] = d["potentiel_mediane"] if s[2] == "médiane" else d["potentiel_top25"]
        t["objectif"] = obj
        t["progression_pct"] = 100 * (obj / d["moyenne_12_mois"] - 1)
        t["objectif_fixe_par"] = cause
        t["mois_2026_atteints_pct"] = 100 * att
        tables[s] = t
        resume.append({
            "Code": code(s), "Scénario": libelle(s), "Total objectifs": obj.sum(),
            "vs encaissement actuel (%)": 100 * (obj.sum() / actuel - 1),
            "Progression médiane par centre (%)": np.median(t["progression_pct"]),
            "Progression maximale (%)": t["progression_pct"].max(),
            "Centre le plus poussé": d.loc[t["progression_pct"].idxmax(), "nom_centre"],
            "Centres fixés par 1,2 x Pic": int(pd.Series(cause).str.contains("Pic").sum()),
            "Centres fixés par les critères": int((pd.Series(cause) == "potentiel (critères)").sum()),
            "Centres au niveau actuel": int((pd.Series(cause) == "niveau actuel (plancher)").sum()),
            COL_ATT: 100 * np.mean(np.concatenate(
                [v >= o for v, o in zip(d["_E26"], obj)])),
            **{f"{d.loc[c, 'nom_centre']}": obj[d.index.get_loc(c)] for c in EXEMPLES if c in d.index},
        })
    resume = pd.DataFrame(resume)

    print(f"\n  Encaissement actuel (moyenne des 12 derniers mois) : {actuel:,.0f} MRU/mois\n")
    print(f"  {'':4}{'Scénario':<46}{'Total':>14}{'vs actuel':>11}{'Par Pic':>9}{'Critères':>9}"
          f"{'Actuel':>8}{'Atteint*':>10}")
    for _, r in resume.iterrows():
        print(f"  {r['Code']:<4}{r["Scénario"]:<56}{r['Total objectifs']:>14,.0f}"
              f"{r['vs encaissement actuel (%)']:>+10.0f}%{r['Centres fixés par 1,2 x Pic']:>9}"
              f"{r['Centres fixés par les critères']:>9}{r['Centres au niveau actuel']:>8}"
              f"{r[COL_ATT]:>9.0f}%")
    print("  * part des mois de 2026 où le centre a encaissé au moins son objectif")
    print("  Colonnes « Par Pic / Critères / Actuel » : nombre de centres dont l'objectif est fixé par cette règle")

    print("\n  EXEMPLES (objectif du mois)")
    noms = [d.loc[c, "nom_centre"] for c in EXEMPLES if c in d.index]
    print(f"  {'':4}" + "".join(f"{n[:13]:>15}" for n in noms))
    print(f"  {'Act.':<4}" + "".join(f"{d.loc[c, 'moyenne_12_mois']:>15,.0f}" for c in EXEMPLES if c in d.index))
    for _, r in resume.iterrows():
        print(f"  {r['Code']:<4}" + "".join(f"{r[n]:>15,.0f}" for n in noms))

    # Tableau complet par centre (tous les scénarios côte à côte)
    comp = d[["nom_centre", "groupe", "moyenne_12_mois", "pic_brut", "pic_hors_exceptionnels", "pic_exceptionnel",
              "encaissement_attendu", "performance", "potentiel_mediane", "potentiel_top25",
              "nb_abonnes", "nb_gc", "ca_dom", "ca_gc", "volume", "volume_statut"]].copy()
    for s in SCENARIOS:
        comp[f"{code(s)} objectif"] = tables[s]["objectif"]
    comp = comp.sort_values("moyenne_12_mois", ascending=False)

    os.makedirs(D_OUT, exist_ok=True)
    chemin = os.path.join(D_OUT, f"scenarios_objectifs_{mois_obj}.xlsx")
    lisez = pd.DataFrame({"Explications": [
        "8 scénarios = 2 choix de Pic x 2 sens de la règle du DG x 2 niveaux de référence.",
        f"MINIMUM : objectif = max({COEF:g} x Pic ; Potentiel). ENTRE : objectif = Potentiel, borné entre "
        f"la moyenne des 12 derniers mois et {COEF:g} x Pic.",
        "Potentiel = encaissement attendu (régression sur les 5 critères, par groupe) x performance de référence "
        "(médiane ou 75e centile du rapport réel / attendu dans le groupe).",
        f"Pic hors exceptionnels : mois de 2026 > {SEUIL_EXC:g} x la médiane du centre ramené à ce seuil.",
        "Colonne « mois 2026 atteints » : part des mois de 2026 où le centre a encaissé au moins l'objectif.",
        "Abonnés et gros consommateurs : effectifs actifs actuels ; CA : moyenne des 12 derniers mois "
        "(mois incomplets exclus, avoirs et rattrapages corrigés).",
        *ALERTES]})
    with pd.ExcelWriter(chemin, engine="openpyxl") as xw:
        lisez.to_excel(xw, sheet_name="Lisez-moi", index=False)
        resume.to_excel(xw, sheet_name="Comparaison", index=False)
        comp.reset_index(names="centre").to_excel(xw, sheet_name="Tous les scénarios", index=False)
        poids.to_excel(xw, sheet_name="Poids des critères", index=False)
        for s in SCENARIOS:
            tables[s].sort_values("objectif", ascending=False).reset_index(names="centre").to_excel(
                xw, sheet_name=code(s), index=False)
    print(f"\n  ✅ {chemin}")
    print("     onglets : Comparaison | Tous les scénarios (un objectif par scénario et par centre) | S1 … S8")
    for a in ALERTES:
        print(f"  ⚠ {a}")


if __name__ == "__main__":
    main()
