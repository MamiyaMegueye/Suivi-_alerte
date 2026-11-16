"""
MODÈLE MULTINIVEAU DE CALCUL DES OBJECTIFS PAR CENTRE
=====================================================

Modèle (une ligne = un centre x un mois) :

    Encaissement privés = a.CA_hors_GC + b.CA_GC + c.Nb_GC (+ d.Volume_intérieur)
                          + effet mois + effet zone + effet centre (aléatoire) + erreur

Estimation : modèle linéaire mixte à intercept aléatoire par centre (REML).

Objectif mensuel du centre :
    Potentiel = prédiction du modèle sans l'effet centre (gestion "moyenne")
    Base      = max(encaissement moyen réel, potentiel)
    Objectif privés = min(plafond, λ x Base)
        plafond = min(COEF_PLAFOND x Pic ANNEE_PIC, (1 + PROGRESSION_MAX) x moyenne)
        λ calculé pour que Σ objectifs privés + Σ objectifs État = cible nationale
    Objectif État   = médiane mensuelle des encaissements de l'État
    Objectif du mois = objectif moyen x indice saisonnier du mois (plafonné)
    Décomposition   = part courante (≤ CA moyen) + part arriérés

Exécution :  python modele_objectifs.py   (ou python lancer.py)
"""

import os
import shutil
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import optimize, stats

import parametres as C

warnings.filterwarnings("ignore")
ICI = os.path.dirname(os.path.abspath(__file__))
D_IN = os.path.join(ICI, C.DOSSIER_DONNEES)
D_OUT = os.path.join(ICI, C.DOSSIER_SORTIES)
os.makedirs(D_OUT, exist_ok=True)

ALERTES = []
PIC = {}            # centre -> {"pic": montant, "mois_pic": "AAAA-MM"} (année ANNEE_PIC)
EXCEPTIONNELS = []  # mois exceptionnels écrêtés
INFO = {}           # informations générales (segment modélisé, saisonnalité...)


def P(nom, defaut=None):
    """Paramètre de parametres.py, avec valeur par défaut."""
    return getattr(C, nom, defaut)


def alerte(msg):
    ALERTES.append(msg)
    print("  ⚠", msg)


# -------------------------------------------------------------
# 1. LECTURE ROBUSTE DES CSV
# -------------------------------------------------------------
def lire_csv(nom, obligatoire=True):
    chemin = os.path.join(D_IN, nom)
    if not os.path.exists(chemin):
        if obligatoire:
            sys.exit(f"❌ Fichier manquant : {chemin}")
        return None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(chemin, sep=None, engine="python", encoding=enc, dtype=str)
            break
        except UnicodeDecodeError:
            continue
    df.columns = [c.strip().strip('"').lower() for c in df.columns]
    return df


def num(s):
    """Convertit '17 449 511,10' / '17449511.1' en nombre."""
    s = s.astype(str).str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)
    both = s.str.contains(",") & s.str.contains(r"\.")
    s = np.where(both, s.str.replace(",", "", regex=False), s.str.replace(",", ".", regex=False))
    return pd.to_numeric(pd.Series(s), errors="coerce").fillna(0.0).values


def code_centre(s):
    return s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def mois_fmt(s):
    s = s.astype(str).str.strip()
    d = pd.to_datetime(s, errors="coerce", dayfirst=False)
    out = d.dt.strftime("%Y-%m")
    return out.fillna(s.str[:7])


def verifier_colonnes(df, cols, nom):
    manq = [c for c in cols if c not in df.columns]
    if manq:
        sys.exit(f"❌ {nom} : colonnes manquantes {manq}. Colonnes trouvées : {list(df.columns)}")


def decaler(mois, n):
    a, m = map(int, mois.split("-"))
    k = a * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


# -------------------------------------------------------------
# 2. CONSTRUCTION DU PANEL CENTRE x MOIS
# -------------------------------------------------------------
def lire_encaissements():
    """Encaissements par centre et par mois : série modélisée (privés ou tous) et État."""
    enc = lire_csv(C.FICHIERS["encaissements"])
    verifier_colonnes(enc, ["centre", "mois", "montant"], "encaissements.csv")
    enc["centre"] = code_centre(enc["centre"])
    enc["mois"] = mois_fmt(enc["mois"])
    for c in ["montant", "montant_prives", "montant_etat"]:
        if c in enc.columns:
            enc[c] = num(enc[c])
    e = enc.groupby(["centre", "mois"], as_index=False).sum(numeric_only=True)

    if P("MODELISER_PRIVES", True):
        if "montant_prives" in e.columns:
            e["encaissement"] = e["montant_prives"]
            e["encaissement_etat"] = e["montant"] - e["montant_prives"]
            INFO["segment"] = ("Privés seulement (administrations exclues)" if P("EXCLURE_ADMINISTRATIONS", False)
                               else "Privés (l'État a un objectif à part)")
        else:
            alerte("encaissements.csv ne sépare pas privés et État : relancez l'extraction "
                   "(python lancer.py). Le modèle utilise tous les encaissements en attendant.")
            e["encaissement"] = e["montant"]
            e["encaissement_etat"] = 0.0
            INFO["segment"] = "Tous (privés + État)"
    else:
        e["encaissement"] = e["montant"]
        e["encaissement_etat"] = 0.0
        INFO["segment"] = "Tous (privés + État)"
    e["encaissement_brut"] = e["encaissement"]
    return e[["centre", "mois", "encaissement", "encaissement_brut", "encaissement_etat"]]


def ecreter_exceptionnels(e):
    """Mois exceptionnels : un mois > SEUIL_EXCEPTIONNEL x la médiane du centre est ramené à ce seuil."""
    seuil = P("SEUIL_EXCEPTIONNEL", 2.5)
    if not seuil:
        return e
    zone = e[(e["mois"] >= C.MOIS_DEBUT_ENCAISSEMENTS) & (e["mois"] <= C.MOIS_FIN)]
    med = zone.groupby("centre")["encaissement"].median()
    lim = e["centre"].map(med) * seuil
    trop = (e["encaissement"] > lim) & lim.notna() & (lim > 0)
    for _, l in e[trop].iterrows():
        EXCEPTIONNELS.append({"centre": l["centre"], "mois": l["mois"],
                              "encaissement": l["encaissement"],
                              "mediane_centre": med[l["centre"]],
                              "ramene_a": lim[_]})
    e = e.copy()
    e.loc[trop, "encaissement"] = lim[trop]
    if EXCEPTIONNELS:
        alerte(f"{len(EXCEPTIONNELS)} mois exceptionnel(s) écrêté(s) à {seuil:g} x la médiane du centre "
               f"(voir l'onglet 'Mois exceptionnels')")
    return e


def construire_panel():
    print("\n[1] Lecture des données")
    fac = lire_csv(C.FICHIERS["facturation"])
    abn = lire_csv(C.FICHIERS["abonnes"])
    prod = lire_csv(C.FICHIERS["production"], obligatoire=False)
    verifier_colonnes(fac, ["centre", "mois", "groupe_tarif", "nb_abonnes", "ca"], "facturation.csv")
    verifier_colonnes(abn, ["centre", "nb_abonnes_actifs"], "abonnes.csv")

    # --- Facturation
    fac["centre"] = code_centre(fac["centre"])
    fac["mois"] = mois_fmt(fac["mois"])
    fac["ca"] = num(fac["ca"])
    fac["nb_abonnes"] = num(fac["nb_abonnes"])
    fac["groupe_tarif"] = fac["groupe_tarif"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    if P("EXCLURE_ADMINISTRATIONS", False):
        # Administrations : pas de règle de paiement fixe -> hors objectifs (CA et effectifs retirés)
        adm = [str(t) for t in P("TARIFS_ADMINISTRATIONS", ["4440"])]
        fac = fac[~fac["groupe_tarif"].isin(adm)].copy()
    fac["est_gc"] = fac["groupe_tarif"].isin([str(t) for t in C.TARIFS_GC])
    mensuels = [str(t) for t in P("TARIFS_FACTURATION_MENSUELLE", [])]
    fac["est_mensuel"] = fac["groupe_tarif"].isin(mensuels)
    noms = (fac.dropna(subset=["nom_centre"]).groupby("centre")["nom_centre"].first()
            if "nom_centre" in fac.columns else pd.Series(dtype=str))

    f = fac.groupby(["centre", "mois"]).apply(
        lambda g: pd.Series({
            "ca_total": g["ca"].sum(),
            "ca_gc": g.loc[g["est_gc"], "ca"].sum(),
            "ca_gc_mensuel": g.loc[g["est_gc"] & g["est_mensuel"], "ca"].sum(),
            "ca_dom_mensuel": g.loc[~g["est_gc"] & g["est_mensuel"], "ca"].sum(),
            "nb_gc_mois": g.loc[g["est_gc"], "nb_abonnes"].sum(),
        })
    ).reset_index()
    f["ca_dom"] = f["ca_total"] - f["ca_gc"]

    # --- Encaissements (+ écrêtage des mois exceptionnels)
    e = ecreter_exceptionnels(lire_encaissements())
    INFO["encaissements"] = e.copy()

    # --- Pic de l'année de référence (règle du DG), calculé avant la fenêtre
    col_pic = "encaissement" if P("PIC_HORS_EXCEPTIONNELS", True) else "encaissement_brut"
    e_pic = e[e["mois"].str.startswith(str(C.ANNEE_PIC)) & (e["mois"] <= C.MOIS_FIN)]
    if len(e_pic):
        idx = e_pic.groupby("centre")[col_pic].idxmax()
        PIC.update(e_pic.loc[idx].set_index("centre")[[col_pic, "mois"]]
                   .rename(columns={col_pic: "pic", "mois": "mois_pic"}).to_dict("index"))
    else:
        alerte(f"Aucun encaissement en {C.ANNEE_PIC} : le Pic est pris sur la fenêtre")

    # --- Panel
    p = e.merge(f, on=["centre", "mois"], how="left")
    p = p[(p["mois"] >= C.MOIS_DEBUT) & (p["mois"] <= C.MOIS_FIN)].copy()
    for c in ["ca_total", "ca_gc", "ca_gc_mensuel", "ca_dom", "ca_dom_mensuel", "nb_gc_mois"]:
        p[c] = p[c].fillna(0.0)

    sans_fact = sorted(set(p["centre"]) - set(f["centre"]))
    if sans_fact:
        alerte(f"{len(sans_fact)} centre(s) avec encaissements mais sans facturation (exclus) : {sans_fact}")
        p = p[~p["centre"].isin(sans_fact)]

    # Mois de facturation incomplets (ex. clôture d'exercice) : exclus du modèle
    seuil = P("SEUIL_MOIS_INCOMPLET", 0.5)
    if seuil:
        tot = f[(f["mois"] >= C.MOIS_DEBUT) & (f["mois"] <= C.MOIS_FIN)].groupby("mois")["ca_total"].sum()
        tot = tot.reindex(sorted(p["mois"].unique()), fill_value=0.0)
        mauvais = sorted(tot.index[tot < seuil * tot.median()])
        if mauvais:
            alerte("Mois de facturation incomplets exclus du modèle : "
                   + ", ".join(f"{m} (CA {tot[m]:,.0f})" for m in mauvais)
                   + f" — médiane mensuelle {tot.median():,.0f}")
            p = p[~p["mois"].isin(mauvais)].copy()

    # Lissage bimestriel : seul le CA facturé tous les deux mois est lissé
    p = p.sort_values(["centre", "mois"])
    if C.LISSAGE_BIMESTRIEL:
        lisser = lambda col: p.groupby("centre")[col].transform(lambda s: s.rolling(2, min_periods=1).mean())
        p["ca_gc_bimestriel"] = p["ca_gc"] - p["ca_gc_mensuel"]
        p["ca_dom_bimestriel"] = p["ca_dom"] - p["ca_dom_mensuel"]
        p["ca_dom"] = p["ca_dom_mensuel"] + lisser("ca_dom_bimestriel")
        p["ca_gc"] = p["ca_gc_mensuel"] + lisser("ca_gc_bimestriel")
        p["ca_total"] = p["ca_dom"] + p["ca_gc"]
        p = p.drop(columns=["ca_gc_bimestriel", "ca_dom_bimestriel"])

    # Nb GC : niveau stable du centre
    p["nb_gc"] = p.groupby("centre")["nb_gc_mois"].transform("max")

    # Abonnés actifs
    abn["centre"] = code_centre(abn["centre"])
    abn["nb_abonnes_actifs"] = num(abn["nb_abonnes_actifs"])
    p = p.merge(abn[["centre", "nb_abonnes_actifs"]], on="centre", how="left")
    manq = p.loc[p["nb_abonnes_actifs"].isna(), "centre"].unique()
    if len(manq):
        alerte(f"Abonnés actifs manquants pour {list(manq)} : remplacés par le max d'abonnés facturés")
        mx = fac.groupby(["centre", "mois"])["nb_abonnes"].sum().groupby("centre").max()
        p["nb_abonnes_actifs"] = p["nb_abonnes_actifs"].fillna(p["centre"].map(mx))
    p = p.rename(columns={"nb_abonnes_actifs": "nb_abonnes"})

    # Zones
    p = ajouter_zones(p, noms)

    # Volume produit (intérieur uniquement, optionnel)
    p["volume_int"] = 0.0
    if prod is not None:
        verifier_colonnes(prod, ["centre", "mois", "volume"], "production.csv")
        prod["centre"] = code_centre(prod["centre"])
        prod["mois"] = mois_fmt(prod["mois"])
        prod["volume"] = num(prod["volume"])
        v = prod.groupby(["centre", "mois"], as_index=False)["volume"].sum()
        p = p.merge(v, on=["centre", "mois"], how="left")
        interieur = p["zone"] == "INTERIEUR"
        # Mois sans donnée de production : moyenne du centre (la production varie peu)
        manquants = interieur & p["volume"].isna()
        p["volume"] = p["volume"].fillna(p.groupby("centre")["volume"].transform("mean"))
        sans = sorted(p.loc[interieur & p["volume"].isna(), "centre"].unique(), key=lambda c: int(c) if c.isdigit() else 0)
        if manquants.any():
            mois_manq = sorted(p.loc[manquants & p["volume"].notna(), "mois"].unique())
            if mois_manq:
                alerte(f"Volume produit : mois sans donnée remplacés par la moyenne du centre : {', '.join(mois_manq)}")
        if sans:
            alerte(f"Volume produit absent pour {len(sans)} centre(s) de l'intérieur (mis à 0) : {sans}")
        p["volume_int"] = np.where(interieur, p["volume"].fillna(0.0), 0.0)
        p = p.drop(columns="volume")
        INFO["volume"] = True

    p["nom_centre"] = p["nom_zone"].where(p["nom_zone"].fillna("").str.strip() != "",
                                          p["centre"].map(noms)).fillna("")
    p = p.drop(columns="nom_zone")
    print(f"  Panel : {p['centre'].nunique()} centres, {len(p)} observations centre-mois "
          f"({p['mois'].min()} → {p['mois'].max()}) — encaissements : {INFO['segment']}")
    return p


def ajouter_zones(p, noms):
    chemin = os.path.join(D_IN, C.FICHIERS["zones"])
    centres = sorted(p["centre"].unique())
    if not os.path.exists(chemin):
        modele = pd.DataFrame({"centre": centres,
                               "nom_centre": [noms.get(c, "") for c in centres],
                               "zone": ""})
        modele.to_csv(chemin, sep=";", index=False, encoding="utf-8-sig")
        sys.exit(f"\n❗ Fichier {chemin} créé.\n"
                 f"   Remplissez la colonne 'zone' avec NKTT, NDB ou INTERIEUR, puis relancez.")
    z = lire_csv(C.FICHIERS["zones"])
    verifier_colonnes(z, ["centre", "zone"], "zones.csv")
    z["centre"] = code_centre(z["centre"])
    z["zone"] = (z["zone"].fillna("").str.upper().str.strip()
                 .replace({"NOUAKCHOTT": "NKTT", "NOUADHIBOU": "NDB", "INTÉRIEUR": "INTERIEUR"}))
    if "nom_centre" not in z.columns:
        z["nom_centre"] = ""
    p = p.merge(z[["centre", "zone", "nom_centre"]].rename(columns={"nom_centre": "nom_zone"}),
                on="centre", how="left")
    mauvais = p.loc[~p["zone"].isin(["NKTT", "NDB", "INTERIEUR"]), "centre"].unique()
    if len(mauvais):
        sys.exit(f"❌ Zone manquante ou invalide pour : {list(mauvais)}.\n"
                 f"   Complétez la colonne 'zone' (NKTT, NDB ou INTERIEUR) dans {chemin}\n"
                 f"   puis relancez avec : python lancer.py --sans-extraction")
    return p


# -------------------------------------------------------------
# 3. MODÈLE LINÉAIRE MIXTE (intercept aléatoire par centre, REML)
# -------------------------------------------------------------
class ModeleMixte:
    def __init__(self, X, y, groupes, noms_x):
        self.X, self.y, self.noms = X, y, noms_x
        self.g_codes, self.g_uniques = pd.factorize(groupes)
        self.N, self.p = X.shape
        G = len(self.g_uniques)
        self.n_g = np.bincount(self.g_codes, minlength=G).astype(float)
        self.S_X = np.zeros((G, self.p))
        np.add.at(self.S_X, self.g_codes, X)
        self.S_y = np.bincount(self.g_codes, weights=y, minlength=G)
        self.XtX, self.Xty, self.yty = X.T @ X, X.T @ y, y @ y

    def _gls(self, gamma):
        c = gamma / (1 + gamma * self.n_g)
        A = self.XtX - (self.S_X * c[:, None]).T @ self.S_X
        b = self.Xty - (self.S_X * c[:, None]).T @ self.S_y
        A_inv = np.linalg.pinv(A)
        beta = A_inv @ b
        r = self.y - self.X @ beta
        S_r = np.bincount(self.g_codes, weights=r, minlength=len(self.n_g))
        Q = r @ r - np.sum(c * S_r ** 2)
        return beta, A, A_inv, Q, c, S_r

    def _neg_reml(self, log_gamma):
        gamma = np.exp(log_gamma)
        _, A, _, Q, _, _ = self._gls(gamma)
        dfr = self.N - self.p
        s2 = max(Q / dfr, 1e-12)
        sign, logdetA = np.linalg.slogdet(A)
        return 0.5 * (dfr * np.log(s2) + np.sum(np.log1p(gamma * self.n_g)) + logdetA + dfr)

    def ajuster(self):
        grille = np.linspace(-12, 8, 81)
        vals = [self._neg_reml(v) for v in grille]
        i = int(np.argmin(vals))
        lo, hi = grille[max(i - 1, 0)], grille[min(i + 1, len(grille) - 1)]
        opt = optimize.minimize_scalar(self._neg_reml, bounds=(lo, hi), method="bounded")
        self.gamma = float(np.exp(opt.x))
        beta, A, A_inv, Q, c, S_r = self._gls(self.gamma)
        self.sigma2_e = Q / (self.N - self.p)
        self.sigma2_u = self.gamma * self.sigma2_e
        self.beta = beta
        self.se = np.sqrt(np.clip(np.diag(A_inv) * self.sigma2_e, 0, None))
        t = np.divide(beta, self.se, out=np.zeros_like(beta), where=self.se > 0)
        self.pvalues = 2 * stats.t.sf(np.abs(t), df=self.N - self.p)
        self.effets_centre = pd.Series(c * S_r, index=self.g_uniques)   # BLUP
        self.pred_fixe = self.X @ beta
        var_f = np.var(self.pred_fixe)
        tot = var_f + self.sigma2_u + self.sigma2_e
        self.r2_marginal = var_f / tot
        self.r2_conditionnel = (var_f + self.sigma2_u) / tot
        self.icc = self.sigma2_u / (self.sigma2_u + self.sigma2_e)
        return self


CRITERES = {
    "ca_dom": "CA hors gros consommateurs",
    "ca_gc": "CA gros consommateurs",
    "nb_abonnes": "Nombre d'abonnés actifs",
    "nb_gc": "Nombre de gros consommateurs",
    "volume_int": "Volume produit (intérieur)",
}


def criteres_retenus(p, silencieux=False):
    exclus = set(P("CRITERES_EXCLUS", []) or [])
    crit = [c for c in CRITERES if c not in exclus and p[c].std() > 0]
    if not silencieux:
        for c in CRITERES:
            if c in exclus:
                print(f"  Critère retiré (parametres.CRITERES_EXCLUS) : {CRITERES[c]}")
            elif c not in crit and c != "volume_int":
                alerte(f"Critère '{CRITERES[c]}' sans variation : exclu du modèle")
    return crit


def design(df, crit, moy, ect, colonnes=None):
    """Matrice du modèle : constante, critères standardisés, mois, zone."""
    Z = (df[crit] - moy) / ect
    mois_d = pd.get_dummies(df["mois"], prefix="mois", dtype=float)
    zone_d = pd.get_dummies(df["zone"], prefix="zone", dtype=float)
    X = pd.concat([pd.Series(1.0, index=df.index, name="constante"), Z, mois_d, zone_d], axis=1)
    if colonnes is None:
        retirer = [sorted(mois_d.columns)[0]] + ([sorted(zone_d.columns)[0]] if len(zone_d.columns) else [])
        if len(zone_d.columns) <= 1:
            retirer += list(zone_d.columns)
        colonnes = [c for c in X.columns if c not in set(retirer)]
    return X.reindex(columns=colonnes, fill_value=0.0), colonnes


GROUPES = {"Nouakchott + Nouadhibou": ["NKTT", "NDB"], "Intérieur": ["INTERIEUR"]}


class Ensemble:
    """Résultats de plusieurs modèles (un par groupe de zones), vus comme un seul."""

    def __init__(self, modeles, p):
        self.groupes = modeles
        self.effets_centre = pd.concat([m.effets_centre for m in modeles.values()])
        n = {g: m.N for g, m in modeles.items()}
        tot = sum(n.values())
        for a in ("r2_marginal", "r2_conditionnel", "icc"):
            setattr(self, a, sum(getattr(m, a) * n[g] for g, m in modeles.items()) / tot)


def estimer(p):
    print("\n[2] Estimation du modèle multiniveau")
    if not P("MODELES_SEPARES", True):
        m, poids, p = _estimer_groupe(p)
        poids.insert(0, "groupe", "National")
        return Ensemble({"National": m}, p), poids, p
    parts, poids_l, modeles = [], [], {}
    for nom, zones_g in GROUPES.items():
        pg = p[p["zone"].isin(zones_g)]
        if pg.empty:
            continue
        print(f"  — Modèle {nom} : {pg['centre'].nunique()} centres")
        m, pw, pg = _estimer_groupe(pg, nom)
        pw.insert(0, "groupe", nom)
        parts.append(pg)
        poids_l.append(pw)
        modeles[nom] = m
    p = pd.concat(parts).sort_values(["centre", "mois"])
    return Ensemble(modeles, p), pd.concat(poids_l, ignore_index=True), p


def _estimer_groupe(p, nom="National"):
    crit = criteres_retenus(p)
    moy, ect = p[crit].mean(), p[crit].std()
    Xdf, colonnes = design(p, crit, moy, ect)
    y = p["encaissement"].values / 1e6              # en millions MRU (stabilité numérique)
    m = ModeleMixte(Xdf.values, y, p["centre"].values, colonnes).ajuster()
    m.colonnes, m.crit, m.moy, m.ect = colonnes, crit, moy, ect

    if np.linalg.matrix_rank(Xdf.values) < Xdf.shape[1]:
        alerte("Colinéarité parfaite entre certaines variables : interpréter les poids avec prudence")
    n_c = p["centre"].nunique()
    if n_c < 15:
        alerte(f"{nom} : seulement {n_c} centres, poids peu fiables (recommandé ≥ 15)")

    # --- Poids
    idx = {n: i for i, n in enumerate(colonnes)}
    lignes = []
    for c in crit:
        b = m.beta[idx[c]]
        lignes.append({
            "critere": CRITERES[c],
            "coef_standardise_MMRU": b,
            "effet_unitaire_MRU": b * 1e6 / ect[c],
            "p_value": m.pvalues[idx[c]],
            "significatif_5pct": "Oui" if m.pvalues[idx[c]] < 0.05 else "Non",
        })
    poids = pd.DataFrame(lignes)
    pos = poids["coef_standardise_MMRU"].clip(lower=0)
    poids["poids_pct"] = 100 * pos / pos.sum() if pos.sum() > 0 else 0.0
    neg = poids.loc[poids["coef_standardise_MMRU"] < 0, "critere"].tolist()
    if neg:
        alerte(f"{nom} : coefficient négatif pour {neg} (souvent dû à la colinéarité) : poids mis à 0")
    poids = poids.sort_values("poids_pct", ascending=False).reset_index(drop=True)

    p = p.copy()
    p["pred_fixe"] = m.pred_fixe * 1e6
    m.effets_centre = m.effets_centre * 1e6
    return m, poids, p


# -------------------------------------------------------------
# 4. VALIDATION SUR LE PASSÉ (le dernier mois est caché au modèle)
# -------------------------------------------------------------
def valider(p):
    if not P("VALIDATION", True):
        return None, {}
    mois = sorted(p["mois"].unique())
    if len(mois) < 4:
        return None, {}
    test_mois = mois[-1]
    if P("MODELES_SEPARES", True):
        morceaux = [_valider_groupe(p[p["zone"].isin(z)], test_mois) for z in GROUPES.values()]
        morceaux = [x for x in morceaux if x is not None]
    else:
        morceaux = [_valider_groupe(p, test_mois)]
    v = pd.concat(morceaux, ignore_index=True)

    def resume(col):
        err = (v[col] - v["reel"]).abs()
        return {"national_pct": 100 * (v[col].sum() / v["reel"].sum() - 1),
                "erreur_ponderee_pct": 100 * err.sum() / v["reel"].sum(),
                "centres_a_20pct": 100 * (err <= 0.2 * v["reel"].abs()).mean()}
    res = {"mois_test": test_mois, "modele": resume("prevision_modele"), "moyenne": resume("prevision_moyenne")}
    print(f"\n  Validation : modèle entraîné sans {test_mois}, puis comparé au réel de {test_mois}")
    print(f"    erreur pondérée : modèle {res['modele']['erreur_ponderee_pct']:.1f} %  |  "
          f"simple moyenne {res['moyenne']['erreur_ponderee_pct']:.1f} %  |  "
          f"écart national modèle {res['modele']['national_pct']:+.1f} %")
    return v.sort_values("reel", ascending=False), res


def _valider_groupe(p, test_mois):
    if p.empty:
        return None
    train, test = p[p["mois"] < test_mois], p[p["mois"] == test_mois]
    test = test[test["centre"].isin(train["centre"].unique())]
    crit = criteres_retenus(train, silencieux=True)
    moy, ect = train[crit].mean(), train[crit].std()
    Xtr, colonnes = design(train, crit, moy, ect)
    m = ModeleMixte(Xtr.values, train["encaissement"].values / 1e6, train["centre"].values, colonnes).ajuster()
    Xte, _ = design(test, crit, moy, ect, colonnes)
    # Mois inconnu du modèle : effet moyen des mois d'apprentissage
    eff_mois = [0.0] + [m.beta[i] for i, c in enumerate(colonnes) if c.startswith("mois_")]
    pred = (Xte.values @ m.beta + np.mean(eff_mois)) * 1e6
    pred += test["centre"].map(m.effets_centre * 1e6).fillna(0.0).values
    naif = test["centre"].map(train.groupby("centre")["encaissement"].mean()).values

    v = pd.DataFrame({"centre": test["centre"].values, "nom_centre": test["nom_centre"].values,
                      "zone": test["zone"].values, "reel": test["encaissement"].values,
                      "prevision_modele": pred, "prevision_moyenne": naif})
    v["ecart_modele_pct"] = 100 * (v["prevision_modele"] / v["reel"].replace(0, np.nan) - 1)
    v["ecart_moyenne_pct"] = 100 * (v["prevision_moyenne"] / v["reel"].replace(0, np.nan) - 1)
    return v


# -------------------------------------------------------------
# 5. CALCUL DES OBJECTIFS
# -------------------------------------------------------------
def repartir(base, plafond, cible):
    """Objectif = min(plafond, λ·base) avec Σ objectifs = cible."""
    base, plafond = np.asarray(base, float), np.asarray(plafond, float)
    if plafond.sum() <= cible:
        return plafond.copy(), np.nan, np.ones(len(base), bool)
    libres = np.ones(len(base), bool)
    for _ in range(len(base) + 1):
        lam = (cible - plafond[~libres].sum()) / base[libres].sum()
        depasse = libres & (lam * base > plafond)
        if not depasse.any():
            break
        libres &= ~depasse
    obj = np.where(libres, lam * base, plafond)
    return obj, lam, ~libres


def indices_saisonniers():
    """Indice de chaque mois calendaire : encaissement national du mois / moyenne mensuelle."""
    if not P("SAISONNALITE", True):
        return {}
    e = INFO["encaissements"]
    e = e[(e["mois"] >= C.MOIS_DEBUT) & (e["mois"] <= C.MOIS_FIN)]
    nat = e.groupby("mois")["encaissement"].sum()
    if len(nat) < 12:
        alerte(f"Saisonnalité calculée sur {len(nat)} mois seulement (12 recommandés)")
    par_mois = nat.groupby(nat.index.str[5:7]).mean()
    return (par_mois / par_mois.mean()).to_dict()


def lire_arrieres():
    a = lire_csv(C.FICHIERS.get("arrieres", "arrieres.csv"), obligatoire=False)
    if a is None or not {"centre", "arrieres"} <= set(a.columns):
        alerte("arrieres.csv absent : relancez l'extraction (python lancer.py) pour la part arriérés")
        return pd.Series(dtype=float)
    a["centre"] = code_centre(a["centre"])
    a["arrieres"] = num(a["arrieres"])
    return a.groupby("centre")["arrieres"].sum()


def calculer_objectifs(p, m):
    print("\n[3] Calcul des objectifs")
    cible = C.OBJECTIF_NATIONAL / 12 if C.PERIODE_OBJECTIF == "annuel" else C.OBJECTIF_NATIONAL

    r = p.groupby("centre").agg(
        nom_centre=("nom_centre", "first"),
        zone=("zone", "first"),
        nb_mois=("mois", "nunique"),
        encaissement_moyen=("encaissement", "mean"),
        pic_2026=("encaissement", "max"),
        ca_moyen=("ca_total", "mean"),
        potentiel_modele=("pred_fixe", "mean"),
    )
    r["mois_du_pic"] = p.loc[p.groupby("centre")["encaissement"].idxmax()].set_index("centre")["mois"]
    if PIC:
        r["pic_2026"] = [PIC.get(c, {}).get("pic", v) for c, v in zip(r.index, r["pic_2026"])]
        r["mois_du_pic"] = [PIC.get(c, {}).get("mois_pic", v) for c, v in zip(r.index, r["mois_du_pic"])]

    # Objectif État : médiane mensuelle de la fenêtre (paiements irréguliers)
    e = INFO["encaissements"]
    e = e[(e["mois"] >= C.MOIS_DEBUT) & (e["mois"] <= C.MOIS_FIN)]
    r["objectif_etat"] = r.index.map(e.groupby("centre")["encaissement_etat"].median()).fillna(0.0)
    r["objectif_etat"] = r["objectif_etat"].clip(lower=0)
    if P("EXCLURE_ADMINISTRATIONS", False):
        r["objectif_etat"] = 0.0          # administrations hors objectifs
    cible_modelisee = cible - r["objectif_etat"].sum()

    r["nb_mois_exceptionnels"] = r.index.map(pd.Series([x["centre"] for x in EXCEPTIONNELS]).value_counts()
                                             if EXCEPTIONNELS else pd.Series(dtype=int)).fillna(0).astype(int)
    r["effet_centre"] = m.effets_centre.reindex(r.index)
    r["plafond_pic"] = C.COEF_PLAFOND * r["pic_2026"]
    sens = P("SENS_REGLE_DG", "maximum")
    minimum_dg = sens == "minimum"        # objectif de CHAQUE centre >= 1,2 x son Pic
    national_dg = sens == "national"      # objectif NATIONAL >= 1,2 x meilleur mois national de 2026
    INFO["minimum_dg"], INFO["national_dg"] = minimum_dg, national_dg
    if national_dg:
        e_all = INFO["encaissements"]
        nat = e_all[e_all["mois"].str.startswith(str(C.ANNEE_PIC)) & (e_all["mois"] <= C.MOIS_FIN)] \
            .groupby("mois")["encaissement_brut"].sum()
        INFO["pic_national"], INFO["mois_pic_national"] = float(nat.max()), nat.idxmax()
        INFO["minimum_national"] = C.COEF_PLAFOND * INFO["pic_national"]
    prog_max = P("PROGRESSION_MAX")
    if prog_max is not None:
        r["plafond_progression"] = (1 + prog_max) * r["encaissement_moyen"]
        r["plafond"] = (r["plafond_progression"] if (minimum_dg or national_dg)
                        else np.minimum(r["plafond_pic"], r["plafond_progression"]))
    else:
        r["plafond_progression"] = np.nan
        r["plafond"] = np.inf if (minimum_dg or national_dg) else r["plafond_pic"]
    r["base"] = np.where(r["potentiel_modele"] > 0,
                         np.maximum(r["encaissement_moyen"], r["potentiel_modele"]),
                         r["encaissement_moyen"])

    obj, lam, plafonne = repartir(r["base"].values, np.asarray(r["plafond"], float) * np.ones(len(r)),
                                  cible_modelisee)
    r["objectif_calcule"] = obj                      # objectif issu du modèle, avant la règle du DG
    if minimum_dg:
        r["objectif_mensuel"] = np.maximum(obj, r["plafond_pic"])     # objectif >= 1,2 x Pic
    else:
        r["objectif_mensuel"] = obj                  # objectif moyen de la série modélisée
    r["objectif_total"] = r["objectif_mensuel"] + r["objectif_etat"]
    r["objectif_annuel"] = r["objectif_total"] * 12
    r["progression_pct"] = 100 * (r["objectif_mensuel"] / r["encaissement_moyen"] - 1)
    lib_prog = f"Plafonné (+{prog_max:.0%} max)".replace("%", " %") if prog_max is not None else ""
    if national_dg:
        r["statut"] = np.where(plafonne, lib_prog, "Libre")
    elif minimum_dg:
        releve = r["plafond_pic"] > r["objectif_calcule"] + 0.5
        r["statut"] = np.where(releve, f"Relevé à {C.COEF_PLAFOND:g} x Pic".replace(".", ","),
                               np.where(plafonne, lib_prog, "Libre"))
    else:
        par_pic = r["plafond_pic"] <= r["plafond"] + 0.5
        r["statut"] = np.where(~plafonne, "Libre",
                               np.where(par_pic, f"Plafonné ({C.COEF_PLAFOND:g} x Pic)".replace(".", ","), lib_prog))
    r["lecture_effet_centre"] = np.where(r["effet_centre"] >= 0, "Au-dessus de son potentiel",
                                         "En dessous de son potentiel")

    # --- Objectif du mois (saisonnalité)
    idx_saison = indices_saisonniers()
    INFO["saisonnalite"] = idx_saison
    mm = C.MOIS_OBJECTIF[5:7]
    r["indice_saisonnier"] = idx_saison.get(mm, 1.0)
    if national_dg:
        om = r["objectif_calcule"] * r["indice_saisonnier"]
        mini = INFO["minimum_national"] - r["objectif_etat"].sum()
        if om.sum() < mini - 0.5:           # total du mois relevé au minimum national, réparti par le modèle
            pl = np.asarray(r["plafond"], float) * np.ones(len(r)) * r["indice_saisonnier"].values
            om, _, _ = repartir(om.values, pl, mini)
        r["objectif_du_mois"] = om
        tot_m = (r["objectif_du_mois"] + r["objectif_etat"]).sum()
        alerte(f"Règle du DG au niveau NATIONAL : meilleur mois national {C.ANNEE_PIC} = "
               f"{INFO['pic_national']:,.0f} ({INFO['mois_pic_national']}) ; minimum {C.COEF_PLAFOND:g} x Pic = "
               f"{INFO['minimum_national']:,.0f} ; total du mois {tot_m:,.0f} "
               f"({'respecté' if tot_m >= INFO['minimum_national'] - 1 else 'NON respecté'})")
    elif minimum_dg:
        r["objectif_du_mois"] = np.maximum(r["objectif_calcule"] * r["indice_saisonnier"], r["plafond_pic"])
        nb_rel = int((r["plafond_pic"] > r["objectif_calcule"] + 0.5).sum())
        alerte(f"Règle du DG appliquée comme MINIMUM (objectif >= {C.COEF_PLAFOND:g} x Pic 2026) : "
               f"{nb_rel} centre(s) relevé(s) ; total du mois {(r['objectif_du_mois'] + r['objectif_etat']).sum():,.0f} "
               f"MRU, au-dessus de la cible de {cible:,.0f}")
        if prog_max is not None:
            conflit = int((r["plafond_pic"] > r["plafond_progression"] + 0.5).sum())
            if conflit:
                alerte(f"{conflit} centre(s) où le minimum 1,2 x Pic dépasse la limite +{prog_max:.0%} : "
                       f"la règle du DG l'emporte")
    else:
        r["objectif_du_mois"] = np.minimum(r["objectif_mensuel"] * r["indice_saisonnier"], r["plafond_pic"])
    r["objectif_du_mois_total"] = r["objectif_du_mois"] + r["objectif_etat"]

    # --- Décomposition : part courante / part arriérés
    arr = lire_arrieres()
    r["stock_arrieres"] = r.index.map(arr).astype(float) if len(arr) else np.nan
    r["part_courante"] = np.minimum(r["objectif_total"], r["ca_moyen"])
    r["part_arrieres"] = (r["objectif_total"] - r["part_courante"]).clip(lower=0)
    r["arrieres_pct_stock"] = 100 * r["part_arrieres"] / r["stock_arrieres"].replace(0, np.nan)
    r["taux_recouvrement_pct"] = 100 * (r["encaissement_moyen"] + r["objectif_etat"]) / r["ca_moyen"].replace(0, np.nan)

    somme_plafonds = r["plafond"].sum()
    if np.isnan(lam):
        alerte(f"Cible ({cible_modelisee:,.0f}) > somme des plafonds ({somme_plafonds:,.0f}) : "
               f"objectif national INATTEIGNABLE avec les plafonds (1,2 x Pic et progression max). "
               f"Écart : {cible_modelisee - somme_plafonds:,.0f} MRU/mois")
    elif lam < 1:
        alerte(f"λ = {lam:.3f} < 1 : certains objectifs sont inférieurs à la base (cible nationale faible)")
    sous = r.index[r["objectif_mensuel"] < r["encaissement_moyen"] - 0.5].tolist()
    if sous:
        alerte(f"{len(sous)} centre(s) avec objectif < encaissement moyen actuel : {sous}")

    # Comparaison avec les objectifs actuels (fichier DSP, comparés aux privés)
    act = lire_csv(C.FICHIERS["objectifs_actuels"], obligatoire=False)
    r["objectif_actuel"] = np.nan
    if act is not None and {"centre", "objectif_actuel"} <= set(act.columns):
        act["centre"] = code_centre(act["centre"])
        act["objectif_actuel"] = num(act["objectif_actuel"])
        r["objectif_actuel"] = r.index.map(act.groupby("centre")["objectif_actuel"].sum()).astype(float)
        r.loc[r["objectif_actuel"] == 0, "objectif_actuel"] = np.nan
    r["ecart_vs_actuel_pct"] = 100 * (r["objectif_du_mois"] / r["objectif_actuel"] - 1)

    r = r.sort_values("objectif_du_mois_total", ascending=False)
    info = {"cible": cible, "cible_modelisee": cible_modelisee, "lambda": lam,
            "somme_plafonds": somme_plafonds, "total_etat": r["objectif_etat"].sum()}
    return r.reset_index(), info


# -------------------------------------------------------------
# 6. EXPORTS
# -------------------------------------------------------------
def exporter(res, poids, m, info, p, validation):
    print("\n[4] Export des résultats")
    chemin = os.path.join(D_OUT, f"objectifs_{C.MOIS_OBJECTIF}.xlsx")
    pp = f"Pic {C.ANNEE_PIC}"
    prv = INFO.get("segment", "").startswith("Privés")
    seg = "privés" if prv else "tous encaissements"
    colonnes = [
        ("centre", "Centre"), ("nom_centre", "Nom centre"), ("zone", "Zone"),
        ("objectif_du_mois_total", f"OBJECTIF {C.MOIS_OBJECTIF} (TOTAL)"),
        ("objectif_du_mois", f"Objectif {C.MOIS_OBJECTIF} {seg} (écran Objectifs)"),
        ("objectif_etat", "Objectif État (mois)"),
        ("indice_saisonnier", "Indice saisonnier du mois"),
        ("objectif_mensuel", f"Objectif {seg} mois moyen"),
        ("objectif_total", "Objectif total mois moyen"),
        ("objectif_annuel", "Objectif annuel total"),
        ("part_courante", "dont part courante (factures du mois)"),
        ("part_arrieres", "dont part arriérés"),
        ("stock_arrieres", "Stock d'arriérés"),
        ("arrieres_pct_stock", "Part arriérés / stock (%/mois)"),
        ("encaissement_moyen", f"Encaissement moyen ({seg})"),
        ("progression_pct", "Progression vs moyenne (%)"),
        ("statut", "Statut"),
        ("pic_2026", pp), ("mois_du_pic", "Mois du pic"),
        ("nb_mois_exceptionnels", "Mois exceptionnels écrêtés"),
        ("plafond_pic", (f"Minimum DG ({C.COEF_PLAFOND} x Pic)" if INFO.get("minimum_dg")
                         else f"{C.COEF_PLAFOND} x Pic du centre (information)" if INFO.get("national_dg")
                         else f"Plafond ({C.COEF_PLAFOND} x Pic)")),
        ("plafond_progression", "Plafond progression max"), ("plafond", "Plafond retenu"),
        ("potentiel_modele", "Potentiel (modèle)"), ("effet_centre", "Effet centre"),
        ("lecture_effet_centre", "Lecture effet centre"), ("base", "Base"),
        ("objectif_actuel", "Objectif actuel (DSP)"), ("ecart_vs_actuel_pct", "Écart vs objectif DSP (%)"),
        ("ca_moyen", "CA moyen"), ("taux_recouvrement_pct", "Taux recouvrement (%)"), ("nb_mois", "Nb mois"),
    ]
    obj = res[[c for c, _ in colonnes]].copy()
    obj.columns = [l for _, l in colonnes]
    a_sommer = [l for c, l in colonnes if c in (
        "objectif_du_mois_total", "objectif_du_mois", "objectif_etat", "objectif_mensuel", "objectif_total",
        "objectif_annuel", "part_courante", "part_arrieres", "stock_arrieres", "encaissement_moyen",
        "pic_2026", "plafond_pic", "plafond", "potentiel_modele", "base", "objectif_actuel", "ca_moyen")]
    total = {c: "" for c in obj.columns}
    total.update({"Centre": "TOTAL", **{c: obj[c].sum() for c in a_sommer}})
    obj = pd.concat([obj, pd.DataFrame([total])], ignore_index=True)

    pw = poids.rename(columns={
        "critere": "Critère", "poids_pct": "POIDS (%)",
        "coef_standardise_MMRU": "Effet de +1 écart-type (millions MRU/mois)",
        "effet_unitaire_MRU": "Effet de +1 unité (MRU encaissés)",
        "p_value": "p-value", "significatif_5pct": "Significatif (5 %)"})
    pw = pw.rename(columns={"groupe": "Modèle"})
    pw = pw[["Modèle", "Critère", "POIDS (%)", "Effet de +1 écart-type (millions MRU/mois)",
             "Effet de +1 unité (MRU encaissés)", "p-value", "Significatif (5 %)"]]

    # Saisonnalité et objectifs des 12 prochains mois
    idx = INFO.get("saisonnalite", {})
    noms_mois = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août",
                 "Septembre", "Octobre", "Novembre", "Décembre"]
    saison = pd.DataFrame({"Mois": noms_mois,
                           "Indice": [idx.get(f"{i:02d}", np.nan) for i in range(1, 13)]})
    prochains = [decaler(C.MOIS_OBJECTIF, k) for k in range(12)]
    m12 = res[["centre", "nom_centre", "zone"]].copy()
    for mo in prochains:
        i = idx.get(mo[5:7], 1.0)
        if INFO.get("national_dg"):
            om = res["objectif_calcule"] * i
            mini = INFO["minimum_national"] - res["objectif_etat"].sum()
            if om.sum() < mini - 0.5:
                om, _, _ = repartir(om.values, np.asarray(res["plafond"], float) * np.ones(len(res)) * i, mini)
            m12[mo] = om + res["objectif_etat"]
        elif INFO.get("minimum_dg"):
            m12[mo] = np.maximum(res["objectif_calcule"] * i, res["plafond_pic"]) + res["objectif_etat"]
        else:
            m12[mo] = np.minimum(res["objectif_mensuel"] * i, res["plafond_pic"]) + res["objectif_etat"]
    tot12 = {"centre": "TOTAL", "nom_centre": "", "zone": "", **{mo: m12[mo].sum() for mo in prochains}}
    m12 = pd.concat([m12, pd.DataFrame([tot12])], ignore_index=True)

    exc = pd.DataFrame(EXCEPTIONNELS or [{"centre": "Aucun"}])
    if EXCEPTIONNELS:
        exc["nom_centre"] = exc["centre"].map(res.set_index("centre")["nom_centre"])
        exc = exc[["centre", "nom_centre", "mois", "encaissement", "mediane_centre", "ramene_a"]]
        exc.columns = ["Centre", "Nom centre", "Mois", "Encaissement réel", "Médiane du centre", "Ramené à"]

    val_res = validation[1] if validation and validation[1] else {}
    diag_l = [
        ("Méthode", "Modèle linéaire mixte, intercept aléatoire par centre (REML)"),
        ("Encaissements modélisés", INFO.get("segment", "")),
        ("Nombre de centres", p["centre"].nunique()),
        ("Nombre d'observations (centre x mois)", len(p)),
        ("Mois de l'objectif", C.MOIS_OBJECTIF),
        ("Période d'estimation (fenêtre glissante)", f"{p['mois'].min()} → {p['mois'].max()}"),
        ("Année du Pic", C.ANNEE_PIC),
        ("Modèles", " | ".join(f"{g} ({mm.N} obs.)" for g, mm in m.groupes.items())),
        *[(f"R² marginal — {g}", round(mm.r2_marginal, 3)) for g, mm in m.groupes.items()],
        *[(f"R² conditionnel — {g}", round(mm.r2_conditionnel, 3)) for g, mm in m.groupes.items()],
        ("R² marginal (moyenne pondérée)", round(m.r2_marginal, 3)),
        ("Objectif national", f"{C.OBJECTIF_NATIONAL:,.0f} ({C.PERIODE_OBJECTIF})"),
        ("Cible mensuelle (mois moyen)", round(info["cible"])),
        ("dont objectif État", round(info["total_etat"])),
        ("dont objectif réparti par le modèle", round(info["cible_modelisee"])),
        (f"Total objectif {C.MOIS_OBJECTIF} (saisonnalisé)", round(res["objectif_du_mois_total"].sum())),
        ("Indice saisonnier du mois", round(idx.get(C.MOIS_OBJECTIF[5:7], 1.0), 3)),
        ("Total part courante / part arriérés", f"{res['part_courante'].sum():,.0f} / {res['part_arrieres'].sum():,.0f}"),
        ("Progression maximale", "aucune" if P("PROGRESSION_MAX") is None else f"+{C.PROGRESSION_MAX:.0%}"),
        ("Seuil mois exceptionnel", "aucun" if not P("SEUIL_EXCEPTIONNEL") else f"{C.SEUIL_EXCEPTIONNEL:g} x médiane"),
        ("Critères retirés", ", ".join(CRITERES[c] for c in P("CRITERES_EXCLUS", []) if c in CRITERES) or "aucun"),
        ("Somme des plafonds", round(info["somme_plafonds"])),
        ("λ (coefficient d'ajustement)", "—" if np.isnan(info["lambda"]) else round(info["lambda"], 4)),
        ("Tarifs gros consommateurs", ", ".join(C.TARIFS_GC)),
    ]
    if val_res:
        diag_l += [
            ("Validation : mois testé", val_res["mois_test"]),
            ("Validation : erreur pondérée modèle (%)", round(val_res["modele"]["erreur_ponderee_pct"], 1)),
            ("Validation : erreur pondérée simple moyenne (%)", round(val_res["moyenne"]["erreur_ponderee_pct"], 1)),
            ("Validation : centres à ±20 % (modèle)", f"{val_res['modele']['centres_a_20pct']:.0f} %"),
            ("Validation : écart national (modèle)", f"{val_res['modele']['national_pct']:+.1f} %"),
        ]
    diag = pd.DataFrame(diag_l, columns=["Indicateur", "Valeur"])
    al = pd.DataFrame({"Alertes": ALERTES or ["Aucune"]})

    with pd.ExcelWriter(chemin, engine="openpyxl") as xw:
        obj.to_excel(xw, sheet_name="Objectifs", index=False)
        pw.to_excel(xw, sheet_name="Poids des critères", index=False)
        m12.to_excel(xw, sheet_name="Objectifs 12 mois", index=False)
        saison.to_excel(xw, sheet_name="Saisonnalité", index=False)
        if validation and validation[0] is not None:
            vv = validation[0].rename(columns={
                "centre": "Centre", "nom_centre": "Nom centre", "zone": "Zone", "reel": "Réel",
                "prevision_modele": "Prévision modèle", "prevision_moyenne": "Prévision simple moyenne",
                "ecart_modele_pct": "Écart modèle (%)", "ecart_moyenne_pct": "Écart moyenne (%)"})
            vv.to_excel(xw, sheet_name="Validation", index=False)
        exc.to_excel(xw, sheet_name="Mois exceptionnels", index=False)
        diag.to_excel(xw, sheet_name="Diagnostic", index=False)
        al.to_excel(xw, sheet_name="Alertes", index=False)
        p.to_excel(xw, sheet_name="Données panel", index=False)
        mise_en_forme(xw)
    shutil.copyfile(chemin, os.path.join(D_OUT, "objectifs_centres.xlsx"))

    # Fichier lu par l'écran « Objectifs » de l'application (objectif comparé aux privés)
    csv_app = os.path.join(D_OUT, f"objectifs_{C.MOIS_OBJECTIF}.csv")
    res[["centre", "nom_centre", "zone", "objectif_du_mois", "objectif_etat", "objectif_du_mois_total"]].rename(
        columns={"objectif_du_mois": "objectif", "objectif_du_mois_total": "objectif_total"}
    ).round(0).to_csv(csv_app, sep=";", index=False, encoding="utf-8-sig")
    print(f"  ✅ {chemin}")
    print(f"  ✅ {csv_app}  (lu par l'écran Objectifs de l'application)")
    print("  ✅ copie : sorties/objectifs_centres.xlsx (dernier calcul)")


def mise_en_forme(xw):
    from openpyxl.styles import Font, PatternFill, Alignment
    entete = PatternFill("solid", fgColor="1F4E78")
    for ws in xw.book.worksheets:
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = entete
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[1].height = 48
        ws.freeze_panes = "B2" if ws.title in ("Objectifs", "Objectifs 12 mois") else "A2"
        for col in ws.columns:
            larg = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
            ws.column_dimensions[col[0].column_letter].width = min(max(12, larg + 2), 40)
            for c in col[1:]:
                if isinstance(c.value, float):
                    c.number_format = "#,##0.00" if abs(c.value) < 10 else "#,##0"
        if ws.max_row > 1 and str(ws.cell(ws.max_row, 1).value) == "TOTAL":
            for c in ws[ws.max_row]:
                c.font = Font(bold=True)
    ws = xw.book["Objectifs"]
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if str(ws.cell(1, c.column).value).startswith("OBJECTIF "):
                c.font = Font(bold=True, color="1F4E78")


# -------------------------------------------------------------
def main():
    print("=" * 62)
    print(f"  MODÈLE MULTINIVEAU — OBJECTIFS DE {C.MOIS_OBJECTIF}")
    print(f"  Fenêtre : {C.MOIS_DEBUT} → {C.MOIS_FIN}  |  Pic : {C.ANNEE_PIC}")
    print("=" * 62)
    p = construire_panel()
    m, poids, p = estimer(p)
    validation = valider(p)
    res, info = calculer_objectifs(p, m)
    exporter(res, poids, m, info, p, validation)

    print("\n" + "-" * 62)
    print("  POIDS DES CRITÈRES")
    print("-" * 62)
    for g, mm in m.groupes.items():
        print(f"  {g}   (R² marginal = {mm.r2_marginal:.2f} | R² conditionnel = {mm.r2_conditionnel:.2f})")
        for _, l in poids[poids["groupe"] == g].iterrows():
            print(f"    {l['critere']:<32} {l['poids_pct']:6.1f} %   (p = {l['p_value']:.3f})")
    print("\n" + "-" * 62)
    print(f"  OBJECTIFS DE {C.MOIS_OBJECTIF} ({INFO.get('segment', '')})   indice saisonnier "
          f"{INFO.get('saisonnalite', {}).get(C.MOIS_OBJECTIF[5:7], 1.0):.2f}")
    print("-" * 62)
    for _, l in res.iterrows():
        print(f"  {l['centre']:<6} {str(l['nom_centre'])[:24]:<24} {l['objectif_du_mois_total']:>16,.0f}  {l['statut']}")
    print(f"  {'TOTAL':<31} {res['objectif_du_mois_total'].sum():>16,.0f}")
    print(f"  (mois moyen : {res['objectif_total'].sum():,.0f} | dont arriérés : {res['part_arrieres'].sum():,.0f})")
    print(f"\n  {len(ALERTES)} alerte(s) — voir l'onglet 'Alertes'.")


if __name__ == "__main__":
    main()
