"""
MATRICES DE CORRÉLATION — encaissement (variable cible) et critères du modèle
=============================================================================
    python correlations.py

Utilise exactement les données préparées par votre modèle (modele_objectifs.py) :
mêmes 12 mois, mêmes nettoyages. Affiche dans le terminal de VS Code, pour chaque groupe :
  1. la corrélation ENTRE CENTRES (moyenne de chaque centre) : c'est ce qui fixe les poids ;
  2. la corrélation CENTRE x MOIS (toutes les observations) ;
puis une explication automatique. Écrit aussi sorties/correlations.xlsx (et une image si
matplotlib est installé).
"""
import contextlib
import io
import os
import sys

import numpy as np
import pandas as pd

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import modele_objectifs as M  # noqa: E402

os.system("")   # active les couleurs dans le terminal Windows
NOMS = {"encaissement": "Encaissement (cible)", "nb_gc": "Nb gros consommateurs",
        "ca_dom": "CA hors GC", "ca_gc": "CA GC", "volume_int": "Volume produit",
        "nb_abonnes": "Nb abonnés (retiré)"}
COURT = {"encaissement": "Encaiss.", "nb_gc": "Nb GC", "ca_dom": "CA horsGC", "ca_gc": "CA GC",
         "volume_int": "Volume", "nb_abonnes": "Abonnés"}


def couleur(r):
    """Fond coloré selon la force de la corrélation (bleu = positive, rouge = négative)."""
    if pd.isna(r):
        return "\033[0m"
    a = abs(r)
    if r < 0:
        fond = 224 if a < 0.4 else (210 if a < 0.7 else 196)
    else:
        fond = 255 if a < 0.2 else (153 if a < 0.4 else (117 if a < 0.6 else (75 if a < 0.8 else 27)))
    texte = 15 if (r >= 0.6 or r <= -0.7) else 16
    return f"\033[48;5;{fond}m\033[38;5;{texte}m"


def afficher(mat, titre):
    cols = list(mat.columns)
    print(f"\n  \033[1m{titre}\033[0m")
    print("  " + " " * 24 + "".join(f"{COURT[c]:>11}" for c in cols))
    for li in cols:
        ligne = f"  {NOMS[li]:<24}"
        for co in cols:
            r = mat.loc[li, co]
            ligne += f"{couleur(r)}{r:>10.2f} \033[0m"
        print(ligne)


def force(r):
    a = abs(r)
    return "très forte" if a >= 0.8 else "forte" if a >= 0.6 else "moyenne" if a >= 0.4 else "faible"


def expliquer(mat, groupe, poids):
    crit = [c for c in mat.columns if c != "encaissement"]
    print(f"\n  \033[1mLecture — {groupe}\033[0m")
    for c in sorted(crit, key=lambda k: -abs(mat.loc["encaissement", k])):
        r = mat.loc["encaissement", c]
        w = poids.get(c)
        txt_w = f" | poids dans le modèle : {w:.0f} %" if w is not None else ""
        print(f"   • {NOMS[c]:<22} r = {r:+.2f} ({force(r)}){txt_w}")
    paires = [(a, b, mat.loc[a, b]) for i, a in enumerate(crit) for b in crit[i + 1:] if abs(mat.loc[a, b]) >= 0.7]
    if paires:
        print("   Critères qui se ressemblent (corrélation ≥ 0,70 entre eux) :")
        for a, b, r in sorted(paires, key=lambda x: -abs(x[2])):
            print(f"     - {NOMS[a]} et {NOMS[b]} : r = {r:+.2f}")
        print("   → Quand deux critères disent la même chose, le modèle garde surtout celui qui explique le\n"
              "     mieux l'encaissement ; l'autre peut recevoir un poids faible, voire nul (colinéarité).")
    print("   Rappel : une corrélation mesure un lien deux à deux ; le POIDS mesure ce qu'un critère apporte\n"
          "   EN PLUS des autres. Un critère très corrélé à l'encaissement peut donc avoir un poids faible.")


def main():
    with contextlib.redirect_stdout(io.StringIO()):
        p = M.construire_panel()
        try:
            poids = M.estimer(p)[1]
        except Exception:
            poids = None
    print("=" * 78)
    print(f"  MATRICES DE CORRÉLATION — {p['mois'].min()} → {p['mois'].max()} "
          f"({p['centre'].nunique()} centres, {len(p)} observations)")
    print("=" * 78)
    print("  Couleurs : \033[48;5;27m\033[38;5;15m  ≥ 0,80  \033[0m \033[48;5;75m\033[38;5;15m 0,60–0,80 \033[0m "
          "\033[48;5;117m\033[38;5;16m 0,40–0,60 \033[0m \033[48;5;153m\033[38;5;16m 0,20–0,40 \033[0m "
          "\033[48;5;196m\033[38;5;15m négative \033[0m")
    feuilles = {}
    for g, zones in M.GROUPES.items():
        d = p[p["zone"].isin(zones)]
        v = ["encaissement", "nb_gc", "ca_dom", "ca_gc"] + (["volume_int"] if g == "Intérieur" else []) + ["nb_abonnes"]
        v = [c for c in v if c in d.columns and d[c].notna().any() and d[c].std() > 0]
        moy = d.groupby("centre")[v].mean()
        m_centres = moy.corr()
        m_obs = d[v].corr()
        print("\n" + "─" * 78)
        print(f"  {g.upper()}  ({d['centre'].nunique()} centres)")
        print("─" * 78)
        afficher(m_centres, "1. Entre centres (moyenne de chaque centre sur 12 mois)")
        afficher(m_obs, "2. Centre x mois (toutes les observations)")
        pw = {}
        if poids is not None:
            inv = {v_: k for k, v_ in M.CRITERES.items()}
            for _, l in poids[poids["groupe"] == g].iterrows():
                pw[inv.get(l["critere"], "")] = l["poids_pct"]
        expliquer(m_centres, g, pw)
        feuilles[f"{g} — entre centres"] = m_centres.rename(index=NOMS, columns=NOMS)
        feuilles[f"{g} — centre x mois"] = m_obs.rename(index=NOMS, columns=NOMS)

    chemin = os.path.join(ICI, M.C.DOSSIER_SORTIES, "correlations.xlsx")
    with pd.ExcelWriter(chemin, engine="openpyxl") as xw:
        for nom, m in feuilles.items():
            court = nom.replace("Nouakchott + Nouadhibou", "NKTT-NDB").replace(" — ", " ")
            m.round(2).to_excel(xw, sheet_name=court[:31])
        from openpyxl.formatting.rule import ColorScaleRule
        for ws in xw.book.worksheets:
            ws.conditional_formatting.add(f"B2:{chr(64 + ws.max_column)}{ws.max_row}",
                                          ColorScaleRule(start_type="num", start_value=-1, start_color="F8696B",
                                                         mid_type="num", mid_value=0, mid_color="FFFFFF",
                                                         end_type="num", end_value=1, end_color="1E3A8A"))
            ws.column_dimensions["A"].width = 26
            for k in range(2, ws.max_column + 1):
                ws.column_dimensions[chr(64 + k)].width = 16
    print(f"\n  ✅ {chemin}")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 2, figsize=(15, 13))
        for k, (ax, (nom, m)) in enumerate(zip(np.ravel(axes), feuilles.items())):
            im = ax.imshow(m.values, cmap="RdBu", vmin=-1, vmax=1)
            ax.set_xticks(range(len(m)), m.columns, rotation=40, ha="right", fontsize=9)
            ax.set_yticks(range(len(m)), m.index if k % 2 == 0 else [""] * len(m), fontsize=9)
            for i in range(len(m)):
                for j in range(len(m)):
                    ax.text(j, i, f"{m.values[i, j]:.2f}", ha="center", va="center", fontsize=10,
                            color="white" if abs(m.values[i, j]) > 0.6 else "black")
            ax.set_title(nom, fontsize=12, fontweight="bold", color="#0C2D5C")
        fig.suptitle("Corrélations entre l'encaissement et les critères", fontsize=15, fontweight="bold",
                     color="#0C2D5C")
        fig.tight_layout(rect=(0, 0, 0.92, 0.96))
        fig.colorbar(im, ax=axes, shrink=0.6)
        img = os.path.join(ICI, M.C.DOSSIER_SORTIES, "correlations.png")
        fig.savefig(img, dpi=130, bbox_inches="tight")
        print(f"  ✅ {img}  (ouvrez-la dans VS Code pour voir les matrices en couleur)")
    except ImportError:
        print("  (image non créée : matplotlib n'est pas installé — pip install matplotlib)")


if __name__ == "__main__":
    main()
