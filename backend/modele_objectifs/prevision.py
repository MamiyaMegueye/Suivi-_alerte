"""
PRÉVISION DES ENCAISSEMENTS PAR CENTRE ET COMPARAISON DES MÉTHODES D'ALLOCATION
===============================================================================
Toutes les fonctions de ce module n'utilisent, pour une date simulée t, que les
données des mois antérieurs à t (encaissements, facturation, corrections).

Méthodes de prévision comparées (validation chronologique, origine glissante) :
  dernier_mois      encaissement du mois précédent
  moy3 / moy6       moyenne des 3 / 6 derniers mois (mois exceptionnels écrêtés)
  moy_hist          moyenne de tout l'historique disponible
  recouvrement_ca   taux de recouvrement du centre (6 derniers mois) x CA facturé des 3 mois précédents
  regression        encaissement attendu selon les critères (régression par groupe) — utilise
                    les effectifs d'abonnés actuels (seul instantané disponible) : information future
"""
import numpy as np
import pandas as pd


def decaler(mois, n):
    a, m = map(int, mois.split("-"))
    k = a * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def plage(debut, fin):
    out, m = [], debut
    while m <= fin:
        out.append(m)
        m = decaler(m, 1)
    return out


METHODES = ["dernier_mois", "moy3", "moy6", "moy_hist", "recouvrement_ca", "regression"]
METHODES_SANS_FUITE = ["dernier_mois", "moy3", "moy6", "moy_hist", "recouvrement_ca"]
LIBELLES = {
    "dernier_mois": "Encaissement du mois précédent",
    "moy3": "Moyenne des 3 derniers mois",
    "moy6": "Moyenne des 6 derniers mois",
    "moy_hist": "Moyenne de tout l'historique",
    "recouvrement_ca": "Taux de recouvrement x CA des 3 mois précédents",
    "regression": "Régression sur les critères (abonnés actuels : information future)",
}


class Donnees:
    """Séries brutes centre x mois (aucune correction : elles sont faites à chaque date simulée)."""

    def __init__(self, enc, ca_dom, ca_gc, abonnes, volume, zones, seuil_exc=2.5, seuil_ca=3.0, seuil_incomplet=0.5):
        self.enc = enc            # DataFrame centre x mois (encaissement brut)
        self.ca_dom = ca_dom      # CA hors GC brut
        self.ca_gc = ca_gc        # CA GC brut
        self.abonnes = abonnes    # DataFrame index centre : nb_abonnes, nb_gc (instantané actuel)
        self.volume = volume      # Series centre -> volume moyen (intérieur, NaN si manquant)
        self.zones = zones        # Series centre -> groupe
        self.seuil_exc, self.seuil_ca, self.seuil_incomplet = seuil_exc, seuil_ca, seuil_incomplet

    # ---- corrections avec les seules données antérieures à t
    def enc_passe(self, t):
        e = self.enc[[m for m in self.enc.columns if m < t]]
        if self.seuil_exc and e.shape[1]:
            lim = e.median(axis=1) * self.seuil_exc
            e = e.clip(upper=lim.where(lim > 0), axis=0)
        return e

    def ca_passe(self, t, quoi="total"):
        cols = [m for m in self.ca_dom.columns if m < t]
        d, g = self.ca_dom[cols].copy(), self.ca_gc[cols].copy()
        tot = (d.fillna(0) + g.fillna(0)).sum()
        if self.seuil_incomplet and len(tot):
            mauvais = tot.index[tot < self.seuil_incomplet * tot.median()]
            d[mauvais] = np.nan
            g[mauvais] = np.nan
        for x in (d, g):
            x[x < 0] = np.nan
            if self.seuil_ca:
                med = x.where(x > 0).median(axis=1)
                lim = med * self.seuil_ca
                x[:] = x.clip(upper=lim.where(lim > 0), axis=0)
        if quoi == "dom":
            return d
        if quoi == "gc":
            return g
        return d.add(g, fill_value=0).where(d.notna() | g.notna())

    def ca_lag(self, t, n=3, quoi="total"):
        """CA moyen des n mois précédant t (corrections calculées avec les données < t)."""
        ca = self.ca_passe(t, quoi)
        cols = [decaler(t, -k) for k in range(1, n + 1) if decaler(t, -k) in ca.columns]
        return ca[cols].mean(axis=1) if cols else pd.Series(np.nan, index=ca.index)


def prevoir(D, t, methode, centres, criteres=None):
    e = D.enc_passe(t).reindex(centres)
    hist = sorted(e.columns)
    if methode == "dernier_mois":
        out = e[hist[-1]]
    elif methode == "moy3":
        out = e[hist[-3:]].mean(axis=1)
    elif methode == "moy6":
        out = e[hist[-6:]].mean(axis=1)
    elif methode == "moy_hist":
        out = e.mean(axis=1)
    elif methode == "recouvrement_ca":
        mois6 = hist[-6:]
        lags = pd.concat({m: D.ca_lag(m) for m in mois6}, axis=1).reindex(centres)
        taux = e[mois6].sum(axis=1, min_count=1) / lags.sum(axis=1, min_count=1).replace(0, np.nan)
        out = taux * D.ca_lag(t).reindex(centres)
        out = out.fillna(e[hist[-6:]].mean(axis=1))
    elif methode == "regression":
        out = prevoir_regression(D, t, centres, criteres or {})
    else:
        raise ValueError(methode)
    return out.clip(lower=0).fillna(0.0)


def tableau_criteres(D, t, centres):
    c = pd.DataFrame(index=centres)
    c["encaissement"] = D.enc_passe(t).reindex(centres)[sorted(D.enc_passe(t).columns)[-6:]].mean(axis=1)
    c["ca_dom"] = D.ca_lag(t, 6, "dom").reindex(centres)
    c["ca_gc"] = D.ca_lag(t, 6, "gc").reindex(centres)
    c["ca_total"] = c["ca_dom"].fillna(0) + c["ca_gc"].fillna(0)
    c["nb_abonnes"] = D.abonnes["nb_abonnes"].reindex(centres)
    c["nb_gc"] = D.abonnes["nb_gc"].reindex(centres)
    c["nb_ab_hors_gc"] = (c["nb_abonnes"] - c["nb_gc"]).clip(lower=0)
    c["volume_int"] = D.volume.reindex(centres)
    c["groupe"] = D.zones.reindex(centres)
    return c.fillna({k: 0.0 for k in ["ca_dom", "ca_gc", "ca_total", "nb_abonnes", "nb_gc", "nb_ab_hors_gc"]})


def prevoir_regression(D, t, centres, criteres):
    c = tableau_criteres(D, t, centres)
    out = pd.Series(np.nan, index=centres)
    for g, crit in criteres.items():
        d = c[c["groupe"] == g].copy()
        crit = [k for k in crit if k in d.columns and d[k].fillna(0).sum() > 0]
        d[crit] = d[crit].fillna(d[crit].median())
        if len(d) <= len(crit) + 1:
            continue
        A = np.column_stack([np.ones(len(d)), d[crit].values])
        b = np.linalg.lstsq(A, d["encaissement"].fillna(0).values, rcond=None)[0]
        out[d.index] = A @ b
    return out


# -------------------------------------------------------------
# Plafonds et répartition sous contraintes (même règle que le modèle)
# -------------------------------------------------------------
def repartir_sous_plafond(x, plafond, cible):
    """Objectifs proportionnels à x, plafonnés ; l'excédent va aux centres qui ont une marge."""
    x, plafond = np.asarray(x, float), np.asarray(plafond, float)
    if x.sum() <= 0:
        x = np.ones_like(x)
    obj = np.minimum(x * cible / x.sum(), plafond)
    for _ in range(500):
        reste = cible - obj.sum()
        libres = obj < plafond - 0.5
        if reste < 0.5 or not libres.any():
            break
        w = np.where(libres, x, 0.0)
        if w.sum() <= 0:
            w = libres.astype(float)
        obj = np.minimum(obj + reste * w / w.sum(), plafond)
    return obj


def plafonds_a_date(D, t, coef_pic, annee_pic, prog_max):
    e_brut = D.enc[[m for m in D.enc.columns if m < t]]
    cols_annee = [m for m in e_brut.columns if m.startswith(str(annee_pic))]
    pic = e_brut[cols_annee].max(axis=1) if cols_annee else e_brut.max(axis=1)
    plaf = coef_pic * pic
    if prog_max is not None:
        moy = D.enc_passe(t).mean(axis=1)
        plaf = np.minimum(plaf, (1 + prog_max) * moy)
    return plaf


def parts_effort(regle, prev, scores, stock, groupes, w_retard):
    """Part de l'effort national attribuée à chaque centre (somme = 1)."""
    prev = prev.clip(lower=0)
    if regle == "prevision":
        return prev / prev.sum()
    part_g = prev.groupby(groupes).transform("sum") / prev.sum()
    sc = scores.reindex(prev.index).fillna(0)
    sc = sc / sc.groupby(groupes).transform("sum").replace(0, np.nan)
    if regle == "shapley":
        s = sc
    elif regle == "shapley_retard":
        st = stock.reindex(prev.index).fillna(0).clip(lower=0)
        st = st / st.groupby(groupes).transform("sum").replace(0, np.nan)
        s = (1 - w_retard) * sc + w_retard * st.fillna(0)
    else:
        raise ValueError(regle)
    out = (part_g * s.fillna(0))
    return out / out.sum()


def objectifs_regle(prev, effort, parts, plafond, cible):
    """Objectif = prévision + part de l'effort, puis plafonds et redistribution dans les marges."""
    x = (prev + effort * parts).clip(lower=0)
    return pd.Series(repartir_sous_plafond(x.values, plafond.reindex(prev.index).values, cible), index=prev.index)


# -------------------------------------------------------------
# Validation chronologique
# -------------------------------------------------------------
def valider_previsions(D, origines, centres, criteres):
    lignes, detail = [], []
    for t in origines:
        reel = D.enc[t].reindex(centres).fillna(0)
        for mth in METHODES:
            pr = prevoir(D, t, mth, centres, criteres)
            for c in centres:
                detail.append((t, mth, c, pr[c], reel[c]))
    d = pd.DataFrame(detail, columns=["mois", "methode", "centre", "prevision", "reel"])
    for mth, g in d.groupby("methode"):
        err = (g["prevision"] - g["reel"]).abs()
        nat = g.groupby("mois")[["prevision", "reel"]].sum()
        lignes.append({
            "methode": mth, "libelle": LIBELLES[mth],
            "information_future": mth not in METHODES_SANS_FUITE,
            "erreur_ponderee_pct": 100 * err.sum() / g["reel"].sum(),
            "erreur_mediane_centre_pct": 100 * (err / g["reel"].replace(0, np.nan)).median(),
            "biais_pct": 100 * (g["prevision"].sum() / g["reel"].sum() - 1),
            "erreur_totale_nationale_pct": 100 * ((nat["prevision"] - nat["reel"]).abs() / nat["reel"]).mean(),
        })
    return pd.DataFrame(lignes).sort_values("erreur_ponderee_pct").reset_index(drop=True), d


def valider_allocations(D, origines, centres, methode_prev, criteres, fn_scores, stock, coef_pic,
                        annee_pic, prog_max, regles):
    """Même enveloppe (encaissement national réel du mois) et mêmes plafonds pour toutes les règles :
    on mesure quelle règle répartit le mieux le total réel entre les centres."""
    lignes = []
    for t in origines:
        reel = D.enc[t].reindex(centres).fillna(0)
        env = reel.sum()
        prev = prevoir(D, t, methode_prev, centres, criteres)
        plaf = plafonds_a_date(D, t, coef_pic, annee_pic, prog_max).reindex(centres).fillna(np.inf)
        scores = fn_scores(tableau_criteres(D, t, centres))
        for nom, (regle, w) in regles.items():
            parts = parts_effort(regle, prev, scores, stock, D.zones.reindex(centres), w)
            obj = objectifs_regle(prev, env - prev.sum(), parts, plaf, env)
            lignes.append({"mois": t, "regle": nom,
                           "erreur_ponderee_pct": 100 * (obj - reel).abs().sum() / env,
                           "non_reparti": env - obj.sum()})
    d = pd.DataFrame(lignes)
    return d.groupby("regle", as_index=False).agg(
        erreur_ponderee_pct=("erreur_ponderee_pct", "mean"),
        non_reparti_moyen=("non_reparti", "mean")).sort_values("erreur_ponderee_pct")
