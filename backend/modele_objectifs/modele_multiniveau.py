"""
MODÈLE MULTINIVEAU AMÉLIORÉ — objectifs mensuels d'encaissement par centre
==========================================================================
Lancement :  python modele_multiniveau.py
Sortie    :  sorties/multiniveau_AAAA-MM.xlsx
             (+ sorties/objectifs_AAAA-MM.csv pour l'écran Objectifs si MN_ECRAN_APP = True)

Ce qui change par rapport au modèle multiniveau actuel (modele_objectifs.py)
-------------------------------------------------------------------------
1. Le modèle PRÉVOIT le mois à venir : chaque ligne (centre, mois t) n'utilise que des données
   antérieures à t (CA décalé, abonnés facturés, encaissements récents). L'ancien modèle utilisait
   le CA du mois même, inconnu au moment de fixer l'objectif.
2. Plusieurs spécifications sont comparées par validation chronologique à origine glissante
   (Hyndman & Athanasopoulos, FPP3, « evaluation on a rolling forecasting origin »), contre la
   moyenne des 6 derniers mois. La spécification retenue est celle qui fait le mieux sur la période
   de sélection ; les 2 derniers mois sont réservés au contrôle final.
3. Diagnostics des résidus (dispersion selon la taille, autocorrélation) ; les adaptations
   (logarithme, correction AR(1), saisonnalité) ne sont retenues que si la validation les justifie.
4. Chaque centre reçoit : moyenne récente, prévision, intervalle à 80 %, effort supplémentaire,
   plafonds et objectif final ; statuts recalculés après tous les ajustements.

Modèle (par groupe : Nouakchott + Nouadhibou / Intérieur)
    encaissement(c,t) = β0 + Σ βk x_k(c,t) + u_c + e(c,t)      u_c ~ N(0, σu²), e ~ N(0, σe²)
β estimés par moindres carrés généralisés, σu² et σe² par REML ; u_c = effet du centre (BLUP).
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

warnings.filterwarnings("ignore")
ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import parametres as C  # noqa: E402


def P(nom, defaut=None):
    return getattr(C, nom, defaut)


D_IN = os.path.join(ICI, P("DOSSIER_DONNEES", "donnees"))
D_OUT = os.path.join(ICI, P("DOSSIER_SORTIES", "sorties"))
GROUPES = {"Nouakchott + Nouadhibou": ["NKTT", "NDB"], "Intérieur": ["INTERIEUR"]}
TARIFS_GC = [str(t) for t in P("TARIFS_GC", ["2220", "1110", "4440"])]
SEUIL_EXC = P("SEUIL_EXCEPTIONNEL", 2.5) or 2.5
SEUIL_CA = P("SEUIL_CA_ANORMAL", 3.0) or 3.0
SEUIL_INC = P("SEUIL_MOIS_INCOMPLET", 0.5) or 0.5
Z80 = 1.2816
COUV = "Couverture de l'intervalle 80 % (%)"
NOTES = []


def note(m):
    NOTES.append(m)
    print(f"  • {m}")


def decaler(m, n):
    a, mm = map(int, m.split("-"))
    k = a * 12 + mm - 1 + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def lire(nom):
    return pd.read_csv(os.path.join(D_IN, nom), sep=";", encoding="utf-8-sig",
                       dtype={"centre": str, "groupe_tarif": str})


# =============================================================
# 1. DONNÉES BRUTES ET CONTRÔLE DE QUALITÉ
# =============================================================
class Donnees:
    def __init__(self):
        F = P("FICHIERS")
        e = lire(F["encaissements"])
        for c in ("montant", "montant_prives", "montant_etat"):
            if c in e.columns:
                e[c] = pd.to_numeric(e[c], errors="coerce")
        self.enc = e.pivot_table(index="centre", columns="mois", values="montant", aggfunc="sum")
        self.etat_dispo = {"montant_prives", "montant_etat"} <= set(e.columns)
        if self.etat_dispo:
            self.enc_p = e.pivot_table(index="centre", columns="mois", values="montant_prives", aggfunc="sum")
            self.enc_e = e.pivot_table(index="centre", columns="mois", values="montant_etat", aggfunc="sum")
        f = lire(F["facturation"])
        f["groupe_tarif"] = f["groupe_tarif"].astype(str).str.replace(r"\.0$", "", regex=True)
        f["ca"] = pd.to_numeric(f["ca"], errors="coerce")
        f["nb_abonnes"] = pd.to_numeric(f["nb_abonnes"], errors="coerce")
        f["gc"] = f["groupe_tarif"].isin(TARIFS_GC)
        self.fac_brut = f
        piv = lambda m, col: f[m].pivot_table(index="centre", columns="mois", values=col, aggfunc="sum")
        self.mois_fac = sorted(f["mois"].unique())
        self.ca_gc = piv(f["gc"], "ca").reindex(columns=self.mois_fac)
        self.ca_dom = piv(~f["gc"], "ca").reindex(columns=self.mois_fac)
        self.nb_gc = piv(f["gc"], "nb_abonnes").reindex(columns=self.mois_fac)
        self.nb_dom = piv(~f["gc"], "nb_abonnes").reindex(columns=self.mois_fac)
        z = lire(F["zones"]).set_index("centre")
        self.nom = z["nom_centre"]
        self.groupe = z["zone"].map(lambda v: next(g for g, zs in GROUPES.items() if v in zs))
        try:
            pr = pd.read_csv(os.path.join(D_IN, F["production"]), sep=";", dtype={"centre": str})
            pr["volume"] = pd.to_numeric(pr["volume"], errors="coerce")
            self.vol = pr.pivot_table(index="centre", columns="mois", values="volume", aggfunc="sum")
        except FileNotFoundError:
            self.vol = pd.DataFrame()
        self.centres = sorted(set(self.enc.index) & set(z.index), key=lambda c: int(c) if c.isdigit() else 0)
        self.mois_enc = sorted(self.enc.columns)
        self.mois_vol = sorted(self.vol.columns) if len(self.vol) else []

    def qualite(self):
        """Contrôles : données manquantes / nulles, mois incomplets, factures négatives,
        CA anormaux, encaissements exceptionnels. Aucune donnée brute n'est modifiée ici."""
        lignes = []
        E = self.enc.reindex(self.centres)
        tot_e = E.sum()
        for m in self.mois_enc:
            r = tot_e[m] / tot_e.median()
            if r < SEUIL_INC:
                lignes.append(("Encaissements", "Mois national incomplet", "", "", m, tot_e[m], "",
                               f"total < {SEUIL_INC:.0%} de la médiane : exclu"))
        for c in self.centres:
            for m in self.mois_enc:
                v = E.at[c, m]
                if pd.isna(v):
                    lignes.append(("Encaissements", "Donnée manquante", c, self.nom.get(c, ""), m, np.nan, "",
                                   "centre absent ce mois : non utilisé (≠ 0)"))
                elif v == 0:
                    lignes.append(("Encaissements", "Valeur nulle", c, self.nom.get(c, ""), m, 0.0, "",
                                   "0 déclaré : conservé"))
        med = E.median(axis=1)
        for c in self.centres:
            for m in self.mois_enc:
                v = E.at[c, m]
                if pd.notna(v) and med[c] > 0 and v > SEUIL_EXC * med[c]:
                    lignes.append(("Encaissements", "Mois exceptionnel", c, self.nom.get(c, ""), m, v,
                                   SEUIL_EXC * med[c],
                                   f"> {SEUIL_EXC:g} x médiane : conservé comme cible, écrêté seulement dans "
                                   "les variables explicatives et l'estimation"))
        tot_f = (self.ca_dom.fillna(0) + self.ca_gc.fillna(0)).sum()
        for m in self.mois_fac:
            if tot_f[m] < SEUIL_INC * tot_f.median():
                lignes.append(("Facturation", "Mois national incomplet", "", "", m, tot_f[m], "",
                               "CA du mois non utilisé (facturation non terminée)"))
        for nom, X in (("CA hors GC", self.ca_dom), ("CA GC", self.ca_gc)):
            X = X.reindex(self.centres)
            medp = X.where(X > 0).median(axis=1)
            for c in self.centres:
                for m in self.mois_fac:
                    v = X.at[c, m]
                    if pd.isna(v):
                        continue
                    if v < 0:
                        lignes.append(("Facturation", f"{nom} négatif (avoir)", c, self.nom.get(c, ""), m, v, np.nan,
                                       "non utilisé : annulation/régularisation, pas une facturation du mois"))
                    elif medp[c] > 0 and v > SEUIL_CA * medp[c]:
                        lignes.append(("Facturation", f"{nom} anormal (rattrapage)", c, self.nom.get(c, ""), m, v,
                                       SEUIL_CA * medp[c], f"ramené à {SEUIL_CA:g} x médiane des mois positifs"))
        inter = [c for c in self.centres if self.groupe.get(c) == "Intérieur"]
        for c in inter:
            if c not in self.vol.index or self.vol.loc[c].isna().all():
                lignes.append(("Production", "Donnée manquante", c, self.nom.get(c, ""), "", np.nan, "",
                               "centre absent du fichier : volume estimé (médiane m³/abonné), jamais mis à 0"))
            elif (self.vol.loc[c].fillna(-1) == 0).any():
                lignes.append(("Production", "Valeur nulle", c, self.nom.get(c, ""), "", 0.0, "",
                               "0 déclaré : conservé"))
        q = pd.DataFrame(lignes, columns=["Source", "Contrôle", "Centre", "Nom centre", "Mois", "Valeur brute",
                                          "Valeur retenue", "Traitement et justification"])
        return q

    # ---- facturation corrigée avec les seules données antérieures à t
    def fac_avant(self, t):
        cols = [m for m in self.mois_fac if m < t]
        tot = (self.ca_dom[cols].fillna(0) + self.ca_gc[cols].fillna(0)).sum()
        inc = list(tot.index[tot < SEUIL_INC * tot.median()]) if len(cols) >= 3 else []
        out = {}
        for nom in ("ca_dom", "ca_gc"):
            x = getattr(self, nom)[cols].copy()
            x[inc] = np.nan
            x[x < 0] = np.nan
            medp = x.where(x > 0).median(axis=1)
            out[nom] = x.clip(upper=(SEUIL_CA * medp).where(medp > 0), axis=0)
        for nom in ("nb_dom", "nb_gc"):
            x = getattr(self, nom)[cols].copy()
            x[inc] = np.nan
            out[nom] = x
        return out


def moy(df, c, mois):
    if c not in df.index:
        return np.nan
    v = [df.at[c, m] for m in mois if m in df.columns]
    v = [x for x in v if pd.notna(x)]
    return float(np.mean(v)) if v else np.nan


def construire_lignes(D, mois_cibles):
    """Une ligne = un centre x un mois t ; variables calculées avec les données < t."""
    lignes = []
    for t in mois_cibles:
        F = D.fac_avant(t)
        p = [decaler(t, -k) for k in range(1, 7)]
        e_av = D.enc[[m for m in D.mois_enc if m < t]]
        med = e_av.median(axis=1)
        e_cap = e_av.clip(upper=SEUIL_EXC * med, axis=0)
        if D.etat_dispo:
            ep_av = D.enc_p[[m for m in D.mois_enc if m < t and m in D.enc_p.columns]]
            ep_cap = ep_av.clip(upper=SEUIL_EXC * ep_av.median(axis=1), axis=0)
            ee_av = D.enc_e[[m for m in D.mois_enc if m < t and m in D.enc_e.columns]]
        for c in D.centres:
            gc = moy(F["nb_gc"], c, p[:3])
            nbd = [F["nb_dom"].at[c, m] for m in p[:2] if c in F["nb_dom"].index and m in F["nb_dom"].columns]
            nbd = [x for x in nbd if pd.notna(x)]
            lignes.append({
                "centre": c, "groupe": D.groupe[c], "mois": t,
                "y": D.enc.at[c, t] if (c in D.enc.index and t in D.enc.columns) else np.nan,
                # facturation bimestrielle : abonnés hors GC = facturés sur 2 mois ; GC facturés chaque mois
                "nb_abonnes": (np.mean(nbd) * 2 if nbd else np.nan) + (0 if np.isnan(gc) else gc),
                "nb_gc": gc,
                **{f"ca_dom_{k}": moy(F["ca_dom"], c, p[:n]) for k, n in (("L1", 1), ("M3", 3), ("M6", 6))},
                **{f"ca_gc_{k}": moy(F["ca_gc"], c, p[:n]) for k, n in (("L1", 1), ("M3", 3), ("M6", 6))},
                "enc_recent": e_cap.loc[c, e_cap.columns[-6:]].mean() if c in e_cap.index else np.nan,
                "y_p": (D.enc_p.at[c, t] if D.etat_dispo and c in D.enc_p.index and t in D.enc_p.columns else np.nan),
                "enc_recent_p": (ep_cap.loc[c, ep_cap.columns[-6:]].mean()
                                 if D.etat_dispo and c in ep_cap.index else np.nan),
                # État : paiements irréguliers -> prévu à part, par sa moyenne des 12 derniers mois
                "etat_prev": (ee_av.loc[c, ee_av.columns[-12:]].fillna(0).mean()
                              if D.etat_dispo and c in ee_av.index else 0.0),
                "dernier_mois": e_av.loc[c, e_av.columns[-1]] if c in e_av.index and e_av.shape[1] else np.nan,
            })
    return pd.DataFrame(lignes)


def indices_saisonniers(D, T):
    """Indice de chaque mois calendaire, calculé sur les encaissements nationaux antérieurs à T :
    total du mois / moyenne des 12 mois qui le précèdent, moyenné sur les années disponibles."""
    tot = D.enc.reindex(D.centres).sum()
    tot = tot[[m for m in tot.index if m < T]]
    r = {}
    ms = list(tot.index)
    for i, m in enumerate(ms):
        if i >= 12:
            r.setdefault(m[5:7], []).append(tot[m] / tot.iloc[i - 12:i].mean())
    if not r or min(len(v) for v in r.values()) < 2 or len(r) < 12:
        return None
    idx = {k: np.mean(v) for k, v in r.items()}
    s = np.mean(list(idx.values()))
    return {k: v / s for k, v in idx.items()}


# =============================================================
# 2. MODÈLE LINÉAIRE MIXTE (intercept aléatoire, REML)
# =============================================================
class Mixte:
    def fit(self, y, X, grp):
        self.cols = X.shape[1]
        g = pd.Series(grp).astype(str).values
        self.niveaux, inv = np.unique(g, return_inverse=True)
        n_i = np.bincount(inv)
        A = np.column_stack([np.ones(len(y)), X])
        p = A.shape[1]
        N = len(y)
        ybar = np.bincount(inv, weights=y) / n_i
        Abar = np.vstack([np.bincount(inv, weights=A[:, j]) / n_i for j in range(p)]).T

        def transf(gam):
            th = 1 - np.sqrt(1 / (1 + n_i * gam))
            return y - th[inv] * ybar[inv], A - th[inv][:, None] * Abar[inv]

        def nreml(lg):
            gam = np.exp(lg)
            ys, As = transf(gam)
            b, *_ = np.linalg.lstsq(As, ys, rcond=None)
            rss = ((ys - As @ b) ** 2).sum()
            s2 = rss / (N - p)
            sign, ld = np.linalg.slogdet(As.T @ As)
            return 0.5 * ((N - p) * np.log(s2) + np.log(1 + n_i * gam).sum() + ld)

        res = minimize_scalar(nreml, bounds=(-12, 8), method="bounded")
        gam = np.exp(res.x)
        ys, As = transf(gam)
        b, *_ = np.linalg.lstsq(As, ys, rcond=None)
        s2 = ((ys - As @ b) ** 2).sum() / (N - p)
        self.beta, self.s2e, self.s2u = b, s2, gam * s2
        self.cov_beta = s2 * np.linalg.pinv(As.T @ As)
        r = y - A @ b
        k = n_i * gam / (1 + n_i * gam)
        self.u = dict(zip(self.niveaux, k * np.bincount(inv, weights=r) / n_i))
        self.var_u = dict(zip(self.niveaux, self.s2u * (1 - k)))
        self.resid = y - A @ b - k[inv] * (np.bincount(inv, weights=r) / n_i)[inv]
        return self

    def predire(self, X, grp):
        A = np.column_stack([np.ones(len(X)), X])
        fixe = A @ self.beta
        u = np.array([self.u.get(str(g), 0.0) for g in grp])
        vu = np.array([self.var_u.get(str(g), self.s2u) for g in grp])
        var = self.s2e + vu + np.einsum("ij,jk,ik->i", A, self.cov_beta, A)
        return fixe + u, fixe, np.sqrt(var)


class Spec:
    def __init__(self, nom, ca="M3", recent=False, abonnes=True, log=False, saison=False, ar1=False, etat=None):
        self.nom, self.ca, self.recent, self.abonnes = nom, ca, recent, abonnes
        self.log, self.saison, self.ar1 = log, saison, ar1
        self.etat = P("MN_ETAT_SEPARE", True) if etat is None else etat

    def variables(self, groupe):
        v = (["nb_abonnes"] if self.abonnes else []) + ["nb_gc", f"ca_dom_{self.ca}", f"ca_gc_{self.ca}"]
        if groupe == "Intérieur":
            v.append("volume")
        if self.recent:
            v.append("enc_recent_p" if self.etat else "enc_recent")
        return v


SPECS = [
    Spec("MN — CA du mois précédent", ca="L1"),
    Spec("MN — CA moyen des 3 mois précédents", ca="M3"),
    Spec("MN — CA moyen des 6 mois précédents", ca="M6"),
    Spec("MN — CA 3 mois + encaissements récents", ca="M3", recent=True),
    Spec("MN — CA 3 mois + encaissements récents, sans abonnés", ca="M3", recent=True, abonnes=False),
    Spec("MN — CA 3 mois + encaissements récents, en logarithme", ca="M3", recent=True, log=True),
    Spec("MN — CA 3 mois + encaissements récents + saisonnalité", ca="M3", recent=True, saison=True),
    Spec("MN — CA 3 mois + encaissements récents + correction AR(1)", ca="M3", recent=True, ar1=True),
    Spec("MN — CA 3 mois + encaissements récents, État NON séparé", ca="M3", recent=True, etat=False),
]
BASE = "Moyenne des 6 derniers mois"
BASE_S = "Moyenne des 6 derniers mois x saisonnalité"


def moy6_saison(D, test, T, idx_s):
    """Moyenne des 6 derniers mois désaisonnalisés, multipliée par l'indice du mois prévu."""
    if not idx_s:
        return test["enc_recent"].values
    E = D.enc
    der6 = [m for m in D.mois_enc if m < T][-6:]
    med = E[[m for m in D.mois_enc if m < T]].median(axis=1)
    out = []
    for c in test["centre"]:
        v = E.loc[c, der6].clip(upper=SEUIL_EXC * med[c]) if c in E.index else pd.Series(dtype=float)
        ds = [v[m] / idx_s.get(m[5:7], 1.0) for m in der6 if pd.notna(v.get(m))]
        out.append(np.mean(ds) * idx_s.get(T[5:7], 1.0) if ds else np.nan)
    return np.array(out)


def ajouter_volume(df, D, T):
    """Volume produit : caractéristique du centre (moyenne des mois publiés avant T) ;
    donnée manquante estimée (médiane m³ / abonné facturé x abonnés facturés), jamais mise à 0."""
    dispo = [m for m in D.mois_vol if m < T]
    v = D.vol[dispo].mean(axis=1) if dispo else pd.Series(dtype=float)
    df = df.copy()
    df["volume"] = df["centre"].map(v).astype(float)
    df.loc[df["groupe"] != "Intérieur", "volume"] = 0.0
    inter = df["groupe"] == "Intérieur"
    ok = inter & df["volume"].notna() & (df["volume"] > 0)
    r = (df.loc[ok, "volume"] / df.loc[ok, "nb_abonnes"]).median()
    manq = inter & df["volume"].isna()
    df["volume_estime"] = manq
    df.loc[manq, "volume"] = r * df.loc[manq, "nb_abonnes"]
    return df


def ajuster_predire(spec, app, test, groupe, idx_s):
    """Apprentissage sur `app` (mois < T), prévision pour `test`. Retourne prévision, potentiel, écart-type."""
    v = spec.variables(groupe)
    cible = "y_p" if spec.etat else "y"
    a = app[(app["groupe"] == groupe)].dropna(subset=v + [cible]).copy()
    t = test[test["groupe"] == groupe].copy()
    for c in v:   # variable manquante en prévision : dernière valeur connue du centre, sinon médiane
        manq = t[c].isna()
        if manq.any():
            der = a.sort_values("mois").groupby("centre")[c].last()
            t.loc[manq, c] = t.loc[manq, "centre"].map(der)
            t[c] = t[c].fillna(a[c].median())
    # cible d'estimation : encaissement écrêté (médiane du centre sur la période d'apprentissage)
    med = a.groupby("centre")[cible].transform("median")
    ya = np.minimum(a[cible].values, SEUIL_EXC * med.values)
    fs_a = np.array([idx_s.get(m[5:7], 1.0) for m in a["mois"]]) if (spec.saison and idx_s) else np.ones(len(a))
    fs_t = np.array([idx_s.get(m[5:7], 1.0) for m in t["mois"]]) if (spec.saison and idx_s) else np.ones(len(t))
    ya = ya / fs_a
    Xa, Xt = a[v].values.astype(float), t[v].values.astype(float)
    if spec.log:
        ya, Xa, Xt = np.log1p(np.clip(ya, 0, None)), np.log1p(np.clip(Xa, 0, None)), np.log1p(np.clip(Xt, 0, None))
    mu, sd = Xa.mean(0), Xa.std(0)
    sd[sd == 0] = 1
    m = Mixte().fit(ya, (Xa - mu) / sd, a["centre"].values)
    pr, fixe, s = m.predire((Xt - mu) / sd, t["centre"].values)
    if spec.ar1:
        rs = pd.DataFrame({"centre": a["centre"].values, "mois": a["mois"].values, "r": m.resid})
        rs = rs.sort_values(["centre", "mois"])
        rs["r1"] = rs.groupby("centre")["r"].shift(1)
        ok = rs.dropna()
        rho = np.corrcoef(ok["r"], ok["r1"])[0, 1] if len(ok) > 10 else 0.0
        der = rs.groupby("centre")["r"].last()
        pr = pr + rho * t["centre"].map(der).fillna(0).values
    if spec.log:
        corr = np.exp(0.5 * s ** 2)
        pr, fixe, s = np.expm1(pr) * corr, np.expm1(fixe) * corr, (np.expm1(pr) * corr) * np.sqrt(np.exp(s ** 2) - 1)
    pr, fixe, s = pr * fs_t, fixe * fs_t, s * fs_t
    if spec.etat:   # privés modélisés + État prévu à part
        pr, fixe = pr + t["etat_prev"].values, fixe + t["etat_prev"].values
    out = t[["centre", "groupe", "mois", "y", "enc_recent"]].copy()
    out["prevision"], out["potentiel"], out["ecart_type"] = np.clip(pr, 0, None), np.clip(fixe, 0, None), s
    return out, m, v


# =============================================================
# 3. VALIDATION CHRONOLOGIQUE
# =============================================================
def valider(D, L, origines):
    res = []
    for T in origines:
        app = ajouter_volume(L[L["mois"] < T], D, T)
        test = ajouter_volume(L[L["mois"] == T], D, T)
        idx_s = indices_saisonniers(D, T)
        b = test[["centre", "groupe", "mois", "y", "enc_recent"]].copy()
        b["methode"], b["prevision"], b["ecart_type"] = BASE, b["enc_recent"], np.nan
        res.append(b)
        b2 = b.copy()
        b2["methode"], b2["prevision"] = BASE_S, moy6_saison(D, test, T, idx_s)
        res.append(b2)
        for sp in SPECS:
            for g in GROUPES:
                o, _, _ = ajuster_predire(sp, app, test, g, idx_s)
                o["methode"] = sp.nom
                res.append(o)
    r = pd.concat(res, ignore_index=True)
    r = r[r["y"].notna()]
    # échelle MASE : erreur moyenne de la prévision naïve (mois précédent) du centre, avant la période testée
    E = D.enc.reindex(D.centres)
    pre = [m for m in D.mois_enc if m < origines[0]]
    echelle = E[pre].diff(axis=1).abs().mean(axis=1)
    r["erreur"] = r["prevision"] - r["y"]
    r["erreur_abs"] = r["erreur"].abs()
    r["mase"] = r["erreur_abs"] / r["centre"].map(echelle)
    r["dans_ic80"] = (r["y"] >= r["prevision"] - Z80 * r["ecart_type"]) & (r["y"] <= r["prevision"] + Z80 * r["ecart_type"])
    return r


def metriques(r, par=()):
    def f(g):
        return pd.Series({
            "Erreur absolue moyenne (MRU)": g["erreur_abs"].mean(),
            "Erreur quadratique (RMSE, MRU)": np.sqrt((g["erreur"] ** 2).mean()),
            "Erreur pondérée WAPE (%)": 100 * g["erreur_abs"].sum() / g["y"].sum(),
            "MASE": g["mase"].replace(np.inf, np.nan).mean(),
            "Biais (%)": 100 * g["erreur"].sum() / g["y"].sum(),
            "Couverture IC 80 % (%)": 100 * g["dans_ic80"].mean() if g["ecart_type"].notna().any() else np.nan,
            "Observations": len(g),
        })
    return r.groupby(["methode", *par], observed=True).apply(f).reset_index()


# =============================================================
# 4. OBJECTIFS SOUS PLAFONDS
# =============================================================
def repartir(base, plafond, cible):
    """Objectif = λ x base (même effort en % pour tous), plafonné ; l'excédent est redistribué aux
    centres qui ont encore une marge. Retourne objectifs et montant non réparti."""
    base, plafond = np.asarray(base, float), np.asarray(plafond, float)
    if cible is None:
        return np.minimum(base, plafond), np.nan, np.nan
    obj = base.copy()
    libre = np.ones(len(base), bool)
    for _ in range(200):
        fixe = obj[~libre].sum()
        lam = (cible - fixe) / base[libre].sum() if base[libre].sum() > 0 else 0
        obj[libre] = lam * base[libre]
        dep = libre & (obj > plafond)
        if not dep.any():
            break
        obj[dep] = plafond[dep]
        libre &= ~dep
        if not libre.any():
            break
    obj = np.minimum(obj, plafond)
    return obj, cible - obj.sum(), (lam if libre.any() else np.nan)


REGLES = {
    "aucun": "Aucun effort : objectif = prévision",
    "pourcentage": "Effort uniforme en % de la prévision (décision de la direction)",
    "reference_mn": "Rattrapage de la référence multiniveau (centre moyen de même profil)",
    "repere": "Effort commun pour atteindre le repère national (enveloppe)",
}


def objectifs(r, prog_max, regle, pct=0.0, repere=None):
    """Prévision -> effort selon une règle explicite -> plafonds appliqués EN DERNIER -> statut."""
    t = r.copy()
    t["plafond_pic"] = P("COEF_PLAFOND", 1.2) * t["pic_2026"]
    t["plafond_progression"] = (1 + prog_max) * t["moyenne_recente"] if prog_max is not None else np.nan
    t["plafond"] = t[["plafond_pic", "plafond_progression"]].min(axis=1)
    non_rep, lam = np.nan, np.nan
    if regle == "aucun":
        t["effort_propose"] = 0.0
    elif regle == "pourcentage":
        t["effort_propose"] = pct * t["prevision"]
    elif regle == "reference_mn":
        t["effort_propose"] = (t["potentiel"] - t["prevision"]).clip(lower=0)
    elif regle == "repere":
        obj, non_rep, lam = repartir(t["prevision"].values, t["plafond"].values, repere)
        t["effort_propose"] = (lam if np.isfinite(lam) else 1.0) * t["prevision"] - t["prevision"]
    else:
        raise ValueError(f"MN_REGLE_EFFORT inconnue : {regle}")
    t["objectif_avant_plafond"] = t["prevision"] + t["effort_propose"]
    if regle == "repere":
        t["objectif"] = obj
    else:
        t["objectif"] = np.minimum(t["objectif_avant_plafond"], t["plafond"])
    t["retire_par_plafond"] = np.minimum(t["objectif"] - t["objectif_avant_plafond"], 0)
    t["effort_retenu"] = t["objectif"] - t["prevision"]
    au_pic = t["objectif"] >= t["plafond_pic"] - 0.5
    au_prog = t["objectif"] >= t["plafond_progression"].fillna(np.inf) - 0.5
    t["statut"] = np.where(au_pic, "Plafonné (1,2 x Pic)",
                           np.where(au_prog, f"Plafonné (+{(prog_max or 0):.0%})".replace("%", " %"), "Libre"))
    t["prevision_au_dessus_plafond"] = np.where(t["prevision"] > t["plafond"] + 0.5, "oui", "")
    t["effort_pct_prevision"] = 100 * t["effort_retenu"] / t["prevision"].replace(0, np.nan)
    t["hausse_vs_moyenne_pct"] = 100 * (t["objectif"] / t["moyenne_recente"] - 1)
    return t, non_rep, lam


# =============================================================
# 5. CONTRÔLES
# =============================================================
def fraicheur(D):
    """Fraîcheur et disponibilité : chaque fichier doit couvrir jusqu'à MOIS_FIN."""
    from datetime import datetime
    F = P("FICHIERS")
    fin = P("MOIS_FIN")
    lignes = []
    for cle, dernier, obligatoire in (("encaissements", D.mois_enc[-1], True), ("facturation", D.mois_fac[-1], True),
                                      ("production", D.mois_vol[-1] if D.mois_vol else "", False)):
        ch = os.path.join(D_IN, F[cle])
        date_f = datetime.fromtimestamp(os.path.getmtime(ch)).strftime("%Y-%m-%d %H:%M") if os.path.exists(ch) else "absent"
        ok = bool(dernier) and dernier >= fin
        lignes.append({"Fichier": F[cle], "Dernier mois présent": dernier, "Mois attendu": fin,
                       "Date du fichier": date_f, "Obligatoire": "oui" if obligatoire else "non",
                       "État": "à jour" if ok else ("EN RETARD" if obligatoire else "en retard (toléré)")})
    return pd.DataFrame(lignes)


def detail_ecretage(D, T):
    """Moyenne brute / corrigée des 6 derniers mois et mois écrêtés (seuil = 2,5 x médiane avant T)."""
    E = D.enc.reindex(D.centres)
    avant = [m for m in D.mois_enc if m < T]
    der6 = avant[-6:]
    med = E[avant].median(axis=1)
    lignes, detail = [], []
    for c in D.centres:
        seuil = SEUIL_EXC * med[c]
        brut = E.loc[c, der6]
        corr = brut.clip(upper=seuil)
        mois_ec = [m for m in der6 if pd.notna(brut[m]) and brut[m] > seuil]
        for m in mois_ec:
            detail.append({"Centre": c, "Nom centre": D.nom.get(c, ""), "Mois": m, "Encaissement brut": brut[m],
                           "Seuil (2,5 x médiane)": seuil, "Valeur retenue": seuil, "Écart retiré": brut[m] - seuil})
        lignes.append({"Centre": c, "Nom centre": D.nom.get(c, ""), "Moyenne brute 6 mois": brut.mean(),
                       "Moyenne corrigée 6 mois": corr.mean(), "Écart": brut.mean() - corr.mean(),
                       "Médiane historique": med[c], "Seuil d'écrêtage": seuil,
                       "Mois écrêtés": ", ".join(mois_ec)})
    return pd.DataFrame(lignes), pd.DataFrame(detail)


def suivi_mensuel(D, T, prev_par_methode):
    """Enregistre les prévisions du mois et évalue celles dont le réel est maintenant connu."""
    from datetime import date
    ch = os.path.join(D_OUT, "suivi_previsions.csv")
    neuf = pd.DataFrame([{"date_calcul": date.today().isoformat(), "mois_prevu": T, "centre": c,
                          "methode": mth, "prevision": v}
                         for mth, s in prev_par_methode.items() for c, v in s.items()])
    if os.path.exists(ch):
        anc = pd.read_csv(ch, sep=";", dtype={"centre": str})
        neuf = pd.concat([anc[anc["mois_prevu"] != T], neuf], ignore_index=True)
    neuf.to_csv(ch, sep=";", index=False, encoding="utf-8-sig")
    reel = D.enc.stack().rename("reel").reset_index().rename(columns={"mois": "mois_prevu"})
    s = neuf.merge(reel, on=["centre", "mois_prevu"], how="inner")
    if s.empty:
        return pd.DataFrame({"Info": [f"Prévisions de {T} enregistrées dans {os.path.basename(ch)} ; "
                                      "elles seront comparées au réel au prochain calcul."]})
    s["err"] = (s["prevision"] - s["reel"]).abs()
    out = s.groupby(["mois_prevu", "methode"]).apply(
        lambda g: pd.Series({"WAPE (%)": 100 * g["err"].sum() / g["reel"].sum(), "Centres": len(g)})).reset_index()
    tot = s.groupby("methode").apply(lambda g: 100 * g["err"].sum() / g["reel"].sum()).rename("WAPE cumulé (%)")
    return out.merge(tot.reset_index(), on="methode").sort_values(["mois_prevu", "WAPE (%)"])


def scenarios_effort(R, retenue, nom_pot, r, prog, repere_ref):
    """Scénarios d'effort : % par rapport à la prévision et fréquence d'atteinte dans le passé
    (mois testés en validation, objectif avant plafond)."""
    v = R[R["methode"] == retenue][["centre", "groupe", "mois", "y", "prevision"]].copy()
    pot = R[R["methode"] == nom_pot][["centre", "mois", "potentiel"]]
    v = v.merge(pot, on=["centre", "mois"], how="left")
    defs = [("Aucun effort (objectif = prévision)", "aucun", 0.0)]
    defs += [(f"Effort uniforme +{100 * p:.0f} %", "pourcentage", p) for p in P("MN_SCENARIOS_EFFORT", [0.05, 0.10, 0.15, 0.20])]
    defs += [("Rattrapage de la référence multiniveau", "reference_mn", 0.0),
             (f"Repère national {repere_ref / 1e6:.0f} M par mois", "repere", 0.0)]
    lignes, par_centre = [], {}
    for nom, regle, p in defs:
        if regle == "aucun":
            oh = v["prevision"]
        elif regle == "pourcentage":
            oh = v["prevision"] * (1 + p)
        elif regle == "reference_mn":
            oh = np.maximum(v["prevision"], v["potentiel"].fillna(v["prevision"]))
        else:
            lam = repere_ref / v.groupby("mois")["prevision"].transform("sum")
            oh = v["prevision"] * lam
        att = v["y"] >= oh
        t, _, _ = objectifs(r, prog, regle, p, repere_ref)
        par_centre[nom] = t["objectif"]
        lignes.append({"Scénario": nom, "Règle (MN_REGLE_EFFORT)": regle,
                       "Total objectifs du mois": t["objectif"].sum(),
                       "Effort total": t["effort_retenu"].sum(),
                       "Effort / prévision (%)": 100 * t["effort_retenu"].sum() / t["prevision"].sum(),
                       "Atteinte passée, tous centres (%)": 100 * att.mean(),
                       **{f"Atteinte passée, {g} (%)": 100 * att[v["groupe"] == g].mean() for g in GROUPES},
                       "Centres plafonnés": int((t["statut"] != "Libre").sum())})
    return pd.DataFrame(lignes), pd.DataFrame(par_centre)


def couverture(R, retenue, bornes, reserves):
    v = R[R["methode"] == retenue].copy()
    v["bas"] = v["prevision"] * v["groupe"].map(bornes[0.10])
    v["haut"] = v["prevision"] * v["groupe"].map(bornes[0.90])
    v["dedans"] = (v["y"] >= v["bas"]) & (v["y"] <= v["haut"])
    v["sous"] = v["y"] < v["bas"]
    v["periode"] = np.where(v["mois"].isin(reserves), "contrôle final", "sélection")
    out = v.groupby(["periode", "groupe"]).agg(**{
        "Couverture de l'intervalle 80 % (%)": ("dedans", lambda x: 100 * x.mean()),
        "Réalisations sous la borne basse (%)": ("sous", lambda x: 100 * x.mean()),
        "Observations": ("dedans", "size")}).reset_index()
    tot = v.groupby("periode").agg(**{"Couverture de l'intervalle 80 % (%)": ("dedans", lambda x: 100 * x.mean()),
                                      "Réalisations sous la borne basse (%)": ("sous", lambda x: 100 * x.mean()),
                                      "Observations": ("dedans", "size")}).reset_index()
    tot["groupe"] = "Tous"
    return pd.concat([out, tot], ignore_index=True)


# =============================================================
# 5. PROGRAMME
# =============================================================
def main(sauvegarde=False):
    print("=" * 70)
    print("  MODÈLE MULTINIVEAU AMÉLIORÉ — objectifs d'encaissement par centre")
    print("=" * 70)
    D = Donnees()
    T = decaler(P("MOIS_FIN"), 1)
    FR = fraicheur(D)
    print("\n[0b] Fraîcheur des données")
    for _, l in FR.iterrows():
        print(f"      {l['Fichier']:<20} dernier mois {l['Dernier mois présent'] or '—':<8} attendu {l['Mois attendu']} "
              f"| fichier du {l['Date du fichier']} | {l['État']}")
    if (FR["État"] == "EN RETARD").any():
        if not sauvegarde:
            raise SystemExit("  ❌ Données obligatoires en retard : calcul arrêté. Relancez l'extraction, ou "
                             "utilisez volontairement le jeu sauvegardé avec --jeu-sauvegarde.")
        note("ATTENTION : calcul fait sur un jeu sauvegardé dont des données obligatoires sont en retard.")
    print(f"\n[1] Données : encaissements {D.mois_enc[0]} → {D.mois_enc[-1]} | facturation {D.mois_fac[0]} → "
          f"{D.mois_fac[-1]} | production {D.mois_vol[0] if D.mois_vol else '—'} → {D.mois_vol[-1] if D.mois_vol else '—'}")
    Q = D.qualite()
    for (s, c), n in Q.groupby(["Source", "Contrôle"]).size().items():
        print(f"      {s:<14} {c:<32} {n:>4}")
    premier = decaler(D.mois_fac[0], 3)                       # 3 mois de facturation antérieurs disponibles
    mois_lignes = [m for m in D.mois_enc if m >= premier] + [T]
    L = construire_lignes(D, mois_lignes)
    nb_val = P("MN_MOIS_VALIDATION", 8)
    origines = [m for m in D.mois_enc if m >= decaler(premier, 4)][-nb_val:]
    reserves = origines[-P("MN_MOIS_RESERVES", 2):]
    selection = [m for m in origines if m not in reserves]
    note(f"Lignes centre x mois : {premier} → {T}. Validation : sélection {selection[0]} → {selection[-1]}, "
         f"contrôle final réservé {reserves[0]} → {reserves[-1]}.")
    note("Abonnés : nombre d'abonnés FACTURÉS (daté) ; hors GC facturés sur 2 mois (facturation bimestrielle), "
         "GC chaque mois. Volume : moyenne des mois publiés avant le mois calculé.")

    print("\n[2] Validation chronologique (origine glissante)")
    R = valider(D, L, origines)
    R["periode"] = np.where(R["mois"].isin(reserves), "contrôle final", "sélection")
    taille = D.enc.reindex(D.centres)[[m for m in D.mois_enc if m < origines[0]][-12:]].mean(axis=1)
    R["taille"] = R["centre"].map(pd.qcut(taille, 3, labels=["petits", "moyens", "grands"]))
    m_sel = metriques(R[R["periode"] == "sélection"])
    m_fin = metriques(R[R["periode"] == "contrôle final"])
    m_tout = metriques(R)
    m_grp = metriques(R, ("groupe",))
    m_tai = metriques(R, ("taille",))
    choix = P("MN_SPECIFICATION", "auto")
    cands = m_sel.sort_values("Erreur absolue moyenne (MRU)")
    retenue = cands.iloc[0]["methode"] if choix == "auto" else choix
    # Principe de parcimonie : une méthode plus complexe n'est retenue que si elle bat la moyenne 6 mois
    # d'au moins MN_MARGE_SELECTION points d'erreur pondérée (WAPE) sur la période de sélection.
    marge = P("MN_MARGE_SELECTION", 1.0)
    wape = m_sel.set_index("methode")["Erreur pondérée WAPE (%)"]
    if choix == "auto" and retenue != BASE and wape[retenue] > wape[BASE] - marge:
        note(f"{retenue} ({wape[retenue]:.1f} %) ne bat pas la moyenne 6 mois ({wape[BASE]:.1f} %) d'au moins "
             f"{marge:g} point : la méthode la plus simple est conservée.")
        retenue = BASE
    crit_seuls = m_sel[m_sel["methode"].isin([s.nom for s in SPECS if not s.recent])].sort_values(
        "Erreur absolue moyenne (MRU)")
    nom_pot = crit_seuls.iloc[0]["methode"]
    spec_pot = next(s for s in SPECS if s.nom == nom_pot)
    spec_niv = next(s for s in SPECS if s.recent and not s.log and not s.ar1 and not s.saison and s.abonnes)
    spec_log = next(s for s in SPECS if s.log)
    w = lambda tab, mm, col: tab.loc[tab["methode"] == mm, col].iloc[0]
    for _, l in m_sel.sort_values("Erreur absolue moyenne (MRU)").iterrows():
        print(f"    {l['methode']:<62} WAPE sélection {l['Erreur pondérée WAPE (%)']:5.1f} % | "
              f"contrôle final {w(m_fin, l['methode'], 'Erreur pondérée WAPE (%)'):5.1f} %")
    note(f"Prévision retenue (plus faible erreur absolue moyenne sur la période de sélection) : {retenue}")
    note(f"Potentiel (centre moyen de même profil) : {nom_pot} — partie fixe du modèle, sans l'effet du centre")
    if retenue in (BASE, BASE_S):
        note("Aucune variante du modèle multiniveau ne prévoit mieux que la moyenne des 6 derniers mois : "
             "la prévision retenue est cette moyenne ; le modèle multiniveau sert au potentiel et aux poids.")
    if indices_saisonniers(D, origines[0]) is None:
        note("Saisonnalité : moins de 2 années d'historique avant les premiers mois testés ; la variante "
             "« + saisonnalité » n'a pu être appliquée que lorsque l'indice était calculable.")
    # Erreurs par centre : modèle retenu vs moyenne 6 mois
    meilleur_mn = cands[~cands["methode"].isin([BASE, BASE_S])].iloc[0]["methode"]
    pc = R[R["methode"].isin([meilleur_mn, BASE])].pivot_table(index="centre", columns="methode",
                                                                values="erreur_abs", aggfunc="mean")
    pc = pc.rename(columns={meilleur_mn: "err_modele", BASE: "err_moy6"})[["err_modele", "err_moy6"]]
    pc["nom_centre"] = pc.index.map(D.nom)
    pc["groupe"] = pc.index.map(D.groupe)
    pc["meilleur"] = np.where(pc["err_modele"] < pc["err_moy6"], "modèle", "moyenne 6 mois")
    # Calibration de l'incertitude : facteur pour que l'IC 80 % couvre 80 % sur la période de sélection
    rs = R[(R["methode"] == retenue) & (R["periode"] == "sélection")].copy()
    rs["ratio"] = rs["y"] / rs["prevision"].replace(0, np.nan)
    bornes = rs.groupby("groupe")["ratio"].quantile([0.10, 0.90]).unstack()
    rf = R[(R["methode"] == retenue) & (R["periode"] == "contrôle final")].copy()
    lo, hi = rf["groupe"].map(bornes[0.10]), rf["groupe"].map(bornes[0.90])
    cov_fin = 100 * np.mean((rf["y"] >= lo * rf["prevision"]) & (rf["y"] <= hi * rf["prevision"]))
    k_ic = np.nan
    ra = R[R["methode"] == retenue].copy()
    ra["ratio"] = ra["y"] / ra["prevision"].replace(0, np.nan)
    bornes_sel = bornes
    bornes = ra.groupby("groupe")["ratio"].quantile([0.10, 0.90]).unstack()
    note("Intervalle des objectifs : bornes recalculées sur tous les mois testés (sélection + contrôle final), "
         "pour intégrer les mois récents.")
    note("Incertitude : intervalle à 80 % empirique (10e et 90e centiles du rapport réel / prévision observés "
         f"en sélection, par groupe) ; couverture sur le contrôle final : {cov_fin:.0f} %")

    print(f"\n[3] Estimation finale et diagnostics des résidus ({T})")
    app = ajouter_volume(L[L["mois"] < T], D, T)
    test = ajouter_volume(L[L["mois"] == T], D, T)
    idx_s = indices_saisonniers(D, T)
    outs_pot, coefs, diag = [], [], []
    tailles = pd.qcut(taille, 3, labels=["petits", "moyens", "grands"])
    for g in GROUPES:
        o, m, v = ajuster_predire(spec_pot, app, test, g, idx_s)
        outs_pot.append(o)
        for j, k in enumerate(v):
            coefs.append({"Groupe": g, "Modèle": spec_pot.nom, "Variable": k,
                          "Coefficient (variable standardisée)": m.beta[j + 1]})
        coefs += [{"Groupe": g, "Modèle": spec_pot.nom, "Variable": "σ² effet centre",
                   "Coefficient (variable standardisée)": m.s2u},
                  {"Groupe": g, "Modèle": spec_pot.nom, "Variable": "σ² résiduelle",
                   "Coefficient (variable standardisée)": m.s2e}]
        for sp in (spec_niv, spec_log):
            _, md, vd = ajuster_predire(sp, app, test, g, idx_s)
            a = app[app["groupe"] == g].dropna(subset=vd + ["y"])
            rr = pd.DataFrame({"centre": a["centre"].values, "mois": a["mois"].values, "r": md.resid})
            rr["taille"] = rr["centre"].map(tailles)
            sdt = rr.groupby("taille", observed=True)["r"].std()
            rr = rr.sort_values(["centre", "mois"])
            acf = {}
            for h in (1, 2, 3):
                rr[f"l{h}"] = rr.groupby("centre")["r"].shift(h)
                ok = rr.dropna(subset=[f"l{h}"])
                acf[h] = (np.corrcoef(ok["r"], ok[f"l{h}"])[0, 1], 1.96 / np.sqrt(len(ok)))
            diag.append({"Groupe": g, "Modèle": sp.nom, "Échelle": "logarithme" if sp.log else "MRU",
                         "Observations": len(rr),
                         "Écart-type résiduel petits centres": sdt.get("petits", np.nan),
                         "Écart-type résiduel centres moyens": sdt.get("moyens", np.nan),
                         "Écart-type résiduel grands centres": sdt.get("grands", np.nan),
                         "Rapport grands / petits": sdt.get("grands", np.nan) / sdt.get("petits", np.nan),
                         **{f"Autocorrélation retard {h}": acf[h][0] for h in acf},
                         "Seuil de significativité (±)": acf[1][1],
                         "Part de variance due au centre (ICC)": md.s2u / (md.s2u + md.s2e)})
            print(f"    {g:<24} {'log' if sp.log else 'MRU':<4} dispersion grands/petits "
                  f"{sdt.get('grands', np.nan) / sdt.get('petits', np.nan):5.1f} | autocorrélation retard 1 "
                  f"{acf[1][0]:+.2f} (seuil ±{acf[1][1]:.2f})")
    diag = pd.DataFrame(diag)
    POT = pd.concat(outs_pot, ignore_index=True).set_index("centre")
    if retenue in (BASE, BASE_S):
        F = test.set_index("centre")[["enc_recent", "groupe"]].copy()
        F["prevision"] = F["enc_recent"] if retenue == BASE else moy6_saison(D, test, T, idx_s)
    else:
        spec = next(s for s in SPECS if s.nom == retenue)
        F = pd.concat([ajuster_predire(spec, app, test, g, idx_s)[0] for g in GROUPES]).set_index("centre")
    E = D.enc.reindex(D.centres)
    der6 = [m for m in D.mois_enc if m < T][-6:]
    m26 = [m for m in D.mois_enc if m.startswith(str(P("ANNEE_PIC", 2026))) and m < T]
    r = pd.DataFrame(index=D.centres)
    r["nom_centre"], r["groupe"] = D.nom, D.groupe
    r["moyenne_recente_brute"] = E[der6].mean(axis=1)
    r["moyenne_recente"] = F["enc_recent"]
    r["prevision"] = F["prevision"]
    r["ic80_bas"] = F["prevision"] * F["groupe"].map(bornes[0.10])
    r["ic80_haut"] = F["prevision"] * F["groupe"].map(bornes[0.90])
    r["potentiel"] = POT["potentiel"]
    r["pic_2026"] = E[m26].max(axis=1)
    r["mois_pic"] = E[m26].idxmax(axis=1)
    prog = P("MN_PROGRESSION_MAX", None)
    regle = P("MN_REGLE_EFFORT", "aucun")
    pct = P("MN_EFFORT_PCT", 0.0) or 0.0
    repere = P("MN_REPERE_MENSUEL", None)
    if regle == "repere" and repere is None:
        raise SystemExit("MN_REGLE_EFFORT = 'repere' demande un montant dans MN_REPERE_MENSUEL")
    res, non_rep, lam = objectifs(r, prog, regle, pct, repere)
    repere_ref = repere or 150_000_000
    scen, scen_c = scenarios_effort(R, retenue, nom_pot, r, prog, repere_ref)
    plaf = []
    for nom_v, pm in (("avec le plafond +50 %", 0.5), ("sans le plafond +50 %", None)):
        t, nr, _ = objectifs(r, pm, regle, pct, repere)
        plaf.append({"Variante": nom_v, "Total objectifs": t["objectif"].sum(), "Non réparti": nr,
                     "Centres libres": int((t["statut"] == "Libre").sum()),
                     "Centres au plafond 1,2 x Pic": int(t["statut"].str.contains("Pic").sum()),
                     "Centres au plafond +50 %": int(t["statut"].str.contains("50").sum())})
    plaf = pd.DataFrame(plaf)
    moy_ec, det_ec = detail_ecretage(D, T)
    cov = couverture(R, retenue, bornes_sel, reserves)

    # Suivi mensuel : prévisions de toutes les méthodes pour T, comparées au réel aux calculs suivants
    prev_m = {BASE: test.set_index("centre")["enc_recent"],
              BASE_S: pd.Series(moy6_saison(D, test, T, idx_s), index=test["centre"].values)}
    for sp in SPECS:
        prev_m[sp.nom] = pd.concat([ajuster_predire(sp, app, test, g, idx_s)[0] for g in GROUPES]).set_index(
            "centre")["prevision"]
    suivi = suivi_mensuel(D, T, prev_m)

    print(f"\n[4] Objectifs de {T} — règle d'effort : {REGLES[regle]}"
          + (f" ({100 * pct:.0f} %)" if regle == "pourcentage" else ""))
    print(f"    Prévision {res['prevision'].sum():,.0f} | effort {res['effort_retenu'].sum():,.0f} | "
          f"objectifs {res['objectif'].sum():,.0f}" + (f" | non réparti {non_rep:,.0f}" if regle == "repere" else ""))
    print("    Scénarios d'effort (fréquence d'atteinte dans le passé, avant plafond) :")
    for _, l in scen.iterrows():
        print(f"      {l['Scénario']:<42} total {l['Total objectifs du mois']:>13,.0f} | effort "
              f"{l['Effort / prévision (%)']:5.1f} % | atteint {l['Atteinte passée, tous centres (%)']:4.0f} % des cas")
    print("    Couverture de l'intervalle 80 % :")
    for _, l in cov.iterrows():
        print(f"      {l['periode']:<15} {l['groupe']:<25} {l[COUV]:5.1f} %")

    # ---- Avant / après (modèle multiniveau actuel)
    av = None
    chemin_av = os.path.join(D_OUT, f"objectifs_{T}.xlsx")
    if os.path.exists(chemin_av):
        try:
            a0 = pd.read_excel(chemin_av, sheet_name="Objectifs")
            col = next(c for c in a0.columns if str(c).startswith("OBJECTIF"))
            a0["Centre"] = a0["Centre"].astype(str)
            av = a0[a0["Centre"] != "TOTAL"].set_index("Centre")[col].astype(float)
        except Exception as ex:
            note(f"Objectifs du modèle actuel illisibles ({ex}) : comparaison avant/après impossible")

    # ---- Export
    os.makedirs(D_OUT, exist_ok=True)
    chemin = os.path.join(D_OUT, f"multiniveau_{T}.xlsx")
    cols = [("nom_centre", "Nom centre"), ("groupe", "Groupe"),
            ("moyenne_recente_brute", "Moyenne 6 mois brute"),
            ("moyenne_recente", "Moyenne 6 mois corrigée (mois exceptionnels écrêtés)"),
            ("prevision", f"1. PRÉVISION {T}"), ("ic80_bas", "   intervalle 80 % : bas"),
            ("ic80_haut", "   intervalle 80 % : haut"),
            ("potentiel", "2. RÉFÉRENCE MULTINIVEAU (centre moyen de même profil, information)"),
            ("effort_propose", f"3. EFFORT PROPOSÉ (règle : {regle})"),
            ("objectif_avant_plafond", "4. Objectif avant plafond (1 + 3)"),
            ("pic_2026", "Pic 2026 réel"), ("mois_pic", "Mois du pic"), ("plafond_pic", "Plafond 1,2 x Pic"),
            ("plafond_progression", "Plafond +50 % (option)"), ("plafond", "Plafond retenu"),
            ("retire_par_plafond", "5. Retiré par le plafond"),
            ("objectif", f"6. OBJECTIF FINAL {T} (4 + 5)"), ("statut", "Statut (sur l'objectif final)"),
            ("effort_retenu", "Effort retenu (6 - 1)"), ("effort_pct_prevision", "Effort / prévision (%)"),
            ("prevision_au_dessus_plafond", "Prévision au-dessus du plafond"),
            ("hausse_vs_moyenne_pct", "Objectif / moyenne corrigée - 1 (%)")]
    obj = res[[c for c, _ in cols]].copy()
    obj.columns = [l for _, l in cols]
    obj = obj.sort_values(f"6. OBJECTIF FINAL {T} (4 + 5)", ascending=False)
    obj.index.name = "Centre"
    sommes = [l for c, l in cols if c in ("moyenne_recente_brute", "moyenne_recente", "prevision", "potentiel",
                                          "effort_propose", "objectif_avant_plafond", "pic_2026", "plafond_pic",
                                          "plafond", "retire_par_plafond", "objectif", "effort_retenu")]
    obj.loc["TOTAL", sommes] = obj[sommes].sum()
    scen_c.insert(0, "Nom centre", D.nom)
    scen_c.insert(1, "Prévision", res["prevision"])
    aa = pd.DataFrame({"Nom centre": D.nom, "Groupe": D.groupe,
                       "Objectif modèle actuel (avant)": av.reindex(D.centres) if av is not None else np.nan,
                       "Objectif modèle amélioré (après)": res["objectif"], "Prévision": res["prevision"],
                       "Référence multiniveau": res["potentiel"], "Moyenne 6 mois brute": res["moyenne_recente_brute"]})
    aa["Écart après - avant (%)"] = 100 * (aa["Objectif modèle amélioré (après)"] / aa["Objectif modèle actuel (avant)"] - 1)
    aa = aa.sort_values("Objectif modèle amélioré (après)", ascending=False)
    cs = ["Objectif modèle actuel (avant)", "Objectif modèle amélioré (après)", "Prévision", "Référence multiniveau",
          "Moyenne 6 mois brute"]
    aa.loc["TOTAL", cs] = aa[cs].sum()

    ameliorations = [
        f"Validation chronologique : meilleure variante multiniveau {w(m_sel, meilleur_mn, 'Erreur pondérée WAPE (%)'):.1f} % "
        f"d'erreur pondérée en sélection contre {w(m_sel, BASE, 'Erreur pondérée WAPE (%)'):.1f} % pour la moyenne des 6 "
        f"derniers mois ; contrôle final {w(m_fin, meilleur_mn, 'Erreur pondérée WAPE (%)'):.1f} % contre "
        f"{w(m_fin, BASE, 'Erreur pondérée WAPE (%)'):.1f} %. Prévision retenue : {retenue}.",
        "Objectif = prévision + effort issu d'une règle explicite (MN_REGLE_EFFORT) ; aucune majoration automatique.",
        "Le plafond 1,2 x Pic réel 2026 est appliqué à la dernière étape ; les statuts portent sur l'objectif final.",
        "Chaque scénario d'effort est présenté avec sa fréquence d'atteinte dans le passé (onglet « Scénarios d'effort »).",
        "Les prévisions de chaque mois sont enregistrées (suivi_previsions.csv) et comparées au réel aux calculs suivants.",
    ]
    limites = [
        f"Validation sur {len(origines)} mois seulement ({origines[0]} → {origines[-1]}) : la facturation commence en "
        f"{D.mois_fac[0]}.",
        "La référence multiniveau n'a pas démontré que l'écart avec la prévision est récupérable : elle reste une "
        "information, utilisée seulement si la règle « reference_mn » est choisie.",
        f"Intervalle annoncé à 80 % : couverture réelle {cov_fin:.0f} % sur le contrôle final (voir « Couverture »).",
        "Effectifs : abonnés FACTURÉS (proxy daté) ; l'historique des abonnés actifs n'existe pas.",
        "Fréquences d'atteinte calculées avant plafond, sur les mois de validation.",
    ]
    lisez = pd.DataFrame({"Rubrique": ["Règle d'effort appliquée"] + ["Amélioration"] * len(ameliorations)
                          + ["Limite"] * len(limites) + ["Note"] * len(NOTES),
                          "Détail": [REGLES[regle] + (f" ({100 * pct:.0f} %)" if regle == "pourcentage" else "")]
                          + ameliorations + limites + NOTES})
    with pd.ExcelWriter(chemin, engine="openpyxl") as xw:
        lisez.to_excel(xw, sheet_name="Lisez-moi", index=False)
        obj.to_excel(xw, sheet_name="Objectifs")
        scen.to_excel(xw, sheet_name="Scénarios d'effort", index=False)
        scen_c.to_excel(xw, sheet_name="Scénarios par centre", index_label="Centre")
        aa.to_excel(xw, sheet_name="Avant-après", index_label="Centre")
        plaf.to_excel(xw, sheet_name="Plafond +50 %", index=False)
        moy_ec.to_excel(xw, sheet_name="Moyennes brute-corrigée", index=False)
        det_ec.to_excel(xw, sheet_name="Mois écrêtés", index=False)
        FR.to_excel(xw, sheet_name="Fraîcheur des données", index=False)
        cov.to_excel(xw, sheet_name="Couverture intervalles", index=False)
        suivi.to_excel(xw, sheet_name="Suivi mensuel", index=False)
        m_sel.to_excel(xw, sheet_name="Validation sélection", index=False)
        m_fin.to_excel(xw, sheet_name="Validation contrôle final", index=False)
        m_tout.to_excel(xw, sheet_name="Validation tous mois", index=False)
        m_grp.to_excel(xw, sheet_name="Validation par groupe", index=False)
        m_tai.to_excel(xw, sheet_name="Validation par taille", index=False)
        pc.to_excel(xw, sheet_name="Erreurs par centre", index_label="Centre")
        diag.to_excel(xw, sheet_name="Diagnostics résidus", index=False)
        pd.DataFrame(coefs).to_excel(xw, sheet_name="Coefficients", index=False)
        Q.to_excel(xw, sheet_name="Qualité des données", index=False)
        R.to_excel(xw, sheet_name="Prévisions testées", index=False)
    print(f"\n  ✅ {chemin}")
    if P("MN_ECRAN_APP", False):
        csv = os.path.join(D_OUT, f"objectifs_{T}.csv")
        pd.DataFrame({"centre": res.index, "nom_centre": res["nom_centre"], "zone": res["groupe"],
                      "objectif": res["objectif"].round(0), "objectif_etat": 0,
                      "objectif_total": res["objectif"].round(0)}).to_csv(csv, sep=";", index=False, encoding="utf-8-sig")
        print(f"  ✅ {csv}  (écran Objectifs)")

    print(f"\n  OBJECTIFS DE {T} — prévision : {retenue} | effort : {regle}")
    print(f"  {'Centre':<31}{'Prévision':>13}{'Réf. MN':>13}{'Effort':>12}{'Plafond':>13}{'Objectif':>13}  Statut")
    for c, l in res.sort_values("objectif", ascending=False).iterrows():
        print(f"  {c:<6}{str(l['nom_centre'])[:24]:<25}{l['prevision']:>13,.0f}{l['potentiel']:>13,.0f}"
              f"{l['effort_retenu']:>12,.0f}{l['plafond']:>13,.0f}{l['objectif']:>13,.0f}  {l['statut']}")
    print(f"  {'TOTAL':<31}{res['prevision'].sum():>13,.0f}{res['potentiel'].sum():>13,.0f}"
          f"{res['effort_retenu'].sum():>12,.0f}{res['plafond'].sum():>13,.0f}{res['objectif'].sum():>13,.0f}")
    return dict(R=R, res=res, scen=scen, cov=cov, plaf=plaf, aa=aa, moy_ec=moy_ec, FR=FR, suivi=suivi)


def lancer():
    """Extraction obligatoire (sauf --jeu-sauvegarde), contrôle de fraîcheur, puis calcul."""
    sauvegarde = "--jeu-sauvegarde" in sys.argv
    if P("MN_EXTRACTION_OBLIGATOIRE", True) and not sauvegarde:
        print("[0] Extraction des données (obligatoire)")
        try:
            import extraction
            extraction.main()
        except BaseException as ex:   # noqa: BLE001 — y compris sys.exit dans l'extraction
            print(f"\n  ❌ EXTRACTION ÉCHOUÉE : {ex!r}")
            print("  Calcul ARRÊTÉ. Pour utiliser volontairement le dernier jeu sauvegardé :")
            print("      python modele_multiniveau.py --jeu-sauvegarde")
            sys.exit(1)
    elif sauvegarde:
        print("[0] Jeu de données SAUVEGARDÉ utilisé (choix explicite --jeu-sauvegarde), pas d'extraction")
    main(sauvegarde)


if __name__ == "__main__":
    lancer()
