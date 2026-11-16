"""
OBJECTIFS D'ENCAISSEMENT PAR RÉGRESSION QUANTILE RÉGULARISÉE (expérimentation)
==============================================================================
Lancement :  python regression_quantile.py
Sortie    :  sorties/objectifs_quantile_AAAA-MM.xlsx  (ne modifie pas le fichier lu par l'application)

Principe
--------
Pour chaque groupe (Nouakchott + Nouadhibou / Intérieur) et chaque quantile τ (50, 60, 70, 75 %) :
    Qτ(encaissement du centre au mois t | informations disponibles avant t)
        = constante + Σ coefficients x variables standardisées
Les coefficients minimisent la perte quantile + α x Σ|coefficients| (pénalisation L1, comme
sklearn.linear_model.QuantileRegressor), α étant choisi par validation chronologique.

Règles de datation
------------------
Une ligne = un centre x un mois t. Toutes les variables de la ligne t sont calculées avec des
données des mois < t (encaissements, facturation, production), et les corrections, imputations
et standardisations sont recalculées dans chaque période d'apprentissage.
Effectifs : le nombre d'abonnés ACTIFS n'existe qu'aujourd'hui (abonnes.csv). Les tests
chronologiques utilisent donc le nombre d'abonnés FACTURÉS (facturation.csv), qui est daté.
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import linprog

warnings.filterwarnings("ignore", category=RuntimeWarning)

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import parametres as C  # noqa: E402


def P(nom, defaut=None):
    return getattr(C, nom, defaut)


D_IN = os.path.join(ICI, P("DOSSIER_DONNEES", "donnees"))
D_OUT = os.path.join(ICI, P("DOSSIER_SORTIES", "sorties"))
QUANTILES = P("QR_QUANTILES", [0.50, 0.60, 0.70, 0.75])
ALPHAS = P("QR_ALPHAS", [0.0, 0.001, 0.003, 0.01, 0.03, 0.1])
NB_MOIS_FINAUX = P("QR_MOIS_RESERVES", 2)       # période finale jamais utilisée pour choisir α
GROUPES = {"Nouakchott + Nouadhibou": ["NKTT", "NDB"], "Intérieur": ["INTERIEUR"]}
TARIFS_GC = [str(t) for t in P("TARIFS_GC", ["2220", "1110", "4440"])]
NOTES = []


def note(msg):
    NOTES.append(msg)
    print(f"  • {msg}")


def decaler(m, n):
    a, mm = map(int, m.split("-"))
    k = a * 12 + mm - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def lire(nom):
    return pd.read_csv(os.path.join(D_IN, nom), sep=";", encoding="utf-8-sig",
                       dtype={"centre": str, "groupe_tarif": str})


# =============================================================
# 1. DONNÉES BRUTES
# =============================================================
class Brut:
    def __init__(self):
        e = lire(P("FICHIERS")["encaissements"])
        e["montant"] = pd.to_numeric(e["montant"], errors="coerce")
        self.enc = e.pivot_table(index="centre", columns="mois", values="montant", aggfunc="sum")
        f = lire(P("FICHIERS")["facturation"])
        f["groupe_tarif"] = f["groupe_tarif"].astype(str).str.replace(r"\.0$", "", regex=True)
        f["est_gc"] = f["groupe_tarif"].isin(TARIFS_GC)
        f["ca"] = pd.to_numeric(f["ca"], errors="coerce")
        f["nb_abonnes"] = pd.to_numeric(f["nb_abonnes"], errors="coerce")
        piv = lambda masque, col: f[masque].pivot_table(index="centre", columns="mois", values=col, aggfunc="sum")
        self.ca_gc = piv(f["est_gc"], "ca")
        self.ca_dom = piv(~f["est_gc"], "ca")
        self.nb_gc = piv(f["est_gc"], "nb_abonnes")
        self.nb_dom = piv(~f["est_gc"], "nb_abonnes")
        mois_f = sorted(f["mois"].unique())
        for x in ("ca_gc", "ca_dom", "nb_gc", "nb_dom"):
            setattr(self, x, getattr(self, x).reindex(columns=mois_f).fillna(0.0))
        z = lire(P("FICHIERS")["zones"])
        self.zone = z.set_index("centre")["zone"]
        self.nom = z.set_index("centre")["nom_centre"]
        self.groupe = self.zone.map(lambda v: next(g for g, zs in GROUPES.items() if v in zs))
        try:
            pr = pd.read_csv(os.path.join(D_IN, P("FICHIERS")["production"]), sep=";", dtype={"centre": str})
            pr["volume"] = pd.to_numeric(pr["volume"], errors="coerce")
            self.vol = pr.pivot_table(index="centre", columns="mois", values="volume", aggfunc="sum")
        except FileNotFoundError:
            self.vol = pd.DataFrame()
        self.centres = sorted(set(self.enc.index) & set(self.zone.index), key=lambda c: int(c) if c.isdigit() else 0)
        self.mois_enc = sorted(self.enc.columns)
        self.mois_fac = mois_f
        self.mois_vol = sorted(self.vol.columns) if len(self.vol) else []

    # ---- facturation corrigée avec les seules données < t
    def fac_avant(self, t):
        cols = [m for m in self.mois_fac if m < t]
        tot = (self.ca_dom[cols] + self.ca_gc[cols]).sum()
        incomplets = list(tot.index[tot < 0.5 * tot.median()]) if len(cols) >= 3 else []
        out = {}
        for nom in ("ca_dom", "ca_gc"):
            x = getattr(self, nom)[cols].copy()
            x[incomplets] = np.nan
            x[x < 0] = np.nan                                    # avoirs massifs : non utilisés
            med = x.where(x > 0).median(axis=1)
            x = x.clip(upper=(3 * med).where(med > 0), axis=0)  # rattrapages : ramenés à 3 x la médiane
            out[nom] = x
        for nom in ("nb_dom", "nb_gc"):
            x = getattr(self, nom)[cols].copy()
            x[incomplets] = np.nan
            out[nom] = x
        return out, incomplets


# =============================================================
# 2. JEU CENTRE x MOIS (variables datées)
# =============================================================
def construire_jeu(B, mois_cibles):
    lignes = []
    for t in mois_cibles:
        F, incomplets = B.fac_avant(t)
        prec = [decaler(t, -k) for k in (1, 2, 3)]
        e_avant = B.enc[[m for m in B.mois_enc if m < t]]
        h6 = e_avant.columns[-6:]
        h12 = e_avant.columns[-12:]
        annee = [m for m in e_avant.columns if m.startswith(str(P("ANNEE_PIC", 2026)))]
        vol_dispo = [m for m in B.mois_vol if m < t]
        for c in B.centres:
            def moy(df, mois):
                vals = [df.at[c, m] for m in mois if m in df.columns and c in df.index]
                vals = [v for v in vals if pd.notna(v)]
                return np.mean(vals) if vals else np.nan
            nbd = [F["nb_dom"].at[c, m] for m in prec[:2] if m in F["nb_dom"].columns and c in F["nb_dom"].index]
            nbd = [v for v in nbd if pd.notna(v)]
            # facturation bimestrielle : abonnés hors GC = facturés sur 2 mois ; GC facturés chaque mois
            gc = moy(F["nb_gc"], prec)
            nb_ab = (np.mean(nbd) * 2 if nbd else np.nan) + (0.0 if np.isnan(gc) else gc)
            y = B.enc.at[c, t] if t in B.enc.columns else np.nan
            vol, vol_statut = np.nan, ""
            if B.groupe[c] == "Intérieur":
                if not vol_dispo:
                    vol_statut = "non encore publié"
                elif c not in B.vol.index or B.vol.loc[c, vol_dispo].isna().all():
                    vol_statut = "manquant (centre absent du fichier de production)"
                else:
                    vol = B.vol.loc[c, vol_dispo[-3:]].mean()
                    vol_statut = "nul (0 déclaré)" if vol == 0 else "réel"
            lignes.append({
                "centre": c, "nom_centre": B.nom.get(c, ""), "groupe": B.groupe[c], "mois": t,
                "encaissement": y,
                "moy6": e_avant.loc[c, h6].mean() if c in e_avant.index else np.nan,
                "nb_mois_hist": int(e_avant.loc[c, h12].notna().sum()) if c in e_avant.index else 0,
                **{f"emp_q{int(round(100 * q))}": e_avant.loc[c, h12].quantile(q) for q in QUANTILES},
                "nb_abonnes": nb_ab,
                "nb_gc": moy(F["nb_gc"], prec),
                "ca_dom_lag1": moy(F["ca_dom"], prec[:1]), "ca_gc_lag1": moy(F["ca_gc"], prec[:1]),
                "ca_dom_lag3": moy(F["ca_dom"], prec), "ca_gc_lag3": moy(F["ca_gc"], prec),
                "ca_dom_brut_lag3": moy(B.ca_dom, prec), "ca_gc_brut_lag3": moy(B.ca_gc, prec),
                "volume": vol, "volume_statut": vol_statut,
                "pic_annee_avant_t": e_avant.loc[c, annee].max() if annee and c in e_avant.index else np.nan,
                "moy12": e_avant.loc[c, h12].mean() if c in e_avant.index else np.nan,
                "mois_fact_incomplets_exclus": ", ".join(m for m in incomplets if m in prec),
            })
    return pd.DataFrame(lignes)


# =============================================================
# 3. RÉGRESSION QUANTILE L1 (même formulation que sklearn QuantileRegressor)
#    min (1/n) Σ ρτ(y - b0 - Xb) + α Σ|b|
# =============================================================
def qr_fit(X, y, tau, alpha):
    n, p = X.shape
    c = np.concatenate([[0.0, 0.0], alpha * np.ones(2 * p), tau / n * np.ones(n), (1 - tau) / n * np.ones(n)])
    I = sparse.eye(n, format="csr")
    A = sparse.hstack([sparse.csr_matrix(np.ones((n, 1))), sparse.csr_matrix(-np.ones((n, 1))),
                       sparse.csr_matrix(X), sparse.csr_matrix(-X), I, -I], format="csr")
    res = linprog(c, A_eq=A, b_eq=y, bounds=(0, None), method="highs")
    if not res.success:
        raise RuntimeError(res.message)
    x = res.x
    return x[0] - x[1], x[2:2 + p] - x[2 + p:2 + 2 * p]


class Modele:
    """Spécification : variables, transformation log, imputation du volume (dans l'apprentissage)."""

    def __init__(self, nom, variables, log=False, volume=True):
        self.nom, self.vars0, self.log, self.volume = nom, variables, log, volume

    def variables(self, groupe):
        v = list(self.vars0)
        if groupe == "Intérieur" and self.volume and "_metier" in self.nom:
            v.append("volume")
        return v

    def lignes_apprentissage(self, d, groupe):
        v = self.variables(groupe)
        ok = d["encaissement"].notna()
        if "volume" in v:
            ok &= d["volume_statut"] != "non encore publié"
        return d[ok & d[[x for x in v if x != "volume"]].notna().all(axis=1)]

    def preparer(self, app, test, groupe):
        v = self.variables(groupe)
        app, test = app.copy(), test.copy()
        if "volume" in v:
            r = (app["volume"] / app["nb_abonnes"].replace(0, np.nan)).median()
            for df in (app, test):
                manq = df["volume"].isna()
                df.loc[manq, "volume"] = r * df.loc[manq, "nb_abonnes"]
                df["volume_impute"] = manq
        Xa, Xt = app[v].astype(float).values, test[v].astype(float).values
        ya = app["encaissement"].values.astype(float)
        if self.log:
            Xa, Xt, ya = np.log1p(np.clip(Xa, 0, None)), np.log1p(np.clip(Xt, 0, None)), np.log1p(np.clip(ya, 0, None))
        mu, sd = Xa.mean(axis=0), Xa.std(axis=0)
        sd[sd == 0] = 1.0
        Xa, Xt = (Xa - mu) / sd, (Xt - mu) / sd
        Xt = np.nan_to_num(Xt)
        echelle = 1.0 if self.log else max(np.mean(np.abs(ya)), 1.0)
        return Xa, ya / echelle, Xt, echelle, v

    def predire(self, app, test, groupe, tau, alpha, retour_coefs=False):
        Xa, ya, Xt, echelle, v = self.preparer(self.lignes_apprentissage(app, groupe), test, groupe)
        b0, b = qr_fit(Xa, ya, tau, alpha)
        pr = (b0 + Xt @ b) * echelle
        pr = np.expm1(pr) if self.log else pr
        pr = np.clip(pr, 0, None)
        if retour_coefs:
            return pr, dict(zip(["constante"] + v, [b0 * echelle] + list(b * echelle)))
        return pr


MODELES = [
    Modele("RQ_hist (moyenne 6 mois seule)", ["moy6"]),
    Modele("RQ_metier (critères seuls)", ["nb_abonnes", "nb_gc", "ca_dom_lag3", "ca_gc_lag3"]),
    Modele("RQ_metier_log (critères seuls, log)", ["nb_abonnes", "nb_gc", "ca_dom_lag3", "ca_gc_lag3"], log=True),
    Modele("RQ_metier_hist (critères + moyenne 6 mois)", ["nb_abonnes", "nb_gc", "ca_dom_lag3", "ca_gc_lag3", "moy6"]),
    Modele("RQ_metier_hist_log (critères + moyenne 6 mois, log)",
           ["nb_abonnes", "nb_gc", "ca_dom_lag3", "ca_gc_lag3", "moy6"], log=True),
    Modele("RQ_metier_hist_lag1 (CA du mois précédent)", ["nb_abonnes", "nb_gc", "ca_dom_lag1", "ca_gc_lag1", "moy6"]),
    Modele("RQ_metier_hist_sans_volume", ["nb_abonnes", "nb_gc", "ca_dom_lag3", "ca_gc_lag3", "moy6"], volume=False),
]
REFERENCES = {"Moyenne des 6 derniers mois": lambda d, q: d["moy6"].values,
              "Quantile empirique des 12 derniers mois": lambda d, q: d[f"emp_q{int(round(100 * q))}"].values}


def pinball(y, q, tau):
    u = y - q
    return np.mean(np.maximum(tau * u, (tau - 1) * u))


def ordonner(preds):
    """Croisements de quantiles : réarrangement (tri croissant des estimations de chaque centre)."""
    M = np.column_stack([preds[q] for q in QUANTILES])
    crois = int((np.diff(M, axis=1) < -0.5).any(axis=1).sum())
    M = np.sort(M, axis=1)
    return {q: M[:, i] for i, q in enumerate(QUANTILES)}, crois


# =============================================================
# 4. VALIDATION CHRONOLOGIQUE (origine glissante)
# =============================================================
def predictions_origine(d, T, alphas_choisis=None):
    """Prévisions pour le mois T, apprises sur les mois < T. alphas_choisis : {(modele, groupe, q): α}."""
    app, test = d[d["mois"] < T], d[d["mois"] == T]
    sortie = []
    for g in GROUPES:
        a_g, t_g = app[app["groupe"] == g], test[test["groupe"] == g]
        base = t_g[["centre", "groupe", "mois", "encaissement", "moy12", "pic_annee_avant_t"]].copy()
        for nom, f in REFERENCES.items():
            preds = {q: f(t_g, q) for q in QUANTILES}
            for q in QUANTILES:
                sortie.append(base.assign(methode=nom, tau=q, alpha=np.nan, prevision=preds[q], croisement=False))
        for M in MODELES:
            for alpha in (ALPHAS if alphas_choisis is None else [None]):
                preds = {}
                for q in QUANTILES:
                    a = alpha if alpha is not None else alphas_choisis[(M.nom, g, q)]
                    preds[q] = M.predire(a_g, t_g, g, q, a)
                if alphas_choisis is not None:
                    Mx = np.column_stack([preds[q] for q in QUANTILES])
                    ligne_croisee = (np.diff(Mx, axis=1) < -0.5).any(axis=1)
                    preds_o, _ = ordonner(preds)
                    for q in QUANTILES:
                        sortie.append(base.assign(methode=M.nom, tau=q, alpha=alphas_choisis[(M.nom, g, q)],
                                                  prevision=preds_o[q], prevision_avant_tri=preds[q],
                                                  croisement=ligne_croisee))
                else:
                    for q in QUANTILES:
                        sortie.append(base.assign(methode=M.nom, tau=q, alpha=alpha, prevision=preds[q]))
    return pd.concat(sortie, ignore_index=True)


def resumer(r, par=()):
    def f(g):
        y, q, tau = g["encaissement"].values, g["prevision"].values, g["_tau"].iloc[0]
        return pd.Series({
            "perte_quantile": pinball(y, q, tau),
            "perte_relative_pct": 100 * pinball(y, q, tau) / np.mean(y),
            "part_reel_<=_estimation_pct": 100 * np.mean(y <= q),
            "taux_atteinte_pct": 100 * np.mean(y >= q),
            "ecart_moyen_MRU": np.mean(y - q),
            "ecart_absolu_moyen_MRU": np.mean(np.abs(y - q)),
            "ecart_absolu_pondere_pct": 100 * np.abs(y - q).sum() / y.sum(),
            "nb_observations": len(y),
        })
    cles = ["methode", "tau", *par]
    return r.assign(_tau=r["tau"]).groupby(cles, observed=True).apply(f).reset_index()


# =============================================================
# 5. RÈGLE DU DG ET OBJECTIFS
# =============================================================
def appliquer_regle(obj, pic, moy12, sens=None):
    coef = P("COEF_PLAFOND", 1.2)
    sens = sens or P("SENS_REGLE_DG", "minimum")
    seuil = coef * pic
    if sens == "minimum":
        apres = np.where(np.isnan(seuil), obj, np.maximum(obj, seuil))
    else:
        apres = np.where(np.isnan(seuil), obj, np.minimum(obj, seuil))
    prog = P("QR_PLAFOND_PROGRESSION")       # +50 % : uniquement s'il est demandé explicitement
    conflit = np.zeros(len(obj), bool)
    if prog is not None:
        cap = (1 + prog) * moy12
        conflit = (sens == "minimum") & (seuil > cap)
    return seuil, apres, apres - obj, conflit


def main():
    print("=" * 66)
    print("  RÉGRESSION QUANTILE RÉGULARISÉE — objectifs d'encaissement par centre")
    print("=" * 66)
    B = Brut()
    mois_obj = decaler(P("MOIS_FIN"), 1)
    print(f"\n[1] Périodes disponibles : encaissements {B.mois_enc[0]} → {B.mois_enc[-1]} | facturation "
          f"{B.mois_fac[0]} → {B.mois_fac[-1]} | production {B.mois_vol[0] if B.mois_vol else '—'} → "
          f"{B.mois_vol[-1] if B.mois_vol else '—'}")
    # Premier mois où toutes les variables existent : 3 mois de facturation antérieurs
    premier = decaler(B.mois_fac[0], 3)
    mois_lignes = [m for m in B.mois_enc if m >= premier] + [mois_obj]
    d = construire_jeu(B, mois_lignes)
    vol_debut = decaler(B.mois_vol[0], 1) if B.mois_vol else None
    # Mois testés : volume publié (mêmes mois pour tous les modèles) et au moins 2 mois d'apprentissage
    debut_test = max(decaler(premier, 2), vol_debut or premier)
    debut_test = max(debut_test, decaler(vol_debut, 2) if vol_debut else debut_test)
    mois_test = [m for m in B.mois_enc if m >= debut_test]
    selection, finaux = mois_test[:-NB_MOIS_FINAUX], mois_test[-NB_MOIS_FINAUX:]
    note(f"Lignes centre x mois : {premier} → {mois_obj} ({len(d)} lignes). Mois testés {mois_test[0]} → "
         f"{mois_test[-1]} : choix de α sur {selection[0]} → {selection[-1]}, période finale réservée "
         f"{finaux[0]} → {finaux[-1]}")
    note("Effectifs : nombre d'abonnés FACTURÉS (daté, facturation.csv) ; hors GC facturés sur 2 mois "
         "(facturation bimestrielle), GC facturés chaque mois. Les effectifs actifs actuels ne sont pas utilisés.")

    print("\n[2] Choix de α (validation chronologique, période de sélection)")
    r_sel = pd.concat([predictions_origine(d, T) for T in selection], ignore_index=True)
    rs = r_sel[r_sel["alpha"].notna()].assign(_tau=lambda x: x["tau"])
    perte = rs.groupby(["methode", "groupe", "tau", "alpha"]).apply(
        lambda g: pinball(g["encaissement"].values, g["prevision"].values, g["_tau"].iloc[0])).rename("perte").reset_index()
    best = perte.loc[perte.groupby(["methode", "groupe", "tau"])["perte"].idxmin()]
    alphas = {(r.methode, r.groupe, r.tau): r.alpha for r in best.itertuples()}
    print(best.groupby(["methode"])["alpha"].agg(lambda s: ", ".join(f"{a:g}" for a in s)).to_string())

    print("\n[3] Validation avec α choisi")
    r_all = pd.concat([predictions_origine(d, T, alphas) for T in mois_test], ignore_index=True)
    r_all["periode"] = np.where(r_all["mois"].isin(finaux), "finale (réservée)", "sélection")
    taille = d[d["mois"] == mois_test[0]].set_index("centre")["moy12"]
    classes = pd.qcut(taille, 3, labels=["petits", "moyens", "grands"])
    r_all["taille"] = r_all["centre"].map(classes)
    fin = r_all[r_all["periode"] == "finale (réservée)"]
    v_fin = resumer(fin)
    v_sel = resumer(r_all[r_all["periode"] == "sélection"])
    v_groupe = resumer(r_all, ("groupe",))
    v_taille = resumer(r_all, ("taille",))
    # Règle du DG appliquée dans le passé (pic connu à la date)
    rr = r_all.copy()
    s, a, sup, _ = appliquer_regle(rr["prevision"].values, rr["pic_annee_avant_t"].values, rr["moy12"].values)
    rr["prevision"] = a
    v_regle = resumer(rr[rr["pic_annee_avant_t"].notna()])
    for q in QUANTILES:
        x = v_fin[v_fin["tau"] == q].sort_values("perte_quantile")
        print(f"  τ = {q:.2f} (période finale) — meilleure : {x.iloc[0]['methode']} "
              f"(atteinte {x.iloc[0]['taux_atteinte_pct']:.0f} %, attendu {100 * (1 - q):.0f} %)")

    # Méthode retenue : plus faible perte quantile moyenne sur la période de SÉLECTION (pas la finale)
    rang = v_sel.groupby("methode")["perte_relative_pct"].mean().sort_values()
    retenue = P("QR_METHODE", "auto")
    retenue = rang.index[0] if retenue == "auto" else retenue
    note(f"Méthode retenue (perte quantile moyenne la plus faible sur la période de sélection) : {retenue}")

    # Croisements
    crois = r_all[r_all["methode"].str.startswith("RQ")].groupby("methode").apply(
        lambda g: g.drop_duplicates(["centre", "mois"])["croisement"].mean() * 100).rename("lignes_avec_croisement_pct")

    print(f"\n[4] Objectifs de {mois_obj}")
    app = d[d["mois"] < mois_obj]
    cible = d[d["mois"] == mois_obj].reset_index(drop=True)

    def objectifs_finaux(app_, methode):
        preds, coefs = {}, []
        for g in GROUPES:
            msk = (cible["groupe"] == g).values
            for q in QUANTILES:
                if methode in REFERENCES:
                    v = REFERENCES[methode](cible[msk], q)
                else:
                    M = next(m for m in MODELES if m.nom == methode)
                    v, cf = M.predire(app_[app_["groupe"] == g], cible[msk], g, q, alphas[(methode, g, q)], True)
                    coefs.append({"groupe": g, "tau": q, "alpha": alphas[(methode, g, q)], **cf})
                preds.setdefault(q, np.zeros(len(cible)))[msk] = v
        po, nb = ordonner(preds)
        return po, nb, coefs

    preds, nb_crois, coefs = objectifs_finaux(app, retenue)
    # Stabilité : apprentissage arrêté 1 et 2 mois plus tôt, et fenêtre raccourcie
    stab = []
    variantes = {f"apprentissage jusqu'à {decaler(mois_obj, -2)}": app[app["mois"] < decaler(mois_obj, -1)],
                 f"apprentissage jusqu'à {decaler(mois_obj, -3)}": app[app["mois"] < decaler(mois_obj, -2)],
                 "6 derniers mois d'apprentissage seulement": app[app["mois"] >= decaler(mois_obj, -6)]}
    for meth in [retenue] + [m for m in ["Quantile empirique des 12 derniers mois"] if m != retenue]:
        ref_p = objectifs_finaux(app, meth)[0]
        for nom_v, app_v in variantes.items():
            pv = objectifs_finaux(app_v, meth)[0]
            for q in QUANTILES:
                dlt = 100 * np.abs(pv[q] / np.where(ref_p[q] > 0, ref_p[q], np.nan) - 1)
                stab.append({"methode": meth, "variante": nom_v, "tau": q,
                             "variation_mediane_pct": np.nanmedian(dlt), "variation_p90_pct": np.nanpercentile(dlt, 90),
                             "variation_total_pct": 100 * (pv[q].sum() / ref_p[q].sum() - 1)})
    stab = pd.DataFrame(stab)

    obj = cible[["centre", "nom_centre", "groupe", "moy6", "moy12", "pic_annee_avant_t", "volume_statut"]].copy()
    scen = []
    for q in QUANTILES:
        k = int(round(100 * q))
        s, a, sup, conf = appliquer_regle(preds[q], obj["pic_annee_avant_t"].values, obj["moy12"].values)
        obj[f"Q{k} modèle"] = preds[q]
        obj[f"Q{k} après règle DG"] = a
        obj[f"Q{k} supplément règle DG"] = sup
        if P("QR_PLAFOND_PROGRESSION") is not None:
            obj[f"Q{k} conflit avec +{P('QR_PLAFOND_PROGRESSION'):.0%}"] = conf
        hist = v_fin[(v_fin["methode"] == retenue) & (v_fin["tau"] == q)]
        hist_r = v_regle[(v_regle["methode"] == retenue) & (v_regle["tau"] == q)]
        scen.append({"Scénario": f"Q{k}", "Total modèle": preds[q].sum(),
                     f"Minimum {P('COEF_PLAFOND', 1.2):g} x pic (somme)": s.sum(),
                     "Total après règle DG": a.sum(), "Supplément imposé par la règle": sup.sum(),
                     "Pour comparaison : total si la règle est un MAXIMUM":
                         appliquer_regle(preds[q], obj["pic_annee_avant_t"].values, obj["moy12"].values,
                                         "maximum" if P("SENS_REGLE_DG", "minimum") == "minimum" else "minimum")[1].sum(),
                     "Centres relevés par la règle": int((sup > 0.5).sum()),
                     "Atteinte historique du modèle (période finale) %": hist["taux_atteinte_pct"].iloc[0],
                     "Atteinte attendue si bien calibré %": 100 * (1 - q),
                     "Atteinte historique après règle DG %": hist_r["taux_atteinte_pct"].iloc[0] if len(hist_r) else np.nan,
                     "Conflits avec le plafond +50 %": int(conf.sum()) if P("QR_PLAFOND_PROGRESSION") is not None else "non appliqué",
                     "Repère facultatif 150 M": 150_000_000})
    obj[f"Minimum {P('COEF_PLAFOND', 1.2):g} x pic {P('ANNEE_PIC', 2026)}"] = P("COEF_PLAFOND", 1.2) * obj["pic_annee_avant_t"]
    scen = pd.DataFrame(scen)
    print(scen[["Scénario", "Total modèle", "Total après règle DG", "Atteinte historique du modèle (période finale) %"]]
          .to_string(index=False))

    # ---- Export
    os.makedirs(D_OUT, exist_ok=True)
    chemin = os.path.join(D_OUT, f"objectifs_quantile_{mois_obj}.xlsx")
    ren = {"methode": "Méthode", "tau": "Quantile", "perte_quantile": "Perte quantile (MRU)",
           "perte_relative_pct": "Perte quantile / encaissement moyen (%)",
           "part_reel_<=_estimation_pct": "Réalisations ≤ estimation (%)", "taux_atteinte_pct": "Taux d'atteinte (%)",
           "ecart_moyen_MRU": "Écart moyen réel - estimation (MRU)", "ecart_absolu_moyen_MRU": "Écart absolu moyen (MRU)",
           "ecart_absolu_pondere_pct": "Écart absolu pondéré (%)", "nb_observations": "Observations",
           "groupe": "Groupe", "taille": "Taille du centre"}
    notes = pd.DataFrame({"Notes": NOTES + [
        f"Règle du DG : SENS_REGLE_DG = '{P('SENS_REGLE_DG', 'minimum')}' — hypothèse de travail à confirmer "
        f"(minimum : objectif = max(modèle, {P('COEF_PLAFOND', 1.2):g} x pic réel {P('ANNEE_PIC', 2026)} connu à la date).",
        "Les coefficients sont exprimés pour des variables standardisées ; ce ne sont pas des pourcentages de répartition.",
        "Un quantile 70 % bien calibré est dépassé dans environ 30 % des cas : ce n'est pas une probabilité de réussite de 70 %.",
        "Les encaissements ne sont ni écrêtés ni corrigés : les mois élevés sont réels et nécessaires aux quantiles hauts.",
        "Facturation : mois incomplets (CA national < 50 % de la médiane), avoirs négatifs et rattrapages (> 3 x médiane) "
        "traités avec les seules données antérieures au mois prévu ; valeurs brutes conservées (colonnes *_brut_*).",
        "Volume produit : utilisé à partir du mois suivant sa publication ; centre absent du fichier = donnée manquante, "
        "imputée dans chaque apprentissage (médiane volume / abonné facturé x abonnés facturés), jamais mise à 0.",
        f"Croisements de quantiles pour {mois_obj} : {nb_crois} centre(s), corrigés par réarrangement (tri croissant).",
        "Pour les mois testés, la règle du DG utilise le pic de l'année connu avant le mois (non disponible en janvier).",
    ]})
    with pd.ExcelWriter(chemin, engine="openpyxl") as xw:
        notes.to_excel(xw, sheet_name="Lisez-moi", index=False)
        scen.to_excel(xw, sheet_name="Scénarios", index=False)
        obj.sort_values(f"Q{int(round(100 * QUANTILES[0]))} modèle", ascending=False).to_excel(
            xw, sheet_name="Objectifs par centre", index=False)
        v_fin.rename(columns=ren).to_excel(xw, sheet_name="Validation période finale", index=False)
        v_sel.rename(columns=ren).to_excel(xw, sheet_name="Validation sélection", index=False)
        v_groupe.rename(columns=ren).to_excel(xw, sheet_name="Validation par groupe", index=False)
        v_taille.rename(columns=ren).to_excel(xw, sheet_name="Validation par taille", index=False)
        v_regle.rename(columns=ren).to_excel(xw, sheet_name="Validation avec règle DG", index=False)
        stab.to_excel(xw, sheet_name="Stabilité", index=False)
        # Mois élevés à vérifier (non modifiés : ils servent aux quantiles hauts)
        E = B.enc
        med = E.median(axis=1)
        haut = E.gt(2.5 * med, axis=0)
        pd.DataFrame([(c, B.nom.get(c, ""), m, E.at[c, m], med[c]) for c in E.index for m in E.columns if haut.at[c, m]],
                     columns=["Centre", "Nom centre", "Mois", "Encaissement (conservé)", "Médiane du centre"]
                     ).to_excel(xw, sheet_name="Mois élevés à vérifier", index=False)
        crois.reset_index().to_excel(xw, sheet_name="Croisements", index=False)
        best.rename(columns={"perte": "perte de sélection"}).to_excel(xw, sheet_name="Alpha choisi", index=False)
        pd.DataFrame(coefs).to_excel(xw, sheet_name="Coefficients", index=False)
        d.to_excel(xw, sheet_name="Jeu centre x mois", index=False)
        r_all.to_excel(xw, sheet_name="Prévisions testées", index=False)
    # ---- Objectifs de chaque centre (console)
    ks = [int(round(100 * q)) for q in QUANTILES]
    print(f"\n  OBJECTIFS DE CHAQUE CENTRE — {mois_obj} (montants du modèle, avant règle du DG)")
    print(f"  {'Centre':<32}" + "".join(f"{'Q' + str(k):>13}" for k in ks) + f"{'1,2 x pic':>13}")
    for _, l in obj.sort_values(f"Q{ks[0]} modèle", ascending=False).iterrows():
        print(f"  {str(l['centre']):<6} {str(l['nom_centre'])[:25]:<25}"
              + "".join(f"{l[f'Q{k} modèle']:>13,.0f}" for k in ks)
              + f"{P('COEF_PLAFOND', 1.2) * l['pic_annee_avant_t']:>13,.0f}")
    print(f"  {'TOTAL':<32}" + "".join(f"{obj[f'Q{k} modèle'].sum():>13,.0f}" for k in ks)
          + f"{(P('COEF_PLAFOND', 1.2) * obj['pic_annee_avant_t']).sum():>13,.0f}")
    print(f"\n  ✅ {chemin}  (onglet « Objectifs par centre »)")
    return dict(d=d, r_all=r_all, v_fin=v_fin, v_sel=v_sel, v_groupe=v_groupe, v_taille=v_taille,
                v_regle=v_regle, stab=stab, crois=crois, scen=scen, obj=obj, retenue=retenue, alphas=alphas,
                coefs=coefs, mois_test=mois_test, finaux=finaux)


if __name__ == "__main__":
    main()
