"""
Requêtes SQL — détection des secteurs en baisse de volume facturé.

Principe métier
---------------
Le volume facturé d'un secteur sur un cycle est la somme de FAT_QTE_CONS
des factures RELEVÉES (TYPE_FACT = 0) de S_FACTURE_M, à abonnés constants
(abonnés facturés en relevé sur les deux cycles). VOLUMEFACT de
CLOTURE_SECTEUR n'est plus utilisé pour la comparaison. On le compare au cycle N-2,
c'est-à-dire au cycle précédent de même parité : la relève étant bimestrielle,
c'est le seul cycle où ce sont LES MÊMES abonnés qui ont été relevés.

Toute baisse est une alerte. L'écart est donné en m³ et en pourcentage.

Deux niveaux de lecture
-----------------------
1. GLOBAL  — par centre puis par secteur : où le volume a chuté.
2. DÉTAIL  — par référence d'abonné dans un secteur : quelles références
   portent la chute. C'est ici que la cause apparaît : une référence
   disparue de la facturation, un index bloqué, un compteur remplacé.

Tables lues (toutes alimentées par le dashboard, jamais modifiées ici)
----------------------------------------------------------------------
raw_cloture_secteur  IDCLOTURE, STR_ID, SECT_ID, ANNEE, MOIS, NBABO,
                     NBTRAITER, POURCTRAITER, VOLUMEFACT, VOLUMESTIM,
                     CLOTURE, DATE_CLOT
raw_facture_m        FACT_ID, FACT_NUM, FACT_DATE, STR_ID, SECT_ID, ABN_ID,
                     REL_ID, FACT_ANNEE, FACT_PERIODE, TYPE_FACT,
                     FAT_QTE_CONS, FACT_MNT, FLAGANNULLE
raw_releve           REL_ID, REL_DATE, REL_INDEX, REL_ANCIEN_INDEX,
                     REL_CONSOM_CALCUL, ID_COMP, CPT_REF
raw_abonnement       ABN_ID, ABN_ADRESSE, COMP_ADRESSE, QUARTIER,
                     ANCIENNE_REFRENCE
raw_compteur         ABN_ID, CPT_SERIE, CPI_REF, CPI_DATE_INS
ref_centre           CENTRE_ID, CENTRE_LIB, ZONE_ID
ref_secteur          SECT_ID, SECT_LIB, SECT_CODE, STR_ID
"""

from __future__ import annotations

import time
from typing import Any

from src import config, cycles, db

# Entités administratives exclues — littéral sûr : ce sont des entiers de
# config, jamais une entrée utilisateur.
_EXCLU = ", ".join(str(x) for x in config.PSEUDO_STRUCTURES)

_ZONES = config.ZONE_LIBELLES


def _mat(expr: str) -> str:
    """Nettoie un matricule.

    MATRICULE et MAT_RELEV sont numériques dans Oracle : après le passage
    par pandas ils arrivent en « 7804.0 ». Le CAST est désormais fait à
    l'extraction, mais cette expression répare aussi les données déjà
    chargées, sans tout ré-extraire.
    """
    return (f"NULLIF(TRIM(regexp_replace(CAST({expr} AS VARCHAR), "
            f"'\\.0+$', '')), '')")


# ============================================================
# Briques SQL réutilisées
# ============================================================

# Types de facture (S_FACTURE_M.TYPE_FACT) — table officielle
# CRM_SNDE.TYPE_FACTURE, relevée le 12/11/2026 :
#   0 Relevée · 1 Estimée · 2 Forfaitaire · 3 Arrêt du compte
#   4 Mutation · 5 Débit intercalaire · 6 Pénalité · 7 Redressement
#   8 Nouvel abonnement · 9 Réabonnement · 10 Estimé migré · 11 Migrée
# - « facturé » : factures du cycle normal (0, 1, 2). Les factures
#   d'événement (mutation, arrêt de compte, pénalité…) ne font pas un
#   secteur facturé.
# - volume comparé : factures issues de la RELÈVE — relevée, estimée et
#   forfaitaire (0, 1, 2), demandé par le service le 12/11/2026. Les
#   factures d'événement restent exclues : une mutation ne couvre qu'une
#   partie de la période (cas réel : abonné 16416020, 3 945 m³ en mutation
#   au cycle 11 contre 22 954 m³ relevés au cycle 09).
_TYPES_CONSO = "0, 1, 2"
_TYPES_RELEVEE = "0, 1, 2"

# État du compteur : libellé de COMTAGE (ref_comptage), avec repli sur la
# nomenclature du dashboard quand la jointure ne trouve rien (constaté le
# 12/11/2026 : ID_COMP = 1 partout, libellé toujours vide).
# Une seule ligne par (centre, secteur, cycle) : on garde l'IDCLOTURE le
# plus récent, comme le fait le dashboard.
_CS_DEDUP = f"""
    SELECT STR_ID, SECT_ID, ANNEE, MOIS,
           NBABO, NBTRAITER, NBNONTRAITER, POURCTRAITER,
           COALESCE(VOLUMEFACT, 0) AS VOLUMEFACT,
           COALESCE(VOLUMESTIM, 0) AS VOLUMESTIM,
           COALESCE(CLOTURE, 0)    AS CLOTURE,
           DATE_CLOT
    FROM raw_cloture_secteur
    WHERE STR_ID NOT IN ({_EXCLU})
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY STR_ID, SECT_ID, ANNEE, MOIS
        ORDER BY IDCLOTURE DESC
    ) = 1
"""

# Volume NET des factures (17/11/2026, validé sur le secteur 4 du centre 91,
# période 11, identique à l'EGF). Une facture relevée couvre toute la
# période depuis la relevée précédente : les estimées émises entre les deux
# sont remboursées (ligne PRD_ID 26 de S_FACTURE_DET, en montant seulement).
# Volume remboursé = somme des estimées (type 1) de l'abonné émises depuis
# sa relevée précédente ; volume net = FAT_QTE_CONS - volume remboursé.
# Ex. abonné 15043325 : estimée 25 le 12/10, relevée 51 le 11/11 -> net 26.
# Les factures restent dans leur propre secteur (S_FACTURE_M.SECT_ID),
# abonnés groupés compris. Utilisé partout où l'on additionne des volumes.
# Limite : une estimée antérieure à l'historique chargé (HISTORIQUE_MOIS)
# n'est pas retrouvée, son remboursement n'est alors pas déduit.
# Secteur de rattachement (20/11/2026, validé sur le secteur 23 du centre
# 91, cycle 09 : 351 abonnés passés en secteur 24 entre 09 et 11) : comme
# l'EGF, une facture est comptée dans le secteur ACTUEL de l'abonné
# (raw_abonnement), pas dans le secteur écrit sur la facture. Sinon une
# resectorisation apparaît comme une chute dans l'ancien secteur et une
# hausse dans le nouveau. Repli sur le secteur de la facture si l'abonné
# n'est pas dans raw_abonnement.
_FM = """(
    SELECT f.* REPLACE (
               CASE WHEN f.TYPE_FACT = 0
                    THEN COALESCE(f.FAT_QTE_CONS, 0) - COALESCE(rb.vol_remb, 0)
                    ELSE f.FAT_QTE_CONS
               END AS FAT_QTE_CONS,
               COALESCE(sa.STR_ID_ACT, f.STR_ID)   AS STR_ID,
               COALESCE(sa.SECT_ID_ACT, f.SECT_ID) AS SECT_ID),
           COALESCE(rb.vol_remb, 0) AS vol_remb,
           f.STR_ID  AS STR_ID_FACTURE,
           f.SECT_ID AS SECT_ID_FACTURE
    FROM raw_facture_m f
    -- table précalculée (src/perf.py, rafraîchie à chaque passage du watcher)
    LEFT JOIN abn_secteur_actuel sa ON sa.sa_abn = f.ABN_ID
    LEFT JOIN (
        WITH v AS (
            SELECT FACT_ID, ABN_ID, FACT_DATE, TYPE_FACT,
                   COALESCE(FAT_QTE_CONS, 0) AS vol,
                   MAX(CASE WHEN TYPE_FACT = 0 THEN FACT_DATE END) OVER (
                       PARTITION BY ABN_ID ORDER BY FACT_DATE, FACT_ID
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                   ) AS releve_prec
            FROM raw_facture_m
            WHERE COALESCE(FLAGANNULLE, 0) = 0
              AND TYPE_FACT IN (0, 1, 2)
        )
        SELECT r.FACT_ID, SUM(e.vol) AS vol_remb
        FROM v r
        JOIN v e
          ON e.ABN_ID = r.ABN_ID
         AND e.TYPE_FACT = 1
         AND e.FACT_DATE < r.FACT_DATE
         AND (r.releve_prec IS NULL OR e.FACT_DATE > r.releve_prec)
        WHERE r.TYPE_FACT = 0
        GROUP BY r.FACT_ID
    ) rb ON rb.FACT_ID = f.FACT_ID
)"""

# Nombre de factures émises par secteur et par cycle : sert à savoir si le
# secteur est réellement passé en facturation (EGF).
_FACT_PAR_SECTEUR = f"""
    SELECT STR_ID, SECT_ID, FACT_ANNEE AS ANNEE, FACT_PERIODE AS MOIS,
           COUNT(*)                    AS nb_factures,
           COUNT(*) FILTER (WHERE TYPE_FACT IN ({_TYPES_RELEVEE}))
                                       AS nb_factures_releve,
           SUM(CASE WHEN TYPE_FACT IN ({_TYPES_RELEVEE})
                    THEN COALESCE(FAT_QTE_CONS, 0) ELSE 0 END)
                                       AS vol_releve,
           -- Même volume sans les abonnés groupés (S_ABONNEMENT.GRP_ID
           -- renseigné) : l'EGF ne les contient pas, sert à rapprocher.
           SUM(CASE WHEN TYPE_FACT IN ({_TYPES_RELEVEE}) AND grp_id_abn IS NULL
                    THEN COALESCE(FAT_QTE_CONS, 0) ELSE 0 END)
                                       AS vol_releve_hors_grp
    FROM (
        SELECT x.*, ab.grp_id_abn
        FROM {_FM} x
        LEFT JOIN (SELECT ABN_ID AS g_abn, MAX(GRP_ID) AS grp_id_abn
                   FROM raw_abonnement GROUP BY ABN_ID) ab
          ON ab.g_abn = x.ABN_ID
    ) AS fm_r
    WHERE COALESCE(FLAGANNULLE, 0) = 0
      AND TYPE_FACT IN ({_TYPES_CONSO})
      AND STR_ID NOT IN ({_EXCLU})
    GROUP BY STR_ID, SECT_ID, FACT_ANNEE, FACT_PERIODE
"""

# Classement de l'écart. Appliqué à l'identique au niveau secteur, centre
# et référence pour que les couleurs veuillent dire la même chose partout.
_GRAVITE = f"""
    CASE
        WHEN COALESCE(vol_p, 0) = 0 AND COALESCE(vol_n, 0) = 0 THEN 'VIDE'
        WHEN COALESCE(vol_p, 0) = 0                            THEN 'NOUVEAU'
        WHEN COALESCE(vol_n, 0) = 0                            THEN 'DISPARU'
        WHEN vol_n - vol_p > 0                                 THEN 'HAUSSE'
        WHEN vol_n - vol_p = 0                                 THEN 'STABLE'
        WHEN 100.0 * (vol_n - vol_p) / vol_p
                <= -{config.GRAVITE_FORTE_PCT}                 THEN 'FORTE'
        WHEN 100.0 * (vol_n - vol_p) / vol_p
                <= -{config.GRAVITE_MOYENNE_PCT}               THEN 'MOYENNE'
        ELSE 'LEGERE'
    END
"""


# Au niveau référence, on distingue deux causes de volume nul qui n'ont
# rien à voir : la référence a quitté la facturation (plus aucune facture),
# ou elle est facturée à 0 m³ (compteur bloqué, index recopié). La seconde
# se corrige sur le terrain, la première est un problème de gestion.
_GRAVITE_REF = f"""
    CASE
        WHEN b.vol_p = 0 AND b.vol_n = 0 AND b.FACT_NUM IS NOT NULL
            THEN 'VOLUME_NUL'
        WHEN b.vol_p = 0 AND b.vol_n = 0                 THEN 'VIDE'
        WHEN b.FACT_NUM IS NULL AND b.vol_p > 0          THEN 'DISPARU'
        WHEN b.vol_p = 0                                 THEN 'NOUVEAU'
        WHEN b.vol_n = 0                                 THEN 'VOLUME_NUL'
        WHEN b.vol_n - b.vol_p > 0                       THEN 'HAUSSE'
        WHEN b.vol_n - b.vol_p = 0                       THEN 'STABLE'
        WHEN 100.0 * (b.vol_n - b.vol_p) / b.vol_p
                <= -{config.GRAVITE_FORTE_PCT}           THEN 'FORTE'
        WHEN 100.0 * (b.vol_n - b.vol_p) / b.vol_p
                <= -{config.GRAVITE_MOYENNE_PCT}         THEN 'MOYENNE'
        ELSE 'LEGERE'
    END
"""


# ------------------------------------------------------------
# Gros consommateurs — secteurs facturés en relevé TOUS LES MOIS
# ------------------------------------------------------------
# Repérés sur les 12 mois qui se terminent au cycle N : un secteur est
# mensuel quand au moins la moitié de ses factures relevées concernent un
# abonné DÉJÀ facturé en relevé le mois précédent. Le critère porte sur
# les abonnés et non sur les mois : un secteur qui mélange deux groupes
# bimestriels (l'un les mois impairs, l'autre les mois pairs) est facturé
# tous les mois mais jamais deux fois de suite pour le même abonné — il
# reste comparé à N-2, abonné par abonné.
# Leur comparaison se fait à N-1, celle des autres secteurs à N-2.
SEUIL_FACT_MOIS = 10        # volume minimal : 3 mois x 10 factures
PART_MENSUELLE_MIN = 0.5    # part des factures précédées d'une facture M-1
_cache_mensuels: dict = {}


def secteurs_mensuels(annee: int, mois: int) -> set[int]:
    """SECT_ID des gros consommateurs au cycle N (cache de 2 minutes)."""
    cle = (int(annee), int(mois))
    hit = _cache_mensuels.get(cle)
    if hit and time.monotonic() - hit[0] < 120:
        return hit[1]
    fin = int(annee) * 12 + int(mois)
    rows = db.lignes(f"""
        WITH f AS (
            SELECT DISTINCT STR_ID, SECT_ID, ABN_ID,
                   FACT_ANNEE * 12 + FACT_PERIODE AS mi
            FROM raw_facture_m
            WHERE COALESCE(FLAGANNULLE, 0) = 0
              AND TYPE_FACT IN ({_TYPES_RELEVEE})
              AND STR_ID NOT IN ({_EXCLU})
              AND FACT_ANNEE * 12 + FACT_PERIODE BETWEEN ? AND ?
        )
        SELECT a.SECT_ID
        FROM f a
        LEFT JOIN f b
          ON b.ABN_ID = a.ABN_ID AND b.STR_ID = a.STR_ID
         AND b.SECT_ID = a.SECT_ID AND b.mi = a.mi - 1
        WHERE a.mi > ?               -- le 1er mois n'a pas de M-1 connu
        GROUP BY a.STR_ID, a.SECT_ID
        HAVING COUNT(*) >= {3 * int(SEUIL_FACT_MOIS)}
           AND COUNT(b.ABN_ID) >= {float(PART_MENSUELLE_MIN)} * COUNT(*)
    """, [fin - 11, fin, fin - 11])
    res = {int(x["SECT_ID"]) for x in rows if x.get("SECT_ID") is not None}
    _cache_mensuels[cle] = (time.monotonic(), res)
    return res


def _comparaison(annee: int, mois: int, population: str):
    """(annee_ref, mois_ref, condition SQL) selon la population.

    bimestriel : tous les secteurs sauf les gros consommateurs, à N-2.
    mensuel    : les gros consommateurs seuls, à N-1.
    """
    mens = secteurs_mensuels(annee, mois)
    liste = ", ".join(str(int(x)) for x in sorted(mens))
    if population == "mensuel":
        pa, pm = cycles.mois_precedent(annee, mois)
        cond = f"secteur_code IN ({liste})" if liste else "FALSE"
    else:
        pa, pm = cycles.cycle_precedent(annee, mois)
        cond = f"secteur_code NOT IN ({liste})" if liste else "TRUE"
    return pa, pm, cond


def precedent_secteur(annee: int, mois: int, sect_id: int) -> tuple[int, int]:
    """Cycle de comparaison d'un secteur : N-1 si gros consommateur."""
    if int(sect_id) in secteurs_mensuels(annee, mois):
        return cycles.mois_precedent(annee, mois)
    return cycles.cycle_precedent(annee, mois)


def _lib_comparaison(annee: int, mois: int, pa: int, pm: int) -> str:
    return "N-1" if (pa, pm) == cycles.mois_precedent(annee, mois) else "N-2"


def _zone(zone_id: Any) -> str:
    try:
        return _ZONES.get(int(zone_id), "(zone inconnue)")
    except (TypeError, ValueError):
        return "(zone inconnue)"


# ============================================================
# 1. Cycles disponibles
# ============================================================

def liste_cycles() -> dict:
    """Cycles présents dans CLOTURE_SECTEUR, du plus récent au plus ancien.

    Chaque cycle est accompagné de son cycle de comparaison N-2 et d'un
    indicateur `comparable` : faux quand le N-2 n'est pas dans la base.
    """
    rows = db.lignes(f"""
        WITH cs AS ({_CS_DEDUP})
        SELECT ANNEE * 100 + MOIS            AS cycle_key,
               ANNEE                         AS annee,
               MOIS                          AS mois,
               COUNT(DISTINCT STR_ID)        AS nb_centres,
               COUNT(*)                      AS nb_secteurs,
               CAST(SUM(VOLUMEFACT) AS BIGINT) AS volume
        FROM cs
        -- Pas de cycle futur : Oracle crée à l'avance des lignes de
        -- clôture (constaté le 13/11/2026 : 12/2026 pour l'Intérieur).
        WHERE ANNEE * 100 + MOIS <= CAST(strftime(current_date, '%Y%m') AS INTEGER)
        GROUP BY ANNEE, MOIS
        ORDER BY cycle_key DESC
        LIMIT {int(config.NB_CYCLES_LISTE)}
    """)

    presents = {int(r["cycle_key"]) for r in rows}
    # Cycles qui ont déjà des factures : le cycle ouvert par défaut doit
    # en avoir, sinon l'écran est vide.
    factures = {int(x["k"]) for x in db.lignes(f"""
        SELECT DISTINCT FACT_ANNEE * 100 + FACT_PERIODE AS k
        FROM raw_facture_m
        WHERE COALESCE(FLAGANNULLE, 0) = 0 AND TYPE_FACT IN ({_TYPES_CONSO})
    """) if x.get("k") is not None}
    out = []
    for r in rows:
        key = int(r["cycle_key"])
        prec = cycles.cycle_precedent_key(key)
        out.append({
            **r,
            "cycle_key": key,
            "libelle": cycles.libelle_key(key),
            "parite": cycles.parite(int(r["mois"])),
            "cycle_precedent": prec,
            "libelle_precedent": cycles.libelle_key(prec),
            "comparable": prec in presents,
        })

    # Cycle ouvert par défaut : le plus récent qui a un N-2 comparable,
    # sinon le plus récent tout court. Ouvrir un cycle sans point de
    # comparaison afficherait un écran vide.
    defaut = next((c["cycle_key"] for c in out
                   if c["comparable"] and c["cycle_key"] in factures), None)
    if defaut is None:
        defaut = next((c["cycle_key"] for c in out if c["comparable"]), None)
    if defaut is None and out:
        defaut = out[0]["cycle_key"]

    return {"cycles": out, "defaut": defaut}


# ============================================================
# 2. Comparaison par secteur — la table de référence
# ============================================================

def _sql_secteurs() -> str:
    """Comparaison secteur par secteur entre le cycle N et le cycle N-2.

    FULL OUTER JOIN volontaire : un secteur facturé en N-2 et absent en N
    est la baisse la plus grave qui existe, il ne doit pas disparaître de
    la liste.
    """
    return f"""
    WITH cs AS ({_CS_DEDUP}),
         fa AS ({_FACT_PAR_SECTEUR}),
         cyc AS (SELECT CAST(? AS BIGINT) AS an, CAST(? AS BIGINT) AS mn,
                        CAST(? AS BIGINT) AS ap, CAST(? AS BIGINT) AS mp),
         n AS (SELECT cs.* FROM cs, cyc WHERE ANNEE = an AND MOIS = mn),
         p AS (SELECT cs.* FROM cs, cyc WHERE ANNEE = ap AND MOIS = mp),
         fn AS (SELECT * FROM fa WHERE ANNEE = ? AND MOIS = ?),
         fp AS (SELECT * FROM fa WHERE ANNEE = ? AND MOIS = ?),
    -- Volume relevé par abonné et par cycle (factures relevées seules).
    rel AS (
        SELECT f.STR_ID, f.SECT_ID, f.ABN_ID,
               f.FACT_ANNEE AS ANNEE, f.FACT_PERIODE AS MOIS,
               SUM(COALESCE(f.FAT_QTE_CONS, 0)) AS v
        FROM {_FM} f, cyc
        WHERE COALESCE(f.FLAGANNULLE, 0) = 0
          AND f.TYPE_FACT IN ({_TYPES_RELEVEE})
          AND ((f.FACT_ANNEE = an AND f.FACT_PERIODE = mn)
            OR (f.FACT_ANNEE = ap AND f.FACT_PERIODE = mp))
        GROUP BY f.STR_ID, f.SECT_ID, f.ABN_ID, f.FACT_ANNEE, f.FACT_PERIODE
    ),
    -- Comparaison DIRECTE N vs N-2 (N-1 pour les gros consommateurs) :
    -- plus de référence lissée (médiane), le volume du cycle de
    -- comparaison est pris tel quel.
    -- Abonnés perdus : facturés en relevé au cycle de référence, sans
    -- facture relevée au cycle N (estimés, non facturés, sortis). Ils ne
    -- sont pas dans la comparaison, mais leur nombre et leur volume sont
    -- remontés à part.
    perdus AS (
        SELECT rp.STR_ID, rp.SECT_ID,
               COUNT(*)                      AS nb_perdus,
               SUM(rp.v)                     AS vol_perdus
        FROM rel rp
        JOIN cyc ON rp.ANNEE = cyc.ap AND rp.MOIS = cyc.mp
        LEFT JOIN rel rn
          ON rn.ABN_ID = rp.ABN_ID
         AND rn.STR_ID = rp.STR_ID AND rn.SECT_ID = rp.SECT_ID
         AND rn.ANNEE = cyc.an AND rn.MOIS = cyc.mn
        WHERE rn.ABN_ID IS NULL
        GROUP BY rp.STR_ID, rp.SECT_ID
    ),
    -- Abonnés ajoutés : facturés en relevé au cycle N, sans facture
    -- relevée au cycle de comparaison (nouveaux, ou de retour).
    ajoutes AS (
        SELECT rn.STR_ID, rn.SECT_ID,
               COUNT(*)                      AS nb_ajoutes,
               SUM(rn.v)                     AS vol_ajoutes
        FROM rel rn
        JOIN cyc ON rn.ANNEE = cyc.an AND rn.MOIS = cyc.mn
        LEFT JOIN rel rp
          ON rp.ABN_ID = rn.ABN_ID
         AND rp.STR_ID = rn.STR_ID AND rp.SECT_ID = rn.SECT_ID
         AND rp.ANNEE = cyc.ap AND rp.MOIS = cyc.mp
        WHERE rp.ABN_ID IS NULL
        GROUP BY rn.STR_ID, rn.SECT_ID
    ),
    -- Comparaison à abonnés constants : seuls comptent les abonnés
    -- facturés en relevé sur les DEUX cycles. Un abonné facturé en
    -- mutation, en estimé ou pas encore facturé n'introduit pas de fausse
    -- baisse (cas réel : abonné 16416020, mutation au cycle 11).
    paire AS (
        SELECT rn.STR_ID, rn.SECT_ID,
               SUM(rn.v)                     AS vol_n,
               -- Volume du cycle de comparaison, brut (N-2 ou N-1).
               SUM(rp.v)                     AS vol_p,
               SUM(rp.v)                     AS vol_p_cycle,
               COUNT(*)                      AS nb_abonnes_compares
        FROM rel rn
        JOIN cyc ON rn.ANNEE = cyc.an AND rn.MOIS = cyc.mn
        JOIN rel rp
          ON rp.ABN_ID = rn.ABN_ID
         AND rp.STR_ID = rn.STR_ID AND rp.SECT_ID = rn.SECT_ID
         AND rp.ANNEE = cyc.ap AND rp.MOIS = cyc.mp
        GROUP BY rn.STR_ID, rn.SECT_ID
    ),
    base AS (
        SELECT
            COALESCE(n.STR_ID,  p.STR_ID,  fn.STR_ID)  AS centre_code,
            COALESCE(n.SECT_ID, p.SECT_ID, fn.SECT_ID) AS secteur_code,
            n.NBABO                        AS parc_n,
            p.NBABO                        AS parc_p,
            n.NBTRAITER                    AS saisis_n,
            n.POURCTRAITER                 AS pct_saisie_n,
            n.CLOTURE                      AS cloture_n,
            n.DATE_CLOT                    AS date_cloture_n,
            -- Volume comparé : factures RELEVÉES (type 0) des abonnés
            -- relevés sur les deux cycles — pas VOLUMEFACT.
            COALESCE(pa.vol_n, 0)          AS vol_n,
            COALESCE(pa.vol_p, 0)          AS vol_p,
            COALESCE(pa.nb_abonnes_compares, 0) AS nb_abonnes_compares,
            COALESCE(pa.vol_p_cycle, 0)    AS vol_p_cycle,
            COALESCE(pe.nb_perdus, 0)      AS nb_perdus,
            COALESCE(pe.vol_perdus, 0)     AS vol_perdus,
            COALESCE(aj.nb_ajoutes, 0)     AS nb_ajoutes,
            COALESCE(aj.vol_ajoutes, 0)    AS vol_ajoutes,
            COALESCE(n.VOLUMESTIM, 0)      AS vol_estime_n,
            COALESCE(p.VOLUMESTIM, 0)      AS vol_estime_p,
            COALESCE(fn.nb_factures, 0)    AS nb_factures_n,
            COALESCE(fp.nb_factures, 0)    AS nb_factures_p,
            -- Volume total = factures relevée/estimée/forfait non
            -- annulées de S_FACTURE_M (groupés compris), en volume NET
            -- (estimées remboursées déduites), secteur de la facture.
            -- VOLUMEFACT de la clôture n'est plus utilisé : c'est une photo
            -- prise à la clôture (factures émises après absentes, 0 pour
            -- un secteur facturé sans clôture).
            COALESCE(fn.vol_releve, 0)     AS vol_total_n,
            COALESCE(fp.vol_releve, 0)     AS vol_total_p,
            COALESCE(fn.vol_releve_hors_grp, 0) AS vol_total_hors_grp_n,
            COALESCE(fp.vol_releve_hors_grp, 0) AS vol_total_hors_grp_p
        FROM n
        FULL OUTER JOIN p
          ON p.STR_ID = n.STR_ID AND p.SECT_ID = n.SECT_ID
        FULL OUTER JOIN fn
          ON fn.STR_ID = COALESCE(n.STR_ID, p.STR_ID)
         AND fn.SECT_ID = COALESCE(n.SECT_ID, p.SECT_ID)
        LEFT JOIN fp
          ON fp.STR_ID = COALESCE(n.STR_ID, p.STR_ID, fn.STR_ID)
         AND fp.SECT_ID = COALESCE(n.SECT_ID, p.SECT_ID, fn.SECT_ID)
        LEFT JOIN paire pa
          ON pa.STR_ID = COALESCE(n.STR_ID, p.STR_ID, fn.STR_ID)
         AND pa.SECT_ID = COALESCE(n.SECT_ID, p.SECT_ID, fn.SECT_ID)
        LEFT JOIN perdus pe
          ON pe.STR_ID = COALESCE(n.STR_ID, p.STR_ID, fn.STR_ID)
         AND pe.SECT_ID = COALESCE(n.SECT_ID, p.SECT_ID, fn.SECT_ID)
        LEFT JOIN ajoutes aj
          ON aj.STR_ID = COALESCE(n.STR_ID, p.STR_ID, fn.STR_ID)
         AND aj.SECT_ID = COALESCE(n.SECT_ID, p.SECT_ID, fn.SECT_ID)
    )
    SELECT
        b.centre_code,
        COALESCE(ce.CENTRE_LIB, '(centre ' || CAST(b.centre_code AS VARCHAR) || ')')
                                                      AS centre_libelle,
        ce.ZONE_ID                                    AS zone_id,
        b.secteur_code,
        COALESCE(NULLIF(TRIM(sec.SECT_CODE), ''),
                 CAST(b.secteur_code AS VARCHAR))     AS secteur_num,
        COALESCE(sec.SECT_LIB, '(secteur ' || CAST(b.secteur_code AS VARCHAR) || ')')
                                                      AS secteur_libelle,
        b.parc_n, b.parc_p, b.saisis_n, b.pct_saisie_n,
        b.cloture_n, b.date_cloture_n,
        b.nb_factures_n, b.nb_factures_p,
        b.vol_estime_n, b.vol_estime_p,
        ROUND(b.vol_p_cycle, 1)                       AS vol_p_cycle,
        b.nb_abonnes_compares,
        b.nb_perdus,
        ROUND(b.vol_perdus, 1)                        AS vol_perdus,
        b.nb_ajoutes,
        ROUND(b.vol_ajoutes, 1)                       AS vol_ajoutes,
        b.vol_total_n,
        b.vol_total_p,
        ROUND(b.vol_total_hors_grp_n, 1)              AS vol_total_hors_grp_n,
        ROUND(b.vol_total_hors_grp_p, 1)              AS vol_total_hors_grp_p,
        ROUND(b.vol_n, 1)                             AS vol_n,
        ROUND(b.vol_p, 1)                             AS vol_p,
        ROUND(b.vol_n - b.vol_p, 1)                   AS ecart,
        CASE WHEN b.vol_p > 0
             THEN ROUND(100.0 * (b.vol_n - b.vol_p) / b.vol_p, 1)
        END                                           AS ecart_pct,
        ROUND(b.vol_total_n - b.vol_total_p, 1)      AS ecart_total,
        CASE WHEN b.vol_total_p > 0
             THEN ROUND(100.0 * (b.vol_total_n - b.vol_total_p) / b.vol_total_p, 1)
        END                                           AS ecart_total_pct,
        -- Cause principale d'une baisse du volume total : la perte la plus
        -- grosse entre celle des abonnés constants et celle des abonnés
        -- perdus (facturés au cycle de comparaison, plus en N).
        CASE WHEN (b.vol_n - b.vol_p) < 0
              AND (b.vol_p - b.vol_n) >= COALESCE(b.vol_perdus, 0)
             THEN 'Abonnés constants'
             ELSE 'Abonnés perdus'
        END                                           AS cause,
        -- Gravité calculée sur le volume facturé TOTAL, comme l'alerte.
        {_GRAVITE.replace('vol_n', 'b.vol_total_n').replace('vol_p', 'b.vol_total_p')}
                                                      AS gravite,
        -- Une saisie inachevée fait baisser le volume mécaniquement :
        -- la comparaison n'est définitive qu'une fois le secteur clôturé.
        CASE
            WHEN b.cloture_n = 1
             AND COALESCE(b.pct_saisie_n, 0) >= {config.SAISIE_COMPLETE_PCT}
                THEN 'definitive'
            ELSE 'provisoire'
        END                                           AS fiabilite,
        -- État d'avancement du secteur sur le cycle N.
        -- Un secteur est FACTURE dès qu'il a des factures de consommation,
        -- même sans ligne de clôture (49 secteurs facturés sans clôture
        -- sur le cycle 11/2026 étaient invisibles avant ce correctif).
        CASE
            WHEN b.nb_factures_n > 0                  THEN 'FACTURE'
            WHEN b.parc_n IS NULL                     THEN 'ABSENT'
            WHEN b.cloture_n = 1                      THEN 'CLOTURE'
            WHEN COALESCE(b.saisis_n, 0) = 0          THEN 'NON_COMMENCE'
            WHEN COALESCE(b.pct_saisie_n, 0) >= 100   THEN 'SAISIE_FINIE'
            ELSE 'EN_COURS'
        END                                           AS etat
    FROM base b
    LEFT JOIN ref_centre  ce  ON ce.CENTRE_ID = b.centre_code
    LEFT JOIN ref_secteur sec ON sec.SECT_ID  = b.secteur_code
    """


# ============================================================
# Règle d'alerte — une seule définition pour tout le monde
# ============================================================
# Journal des alertes, bandeau, cartes, onglet Baisses, Excel : tous
# utilisent ces trois prédicats, pour que les chiffres concordent.

def cond_baisse_constants() -> str:
    """Abonnés constants en baisse d'au moins SEUIL_BAISSE_M3."""
    s = float(config.SEUIL_BAISSE_M3)
    return f"(COALESCE(ecart, 0) < 0 AND COALESCE(ecart, 0) <= -{s})"


def cond_baisse_total() -> str:
    """Volume facturé total du secteur en baisse d'au moins SEUIL_BAISSE_M3."""
    s = float(config.SEUIL_BAISSE_M3)
    return (f"(COALESCE(ecart_total, 0) < 0 "
            f"AND COALESCE(ecart_total, 0) <= -{s})")


def cond_alerte() -> str:
    """Secteur en alerte : volume facturé total en baisse d'au moins le seuil."""
    return cond_baisse_total()


CAUSE_CONSTANTS = "Abonnés constants"
CAUSE_PERDUS = "Abonnés perdus"


def secteurs(
    annee: int,
    mois: int,
    str_id: int | None = None,
    baisse_min_pct: float = 0.0,
    seulement_baisses: bool = True,
    population: str = "bimestriel",
    zone: int | None = None,
) -> list[dict]:
    """Liste des secteurs, triée par perte de volume décroissante.

    baisse_min_pct : filtre d'affichage (0 = toutes les baisses).
    population     : « bimestriel » (N-2, défaut) ou « mensuel » (gros
                     consommateurs, N-1).
    """
    pa, pm, cond_pop = _comparaison(annee, mois, population)
    params: list = [annee, mois, pa, pm, annee, mois, pa, pm]

    conditions = [cond_pop]
    if zone is not None:
        conditions.append(f"zone_id = {int(zone)}")
    if str_id is not None:
        conditions.append("centre_code = ?")
        params.append(str_id)

    # Seuls les secteurs facturés sur le cycle N sont comparés : la
    # baisse se juge au moment où le secteur est facturé.
    conditions.append("etat = 'FACTURE'")

    if seulement_baisses:
        # Secteur retenu si son volume facturé total baisse d'au moins
        # config.SEUIL_BAISSE_M3 m³ (cause indiquée dans la colonne `cause`).
        conditions.append(cond_alerte())
        if baisse_min_pct and baisse_min_pct > 0:
            conditions.append(
                "(ecart_pct IS NULL OR ecart_pct <= ?)"
            )
            params.append(-abs(float(baisse_min_pct)))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = db.lignes(f"""
        SELECT * FROM ({_sql_secteurs()}) t
        {where}
        ORDER BY ecart_total ASC NULLS LAST, centre_libelle, secteur_num
    """, params)

    comp = _lib_comparaison(annee, mois, pa, pm)
    for r in rows:
        r["zone_lib"] = _zone(r.get("zone_id"))
        r["comparaison"] = comp
    return rows


# ============================================================
# 3. Agrégat par centre
# ============================================================

def centres(annee: int, mois: int,
            population: str = "bimestriel",
            zone: int | None = None) -> list[dict]:
    """Volume N vs N-2 (N-1 pour les gros consommateurs) par centre."""
    pa, pm, cond_pop = _comparaison(annee, mois, population)
    params: list = [annee, mois, pa, pm, annee, mois, pa, pm]

    rows = db.lignes(f"""
        WITH t AS ({_sql_secteurs()})
        SELECT
            centre_code,
            centre_libelle,
            zone_id,
            COUNT(*)                                   AS nb_secteurs,
            COUNT(*) FILTER (WHERE {cond_alerte()})    AS nb_en_baisse,
            COUNT(*) FILTER (WHERE gravite = 'DISPARU') AS nb_disparus,
            COUNT(*) FILTER (WHERE fiabilite = 'provisoire') AS nb_provisoires,
            CAST(SUM(vol_n) AS BIGINT)                 AS vol_n,
            CAST(SUM(vol_p) AS BIGINT)                 AS vol_p,
            CAST(SUM(vol_n) - SUM(vol_p) AS BIGINT)    AS ecart,
            CASE WHEN SUM(vol_p) > 0
                 THEN ROUND(100.0 * (SUM(vol_n) - SUM(vol_p)) / SUM(vol_p), 1)
            END                                        AS ecart_pct,
            CAST(COALESCE(SUM(-ecart) FILTER (
                     WHERE {cond_alerte()} AND ecart < 0), 0) AS BIGINT)
                                                       AS volume_perdu,
            CAST(COALESCE(SUM(-ecart_total) FILTER (
                     WHERE {cond_alerte()} AND ecart_total < 0), 0) AS BIGINT)
                                                       AS baisse_totale,
            CAST(SUM(nb_perdus) AS BIGINT)             AS nb_perdus,
            CAST(SUM(vol_perdus) AS BIGINT)            AS vol_perdus
        FROM t
        WHERE etat = 'FACTURE' AND {cond_pop}
          AND {f"zone_id = {int(zone)}" if zone is not None else "TRUE"}
        GROUP BY centre_code, centre_libelle, zone_id
        ORDER BY ecart ASC NULLS LAST
    """, params)

    for r in rows:
        r["zone_lib"] = _zone(r.get("zone_id"))
        vp, vn = r.get("vol_p") or 0, r.get("vol_n") or 0
        if vp == 0 and vn == 0:
            r["gravite"] = "VIDE"
        elif vp == 0:
            r["gravite"] = "NOUVEAU"
        elif vn == 0:
            r["gravite"] = "DISPARU"
        else:
            p = 100.0 * (vn - vp) / vp
            r["gravite"] = (
                "FORTE" if p <= -config.GRAVITE_FORTE_PCT
                else "MOYENNE" if p <= -config.GRAVITE_MOYENNE_PCT
                else "LEGERE" if p < 0
                else "STABLE" if p == 0
                else "HAUSSE"
            )
    return rows


# ============================================================
# 4. Synthèse globale
# ============================================================

def synthese(annee: int, mois: int,
             population: str = "bimestriel",
             zone: int | None = None) -> dict:
    """Les chiffres de l'en-tête : volume, écart, nombre d'alertes."""
    pa, pm, cond_pop = _comparaison(annee, mois, population)
    params: list = [annee, mois, pa, pm, annee, mois, pa, pm]

    row = db.ligne(f"""
        WITH t AS ({_sql_secteurs()})
        SELECT
            CAST(SUM(vol_n) AS BIGINT)                   AS vol_n,
            CAST(SUM(vol_p) AS BIGINT)                   AS vol_p,
            CAST(SUM(vol_n) - SUM(vol_p) AS BIGINT)      AS ecart,
            CASE WHEN SUM(vol_p) > 0
                 THEN ROUND(100.0 * (SUM(vol_n) - SUM(vol_p)) / SUM(vol_p), 1)
            END                                          AS ecart_pct,
            COUNT(*)                                     AS nb_secteurs,
            COUNT(*) FILTER (WHERE {cond_alerte()})      AS nb_en_baisse,
            COUNT(*) FILTER (WHERE {cond_alerte()}
                               AND cause = '{CAUSE_CONSTANTS}')
                                                         AS nb_cause_constants,
            COUNT(*) FILTER (WHERE {cond_alerte()}
                               AND cause = '{CAUSE_PERDUS}')
                                                         AS nb_cause_perdus,
            COUNT(*) FILTER (WHERE gravite = 'FORTE')    AS nb_forte,
            COUNT(*) FILTER (WHERE gravite = 'MOYENNE')  AS nb_moyenne,
            COUNT(*) FILTER (WHERE gravite = 'LEGERE')   AS nb_legere,
            COUNT(*) FILTER (WHERE gravite = 'DISPARU')  AS nb_disparus,
            COUNT(*) FILTER (WHERE fiabilite = 'provisoire' AND {cond_alerte()})
                                                         AS nb_provisoires,
            COUNT(DISTINCT centre_code) FILTER (WHERE {cond_alerte()})
                                                         AS nb_centres_touches,
            CAST(COALESCE(SUM(-ecart) FILTER (
                     WHERE {cond_alerte()} AND ecart < 0), 0) AS BIGINT)
                                                         AS volume_perdu,
            CAST(COALESCE(SUM(-ecart_total) FILTER (
                     WHERE {cond_alerte()} AND ecart_total < 0), 0) AS BIGINT)
                                                         AS baisse_totale,
            CAST(SUM(nb_perdus) AS BIGINT)               AS nb_perdus,
            CAST(SUM(vol_perdus) AS BIGINT)              AS vol_perdus,
            CAST(SUM(vol_estime_n) AS BIGINT)            AS vol_estime_n,
            CAST(SUM(vol_estime_p) AS BIGINT)            AS vol_estime_p
        FROM t
        WHERE etat = 'FACTURE' AND {cond_pop}
          AND {f"zone_id = {int(zone)}" if zone is not None else "TRUE"}
    """, params)

    return {
        **row,
        "cycle": cycles.libelle(annee, mois),
        "cycle_precedent": cycles.libelle(pa, pm),
        "comparaison": _lib_comparaison(annee, mois, pa, pm),
        "cycle_key": cycles.cycle_key(annee, mois),
        "cycle_precedent_key": cycles.cycle_key(pa, pm),
        "parite": cycles.parite(mois),
        "population": population,
        "seuil_m3": float(config.SEUIL_BAISSE_M3),
        "nb_gros_consommateurs": len(secteurs_mensuels(annee, mois)),
    }


# ============================================================
# 5. Détail par référence — le drill-down
# ============================================================

def references(annee: int, mois: int, str_id: int, sect_id: int) -> dict:
    """Comparaison référence par référence dans un secteur.

    FULL OUTER JOIN entre les factures du cycle N et celles du cycle N-2 :
    une référence facturée en N-2 et absente en N apparaît avec l'état
    DISPARU. C'est la cause la plus fréquente d'une chute de secteur.
    """
    pa, pm = precedent_secteur(annee, mois, sect_id)

    sql = f"""
    WITH
    fn AS (
        SELECT FACT_ID, ABN_ID, REL_ID, FACT_NUM, FACT_DATE, TYPE_FACT,
               CASE WHEN TYPE_FACT IN ({_TYPES_RELEVEE})
                    THEN COALESCE(FAT_QTE_CONS, 0) ELSE 0 END AS vol,
               FACT_MNT
        FROM {_FM} AS fm_r
        WHERE STR_ID = ? AND SECT_ID = ?
          AND FACT_ANNEE = ? AND FACT_PERIODE = ?
          AND COALESCE(FLAGANNULLE, 0) = 0
          AND TYPE_FACT IN ({_TYPES_CONSO})
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ABN_ID
                                   ORDER BY FACT_ID DESC) = 1
    ),
    fp AS (
        SELECT FACT_ID, ABN_ID, REL_ID,
               CASE WHEN TYPE_FACT IN ({_TYPES_RELEVEE})
                    THEN COALESCE(FAT_QTE_CONS, 0) ELSE 0 END AS vol,
               TYPE_FACT
        FROM {_FM} AS fm_r
        WHERE STR_ID = ? AND SECT_ID = ?
          AND FACT_ANNEE = ? AND FACT_PERIODE = ?
          AND COALESCE(FLAGANNULLE, 0) = 0
          AND TYPE_FACT IN ({_TYPES_CONSO})
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ABN_ID
                                   ORDER BY FACT_ID DESC) = 1
    ),
    cpt AS (
        SELECT ABN_ID, CPT_SERIE, CPI_REF
        FROM raw_compteur
        -- perf : seulement les abonnés du secteur (au lieu de tout le pays)
        WHERE ABN_ID IN (SELECT ABN_ID FROM fn UNION SELECT ABN_ID FROM fp)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY ABN_ID
            ORDER BY CPI_DATE_INS DESC NULLS LAST, ID DESC
        ) = 1
    ),
    base AS (
        SELECT COALESCE(fn.ABN_ID, fp.ABN_ID) AS abn_id,
               fn.REL_ID, fn.FACT_NUM, fn.FACT_DATE, fn.TYPE_FACT,
               fn.FACT_MNT,
               fp.REL_ID           AS rel_id_p,
               COALESCE(fn.vol, 0) AS vol_n,
               COALESCE(fp.vol, 0) AS vol_p,
               fp.TYPE_FACT        AS type_fact_p
        FROM fn
        FULL OUTER JOIN fp ON fp.ABN_ID = fn.ABN_ID
    )
    SELECT
        b.abn_id                                      AS reference,
        abn.ANCIENNE_REFRENCE                         AS ancienne_reference,
        COALESCE(NULLIF(TRIM(abn.ABN_ADRESSE), ''), '')  AS adresse,
        COALESCE(NULLIF(TRIM(abn.COMP_ADRESSE), ''), '') AS complement_adresse,
        abn.QUARTIER                                  AS quartier,
        cpt.CPT_SERIE                                 AS num_compteur,
        cpt.CPI_REF                                   AS ref_compteur,
        r.CPT_REF                                     AS ref_releve,
        r.REL_ANCIEN_INDEX                            AS index_debut,
        r.REL_INDEX                                   AS index_fin,
        r.REL_CONSOM_CALCUL                           AS conso_relevee,
        r.REL_DATE                                    AS date_releve,
        r.ID_COMP                                     AS id_comp,
        COALESCE(NULLIF(TRIM(cp.LIBELLE), ''),
                 CASE r.ID_COMP
                     WHEN 1 THEN 'COMPTEUR ACCESSIBLE'  WHEN 2 THEN 'COMPTEUR ILLISIBLE'
                     WHEN 3 THEN 'COMPTEUR DEFECTUEUX'  WHEN 4 THEN 'COMPTEUR BLOQUE'
                     WHEN 5 THEN 'COMPTEUR INACCESSIBLE' WHEN 6 THEN 'COMPTEUR VOLE'
                 END)                         AS etat_compteur,
        -- Index du cycle N-2 : deux index identiques d'un cycle à l'autre
        -- signent un compteur bloqué ou un relevé recopié.
        rp.REL_INDEX                                  AS index_fin_p,
        rp.ID_COMP                                    AS id_comp_p,
        b.FACT_NUM                                    AS num_facture,
        b.FACT_DATE                                   AS date_facture,
        b.FACT_MNT                                    AS montant,
        CASE b.TYPE_FACT
            WHEN 0 THEN 'Relevée'
            WHEN 1 THEN 'Estimée'
            WHEN 2 THEN 'Forfaitaire'
            WHEN 3 THEN 'Arrêt du compte'
            WHEN 4 THEN 'Mutation'
            WHEN 5 THEN 'Débit intercalaire'
            WHEN 6 THEN 'Pénalité'
            WHEN 7 THEN 'Redressement'
            WHEN 8 THEN 'Nouvel abonnement'
            WHEN 9 THEN 'Réabonnement'
            WHEN 10 THEN 'Estimé migré'
            WHEN 11 THEN 'Migrée'
            ELSE 'Autre (' || CAST(b.TYPE_FACT AS VARCHAR) || ')'
        END                                           AS type_facture,
        ROUND(b.vol_n, 1)                             AS vol_n,
        ROUND(b.vol_p, 1)                             AS vol_p,
        ROUND(b.vol_n - b.vol_p, 1)                   AS ecart,
        CASE WHEN b.vol_p > 0
             THEN ROUND(100.0 * (b.vol_n - b.vol_p) / b.vol_p, 1)
        END                                           AS ecart_pct,
        {_GRAVITE_REF}                                AS gravite
    FROM base b
    LEFT JOIN raw_abonnement abn ON abn.ABN_ID = b.abn_id
    LEFT JOIN cpt                ON cpt.ABN_ID = b.abn_id
    LEFT JOIN raw_releve     r   ON r.REL_ID   = b.REL_ID
    LEFT JOIN raw_releve     rp  ON rp.REL_ID  = b.rel_id_p
    LEFT JOIN ref_comptage   cp  ON cp.ID_COMP = r.ID_COMP
    ORDER BY ecart ASC NULLS LAST
    """
    params = [str_id, sect_id, annee, mois, str_id, sect_id, pa, pm]
    rows = db.lignes(sql, params)
    # Seules les factures RELEVÉES intéressent la comparaison : les
    # abonnés facturés en estimé ou au forfait ne sont pas affichés.
    rows = [r for r in rows if r.get("gravite") != "ESTIMEE"]

    entete = db.ligne(f"""
        SELECT * FROM ({_sql_secteurs()}) t
        WHERE centre_code = ? AND secteur_code = ?
    """, [annee, mois, pa, pm, annee, mois, pa, pm, str_id, sect_id])
    if entete:
        entete["zone_lib"] = _zone(entete.get("zone_id"))

    perdu = sum(-(r["ecart"] or 0) for r in rows if (r["ecart"] or 0) < 0)
    return {
        "entete": entete,
        "lignes": rows,
        "cycle": cycles.libelle(annee, mois),
        "cycle_precedent": cycles.libelle(pa, pm),
        "comparaison": _lib_comparaison(annee, mois, pa, pm),
        "resume": {
            "nb_references": len(rows),
            "nb_en_baisse": sum(1 for r in rows if (r["ecart"] or 0) < 0),
            "nb_disparues": sum(1 for r in rows if r["gravite"] == "DISPARU"),
            "nb_volume_nul": sum(
                1 for r in rows if r["gravite"] == "VOLUME_NUL"
            ),
            "nb_nouvelles": sum(1 for r in rows if r["gravite"] == "NOUVEAU"),
            "volume_perdu": round(perdu, 1),
        },
    }


# ============================================================
# 6. Listes pour les filtres
# ============================================================

def centres_list(annee: int, mois: int) -> list[dict]:
    """Centres présents sur le cycle, pour le filtre."""
    rows = db.lignes(f"""
        WITH cs AS ({_CS_DEDUP})
        SELECT cs.STR_ID AS centre_code,
               COALESCE(ce.CENTRE_LIB,
                        '(centre ' || CAST(cs.STR_ID AS VARCHAR) || ')')
                                            AS centre_libelle,
               ce.ZONE_ID                   AS zone_id,
               COUNT(*)                     AS nb_secteurs
        FROM cs
        LEFT JOIN ref_centre ce ON ce.CENTRE_ID = cs.STR_ID
        WHERE cs.ANNEE = ? AND cs.MOIS = ?
        GROUP BY cs.STR_ID, ce.CENTRE_LIB, ce.ZONE_ID
        ORDER BY centre_libelle
    """, [annee, mois])
    for r in rows:
        r["zone_lib"] = _zone(r.get("zone_id"))
    return rows


def secteurs_list(annee: int, mois: int,
                  str_id: int | None = None) -> list[dict]:
    """Secteurs du cycle, éventuellement filtrés sur un centre."""
    cond, params = "", [annee, mois]
    if str_id is not None:
        cond = "AND cs.STR_ID = ?"
        params.append(str_id)

    return db.lignes(f"""
        WITH cs AS ({_CS_DEDUP})
        SELECT cs.SECT_ID  AS secteur_code,
               cs.STR_ID   AS centre_code,
               COALESCE(NULLIF(TRIM(sec.SECT_CODE), ''),
                        CAST(cs.SECT_ID AS VARCHAR))     AS secteur_num,
               COALESCE(sec.SECT_LIB,
                        '(secteur ' || CAST(cs.SECT_ID AS VARCHAR) || ')')
                                                         AS secteur_libelle,
               COALESCE(ce.CENTRE_LIB, '')               AS centre_libelle,
               cs.NBABO                                  AS parc,
               cs.POURCTRAITER                           AS pct_saisie,
               cs.CLOTURE                                AS cloture
        FROM cs
        LEFT JOIN ref_secteur sec ON sec.SECT_ID  = cs.SECT_ID
        LEFT JOIN ref_centre  ce  ON ce.CENTRE_ID = cs.STR_ID
        WHERE cs.ANNEE = ? AND cs.MOIS = ? {cond}
        ORDER BY centre_libelle, secteur_libelle
    """, params)


# ============================================================
# 7. Avancement de la saisie — du premier relevé à la clôture
# ============================================================

# Activité de relève du cycle, reconstituée depuis S_RELEVE.
# COUNT(DISTINCT CPT_REF) et non COUNT(*) : un compteur relevé deux fois
# dans le cycle ne doit pas compter double, sinon le pourcentage dépasse
# 100 %.
_RELEVE_STATS = """
    SELECT STR_ID, SECT_ID,
           COUNT(DISTINCT CPT_REF)                  AS saisis_reel,
           COUNT(*)                                 AS nb_releves,
           MIN(REL_DATE)                            AS premier_releve,
           MAX(REL_DATE)                            AS dernier_releve,
           COUNT(DISTINCT CAST(REL_DATE AS DATE))   AS nb_jours_actifs,
           COUNT(DISTINCT CPT_REF) FILTER (WHERE COALESCE(REL_VALIDE, 0) = 1)
                                                    AS saisis_valides,
           COUNT(DISTINCT CPT_REF) FILTER (WHERE COALESCE(REL_ESTIMATIF, 0) = 1)
                                                    AS saisis_estimes,
           MODE(MATRICULE)                          AS MATRICULE_RELEVEUR
    FROM raw_releve
    WHERE REL_ANNEE = ? AND REL_MOIS = ?
    GROUP BY STR_ID, SECT_ID
"""


def avancement(annee: int, mois: int, str_id: int | None = None,
               sect_id: int | None = None,
               statut: str | None = None) -> list[dict]:
    """Avancement de la saisie, secteur par secteur, sur un cycle.

    Visible dès le premier relevé saisi et jusqu'à la clôture. Deux
    pourcentages sont renvoyés :

      pct_declare  POURCTRAITER de CLOTURE_SECTEUR — ce que le système
                   de facturation affiche.
      pct_reel     calculé depuis S_RELEVE sur le parc du secteur — ce
                   que le terrain a réellement saisi.

    Un écart entre les deux signale un décalage de rafraîchissement, ou
    des relevés saisis hors du compteur déclaré.
    """
    pa, pm = cycles.cycle_precedent(annee, mois)
    params: list = [
        annee, mois,          # _RELEVE_STATS
        annee, mois,          # cs du cycle N
        annee, mois,          # _FACT_PAR_SECTEUR -> fn
        pa, pm,               # p (cycle N-2, pour le volume de référence)
    ]

    conds = []
    if str_id is not None:
        conds.append("centre_code = ?")
        params.append(str_id)
    if sect_id is not None:
        conds.append("secteur_code = ?")
        params.append(sect_id)
    if statut:
        conds.append("etat = ?")
        params.append(statut)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    rows = db.lignes(f"""
        WITH cs AS ({_CS_DEDUP}),
             rs AS ({_RELEVE_STATS}),
             fa AS ({_FACT_PAR_SECTEUR}),
             n  AS (SELECT * FROM cs WHERE ANNEE = ? AND MOIS = ?),
             fn AS (SELECT * FROM fa WHERE ANNEE = ? AND MOIS = ?),
             p  AS (SELECT * FROM cs WHERE ANNEE = ? AND MOIS = ?)
        SELECT
            n.STR_ID                                      AS centre_code,
            COALESCE(ce.CENTRE_LIB,
                     '(centre ' || CAST(n.STR_ID AS VARCHAR) || ')')
                                                          AS centre_libelle,
            ce.ZONE_ID                                    AS zone_id,
            n.SECT_ID                                     AS secteur_code,
            COALESCE(NULLIF(TRIM(sec.SECT_CODE), ''),
                     CAST(n.SECT_ID AS VARCHAR))          AS secteur_num,
            COALESCE(sec.SECT_LIB,
                     '(secteur ' || CAST(n.SECT_ID AS VARCHAR) || ')')
                                                          AS secteur_libelle,
            -- Même règle que l'onglet Retards : le releveur qui a
            -- réellement saisi le cycle, avec repli sur celui affecté
            -- au secteur.
            COALESCE({_mat("rs.MATRICULE_RELEVEUR")},
                     {_mat("sec.MAT_RELEV")})             AS matricule_releveur,
            n.NBABO                                       AS parc,
            n.NBTRAITER                                   AS saisis_declare,
            n.NBNONTRAITER                                AS restant,
            n.POURCTRAITER                                AS pct_declare,
            COALESCE(rs.saisis_reel, 0)                   AS saisis_reel,
            COALESCE(rs.saisis_valides, 0)                AS saisis_valides,
            COALESCE(rs.saisis_estimes, 0)                AS saisis_estimes,
            CASE WHEN COALESCE(n.NBABO, 0) > 0
                 THEN ROUND(100.0 * COALESCE(rs.saisis_reel, 0)
                            / n.NBABO, 1)
            END                                           AS pct_reel,
            rs.premier_releve, rs.dernier_releve,
            COALESCE(rs.nb_jours_actifs, 0)               AS nb_jours_actifs,
            n.CLOTURE                                     AS cloture,
            n.DATE_CLOT                                   AS date_cloture,
            COALESCE(fn.nb_factures, 0)                   AS nb_factures,
            -- vol_n, vol_p, ecart, ecart_pct : ajoutés après la requête,
            -- avec le calcul de l'onglet Baisses (VOLUMEFACT n'est plus
            -- utilisé pour la comparaison).
            -- Étapes du cycle de vie du secteur, dans l'ordre réel.
            CASE
                -- Même règle que les onglets Baisses et Retards : quelques
                -- factures isolées sur un secteur non clôturé ne font pas
                -- un secteur facturé.
                WHEN COALESCE(fn.nb_factures, 0) > 0
                     AND n.CLOTURE = 1                         THEN 'FACTURE'
                WHEN n.CLOTURE = 1                            THEN 'CLOTURE'
                WHEN COALESCE(n.POURCTRAITER, 0) >= 100        THEN 'SAISIE_FINIE'
                WHEN COALESCE(n.NBTRAITER, 0) > 0
                  OR COALESCE(rs.saisis_reel, 0) > 0          THEN 'EN_COURS'
                ELSE 'NON_COMMENCE'
            END                                           AS etat
        FROM n
        LEFT JOIN rs ON rs.STR_ID = n.STR_ID AND rs.SECT_ID = n.SECT_ID
        LEFT JOIN fn ON fn.STR_ID = n.STR_ID AND fn.SECT_ID = n.SECT_ID
        LEFT JOIN p  ON p.STR_ID  = n.STR_ID AND p.SECT_ID  = n.SECT_ID
        LEFT JOIN ref_centre  ce  ON ce.CENTRE_ID = n.STR_ID
        LEFT JOIN ref_secteur sec ON sec.SECT_ID  = n.SECT_ID
        {where}
        ORDER BY pct_declare ASC NULLS FIRST, centre_libelle, secteur_libelle
    """, params)

    # Écart identique à celui de l'onglet Baisses : factures RELEVÉES,
    # à abonnés constants. Un secteur pas encore facturé a donc 0 / 0.
    # Les gros consommateurs sont comparés à N-1.
    def _vols(a2: int, m2: int) -> dict:
        return {
            (v["centre_code"], v["secteur_code"]): v
            for v in db.lignes(f"""
                SELECT centre_code, secteur_code, vol_n, vol_p, ecart, ecart_pct
                FROM ({_sql_secteurs()}) t
            """, [annee, mois, a2, m2, annee, mois, a2, m2])
        }
    mens = secteurs_mensuels(annee, mois)
    vols = _vols(pa, pm)
    vols1 = _vols(*cycles.mois_precedent(annee, mois)) if mens else {}
    for r in rows:
        r["zone_lib"] = _zone(r.get("zone_id"))
        gros = r["secteur_code"] in mens
        r["comparaison"] = "N-1" if gros else "N-2"
        v = (vols1 if gros else vols).get(
            (r["centre_code"], r["secteur_code"]), {})
        for k in ("vol_n", "vol_p", "ecart", "ecart_pct"):
            r[k] = v.get(k)
    return rows


def avancement_jours(annee: int, mois: int, str_id: int,
                     sect_id: int) -> dict:
    """Progression quotidienne de la saisie pour un secteur.

    Permet de suivre la relève jour par jour depuis le premier compteur
    saisi : combien de compteurs relevés chaque jour, et le cumul.
    """
    jours = db.lignes("""
        SELECT CAST(REL_DATE AS DATE)   AS jour,
               COUNT(DISTINCT CPT_REF)  AS nb
        FROM raw_releve
        WHERE STR_ID = ? AND SECT_ID = ?
          AND REL_ANNEE = ? AND REL_MOIS = ?
          AND REL_DATE IS NOT NULL
        GROUP BY CAST(REL_DATE AS DATE)
        ORDER BY jour
    """, [str_id, sect_id, annee, mois])

    entete = db.ligne(f"""
        SELECT * FROM ({_sql_secteurs()}) t
        WHERE centre_code = ? AND secteur_code = ?
    """, [annee, mois, *precedent_secteur(annee, mois, sect_id),
          annee, mois, *precedent_secteur(annee, mois, sect_id),
          str_id, sect_id])

    parc = (entete or {}).get("parc_n") or 0
    cumul = 0
    for j in jours:
        cumul += int(j["nb"] or 0)
        j["cumul"] = cumul
        j["pct_cumul"] = round(100.0 * cumul / parc, 1) if parc else None

    return {
        "jours": jours,
        "parc": parc,
        "total": cumul,
        "cycle": cycles.libelle(annee, mois),
        "entete": entete,
    }


# ============================================================
# 8. Détail par abonné — assis sur le PARC, pas sur les factures
# ============================================================

def abonnes(annee: int, mois: int, str_id: int, sect_id: int) -> dict:
    """Ligne par abonné d'un secteur, sur les deux cycles comparés.

    Point important : la liste part du PARC du secteur
    (`raw_abonnement`), pas des factures. C'est ce qui permet de s'en
    servir pendant la saisie, avant toute facturation : on voit qui a
    été relevé, qui ne l'est pas encore, puis qui a été facturé.

    `statut_saisie` suit cette progression :
        NON_RELEVE  dans le parc, aucun relevé sur ce cycle
        RELEVE      relevé saisi, pas encore facturé
        FACTURE     facture émise
        HORS_PARC   relevé ou facturé mais absent du parc du secteur
                    (abonné déplacé, ou résilié depuis)
    """
    pa, pm = precedent_secteur(annee, mois, sect_id)

    sql = f"""
    WITH
    parc AS (
        SELECT ABN_ID, ABN_ADRESSE, COMP_ADRESSE, QUARTIER, NLOT,
               ANCIENNE_REFRENCE
        FROM raw_abonnement
        WHERE STR_ID = ? AND SECT_ID = ?
          AND COALESCE(ABN_RES, 0) = 0
    ),
    rn AS (
        SELECT ABN_ID, REL_ID, CPT_REF, REL_DATE,
               REL_INDEX, REL_ANCIEN_INDEX, REL_CONSOM_CALCUL,
               ID_COMP, COALESCE(REL_VALIDE, 0)    AS REL_VALIDE,
               COALESCE(REL_ESTIMATIF, 0)          AS REL_ESTIMATIF,
               MATRICULE                           AS MAT_RELEVEUR
        FROM raw_releve
        WHERE STR_ID = ? AND SECT_ID = ?
          AND REL_ANNEE = ? AND REL_MOIS = ?
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ABN_ID
                                   ORDER BY REL_DATE DESC NULLS LAST,
                                            REL_ID DESC) = 1
    ),
    rp AS (
        SELECT ABN_ID, REL_INDEX AS REL_INDEX_P, ID_COMP AS ID_COMP_P
        FROM raw_releve
        WHERE STR_ID = ? AND SECT_ID = ?
          AND REL_ANNEE = ? AND REL_MOIS = ?
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ABN_ID
                                   ORDER BY REL_DATE DESC NULLS LAST,
                                            REL_ID DESC) = 1
    ),
    fn AS (
        SELECT FACT_ID, ABN_ID, FACT_NUM, FACT_DATE, TYPE_FACT,
               CASE WHEN TYPE_FACT IN ({_TYPES_RELEVEE})
                    THEN COALESCE(FAT_QTE_CONS, 0) ELSE 0 END AS vol,
               FACT_MNT
        FROM {_FM} AS fm_r
        WHERE STR_ID = ? AND SECT_ID = ?
          AND FACT_ANNEE = ? AND FACT_PERIODE = ?
          AND COALESCE(FLAGANNULLE, 0) = 0
          AND TYPE_FACT IN ({_TYPES_CONSO})
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ABN_ID
                                   ORDER BY FACT_ID DESC) = 1
    ),
    fp AS (
        SELECT ABN_ID,
               CASE WHEN TYPE_FACT IN ({_TYPES_RELEVEE})
                    THEN COALESCE(FAT_QTE_CONS, 0) ELSE 0 END AS vol
        FROM {_FM} AS fm_r
        WHERE STR_ID = ? AND SECT_ID = ?
          AND FACT_ANNEE = ? AND FACT_PERIODE = ?
          AND COALESCE(FLAGANNULLE, 0) = 0
          AND TYPE_FACT IN ({_TYPES_CONSO})
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ABN_ID
                                   ORDER BY FACT_ID DESC) = 1
    ),
    -- Volumes alignés sur le niveau secteur : somme des factures
    -- relevées du cycle, et référence = médiane des 3 dernières.
    sn AS (
        SELECT ABN_ID, SUM(COALESCE(FAT_QTE_CONS, 0)) AS v
        FROM {_FM} AS fm_r
        WHERE STR_ID = ? AND SECT_ID = ?
          AND FACT_ANNEE = ? AND FACT_PERIODE = ?
          AND COALESCE(FLAGANNULLE, 0) = 0
          AND TYPE_FACT IN ({_TYPES_RELEVEE})
        GROUP BY ABN_ID
    ),
    hist AS (
        SELECT ABN_ID, FACT_ANNEE * 12 + FACT_PERIODE AS mi,
               SUM(COALESCE(FAT_QTE_CONS, 0)) AS v
        FROM {_FM} AS fm_r
        WHERE STR_ID = ? AND SECT_ID = ?
          AND COALESCE(FLAGANNULLE, 0) = 0
          AND TYPE_FACT IN ({_TYPES_RELEVEE})
          AND FACT_ANNEE * 12 + FACT_PERIODE BETWEEN ? AND ?
        GROUP BY ABN_ID, FACT_ANNEE, FACT_PERIODE
    ),
    hp AS (SELECT ABN_ID, v FROM hist WHERE mi = ?),
    cpt AS (
        SELECT ABN_ID, CPT_SERIE, CPI_REF
        FROM raw_compteur
        -- perf : seulement les abonnés du secteur (au lieu de tout le pays)
        WHERE ABN_ID IN (SELECT ABN_ID FROM parc UNION SELECT ABN_ID FROM rn
                         UNION SELECT ABN_ID FROM rp UNION SELECT ABN_ID FROM fn
                         UNION SELECT ABN_ID FROM fp)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY ABN_ID
            ORDER BY CPI_DATE_INS DESC NULLS LAST, ID DESC
        ) = 1
    ),
    ids AS (
        SELECT ABN_ID FROM parc
        UNION SELECT ABN_ID FROM rn
        UNION SELECT ABN_ID FROM rp
        UNION SELECT ABN_ID FROM fn
        UNION SELECT ABN_ID FROM fp
    ),
    base AS (
        SELECT
            i.ABN_ID                                     AS abn_id,
            (p.ABN_ID IS NOT NULL)                       AS dans_parc,
            COALESCE(sn.v, 0)                            AS vol_n,
            CASE WHEN hp.ABN_ID IS NOT NULL
                 THEN hp.v
                 ELSE 0 END                              AS vol_p,
            COALESCE(hp.v, 0)                            AS vol_p_cycle,
            fn.FACT_NUM, fn.FACT_DATE, fn.TYPE_FACT, fn.FACT_MNT,
            (fn.ABN_ID IS NOT NULL)                      AS a_facture,
            (rn.ABN_ID IS NOT NULL)                      AS a_releve,
            rn.CPT_REF, rn.REL_DATE, rn.REL_INDEX, rn.REL_ANCIEN_INDEX,
            rn.REL_CONSOM_CALCUL, rn.ID_COMP, rn.MAT_RELEVEUR,
            rn.REL_VALIDE, rn.REL_ESTIMATIF,
            rp.REL_INDEX_P, rp.ID_COMP_P,
            p.ABN_ADRESSE, p.COMP_ADRESSE, p.QUARTIER, p.NLOT,
            p.ANCIENNE_REFRENCE
        FROM ids i
        LEFT JOIN parc p  ON p.ABN_ID  = i.ABN_ID
        LEFT JOIN rn      ON rn.ABN_ID = i.ABN_ID
        LEFT JOIN rp      ON rp.ABN_ID = i.ABN_ID
        LEFT JOIN fn      ON fn.ABN_ID = i.ABN_ID
        LEFT JOIN fp      ON fp.ABN_ID = i.ABN_ID
        LEFT JOIN sn      ON sn.ABN_ID = i.ABN_ID
        LEFT JOIN hp      ON hp.ABN_ID = i.ABN_ID
    )
    SELECT
        b.abn_id                                      AS reference,
        b.ANCIENNE_REFRENCE                           AS ancienne_reference,
        COALESCE(NULLIF(TRIM(b.ABN_ADRESSE), ''), '') AS adresse,
        COALESCE(NULLIF(TRIM(b.COMP_ADRESSE), ''), '') AS complement_adresse,
        b.QUARTIER                                    AS quartier,
        cpt.CPT_SERIE                                 AS num_compteur,
        cpt.CPI_REF                                   AS ref_compteur,
        b.CPT_REF                                     AS ref_releve,
        b.REL_DATE                                    AS date_releve,
        {_mat("b.MAT_RELEVEUR")}           AS matricule_releveur,
        b.REL_ANCIEN_INDEX                            AS index_debut,
        b.REL_INDEX                                   AS index_fin,
        b.REL_INDEX_P                                 AS index_fin_p,
        b.REL_CONSOM_CALCUL                           AS conso_relevee,
        b.ID_COMP                                     AS id_comp,
        COALESCE(NULLIF(TRIM(cp.LIBELLE), ''),
                 CASE b.ID_COMP
                     WHEN 1 THEN 'COMPTEUR ACCESSIBLE'  WHEN 2 THEN 'COMPTEUR ILLISIBLE'
                     WHEN 3 THEN 'COMPTEUR DEFECTUEUX'  WHEN 4 THEN 'COMPTEUR BLOQUE'
                     WHEN 5 THEN 'COMPTEUR INACCESSIBLE' WHEN 6 THEN 'COMPTEUR VOLE'
                 END)                         AS etat_compteur,
        b.REL_VALIDE                                  AS releve_valide,
        b.REL_ESTIMATIF                               AS releve_estimatif,
        b.FACT_NUM                                    AS num_facture,
        b.FACT_DATE                                   AS date_facture,
        b.FACT_MNT                                    AS montant,
        CASE b.TYPE_FACT
            WHEN 0 THEN 'Relevée'
            WHEN 1 THEN 'Estimée'
            WHEN 2 THEN 'Forfaitaire'
            WHEN 3 THEN 'Arrêt du compte'
            WHEN 4 THEN 'Mutation'
            WHEN 5 THEN 'Débit intercalaire'
            WHEN 6 THEN 'Pénalité'
            WHEN 7 THEN 'Redressement'
            WHEN 8 THEN 'Nouvel abonnement'
            WHEN 9 THEN 'Réabonnement'
            WHEN 10 THEN 'Estimé migré'
            WHEN 11 THEN 'Migrée'
            WHEN NULL THEN NULL
            ELSE 'Autre (' || CAST(b.TYPE_FACT AS VARCHAR) || ')'
        END                                           AS type_facture,
        ROUND(b.vol_n, 1)                             AS vol_n,
        ROUND(b.vol_p, 1)                             AS vol_p,
        ROUND(b.vol_p_cycle, 1)                       AS vol_p_cycle,
        ROUND(b.vol_n - b.vol_p, 1)                   AS ecart,
        CASE WHEN b.vol_p > 0
             THEN ROUND(100.0 * (b.vol_n - b.vol_p) / b.vol_p, 1)
        END                                           AS ecart_pct,
        -- Où en est cet abonné dans le cycle de relève.
        CASE
            -- Un abonné facturé est « Facturé », même s'il n'est plus
            -- dans le parc actuel du secteur : il compte dans le volume.
            WHEN b.a_facture                          THEN 'FACTURE'
            WHEN NOT b.dans_parc                      THEN 'HORS_PARC'
            WHEN b.a_releve                           THEN 'RELEVE'
            ELSE 'NON_RELEVE'
        END                                           AS statut_saisie,
        -- Gravité de l'écart de volume. Un abonné pas encore relevé
        -- n'est pas une baisse : c'est une saisie en attente.
        CASE
            WHEN b.dans_parc AND NOT b.a_releve AND NOT b.a_facture
                THEN 'NON_RELEVE'
            -- Relevé saisi mais facture pas encore émise : le volume N
            -- est à 0 par construction. Ce n'est pas une baisse, c'est
            -- une facturation en attente.
            WHEN b.a_releve AND NOT b.a_facture
                THEN 'EN_ATTENTE_FACT'
            -- Facturé en estimé ou au forfait : pas de volume relevé
            -- sur ce cycle, il ne compte pas dans la comparaison.
            WHEN b.vol_p = 0 AND b.vol_n = 0 AND b.FACT_NUM IS NOT NULL
                THEN 'VOLUME_NUL'
            WHEN b.vol_p = 0 AND b.vol_n = 0             THEN 'VIDE'
            WHEN b.FACT_NUM IS NULL AND b.vol_p > 0      THEN 'DISPARU'
            WHEN b.vol_p = 0                             THEN 'NOUVEAU'
            WHEN b.vol_n = 0                             THEN 'VOLUME_NUL'
            WHEN b.vol_n - b.vol_p > 0                   THEN 'HAUSSE'
            WHEN b.vol_n - b.vol_p = 0                   THEN 'STABLE'
            WHEN 100.0 * (b.vol_n - b.vol_p) / b.vol_p
                    <= -{config.GRAVITE_FORTE_PCT}       THEN 'FORTE'
            WHEN 100.0 * (b.vol_n - b.vol_p) / b.vol_p
                    <= -{config.GRAVITE_MOYENNE_PCT}     THEN 'MOYENNE'
            ELSE 'LEGERE'
        END                                           AS gravite
    FROM base b
    LEFT JOIN cpt              ON cpt.ABN_ID = b.abn_id
    LEFT JOIN ref_comptage cp  ON cp.ID_COMP = b.ID_COMP
    ORDER BY ecart ASC NULLS LAST, reference
    """

    params = [
        str_id, sect_id,                    # parc
        str_id, sect_id, annee, mois,       # rn
        str_id, sect_id, pa, pm,            # rp
        str_id, sect_id, annee, mois,       # fn
        str_id, sect_id, pa, pm,            # fp
        str_id, sect_id, annee, mois,       # sn
        str_id, sect_id,                    # hist
        pa * 12 + pm - 12, pa * 12 + pm,
        pa * 12 + pm,                       # hp
    ]
    rows = db.lignes(sql, params)
    # Seules les factures RELEVÉES intéressent la comparaison : les
    # abonnés facturés en estimé ou au forfait ne sont pas affichés.
    rows = [r for r in rows if r.get("gravite") != "ESTIMEE"]
    # Seuls les abonnés FACTURÉS sur le cycle N sont affichés : c'est sur
    # eux que la baisse se juge. Les non relevés et les relevés en attente
    # de facture sont écartés.
    rows = [r for r in rows if r.get("statut_saisie") == "FACTURE"]

    entete = db.ligne(f"""
        SELECT * FROM ({_sql_secteurs()}) t
        WHERE centre_code = ? AND secteur_code = ?
    """, [annee, mois, pa, pm, annee, mois, pa, pm, str_id, sect_id])
    if entete:
        entete["zone_lib"] = _zone(entete.get("zone_id"))

    def _n(statut: str) -> int:
        return sum(1 for r in rows if r["statut_saisie"] == statut)

    # Un abonné pas encore relevé, ou relevé mais pas encore facturé, a un
    # volume N à 0 par construction. L'inclure dans « en baisse » et dans
    # le volume perdu produirait une fausse alerte en pleine saisie : ces
    # lignes sont donc comptées à part.
    attente = ("NON_RELEVE", "EN_ATTENTE_FACT")
    juges = [r for r in rows if r["gravite"] not in attente]
    perdu = sum(-(r["ecart"] or 0) for r in juges if (r["ecart"] or 0) < 0)
    a_venir = sum(
        (r["vol_p"] or 0) for r in rows if r["gravite"] in attente
    )

    # Part de chaque abonné dans la baisse du secteur, et nombre
    # d'abonnés qui en portent 80 % : les premiers à aller voir.
    nb_80 = 0
    cumul = 0.0
    for r in sorted(juges, key=lambda x: x["ecart"] or 0):
        e = r["ecart"] or 0
        if e >= 0 or perdu <= 0:
            break
        if cumul < 0.8 * perdu:
            nb_80 += 1
        cumul += -e
    for r in rows:
        e = r["ecart"] or 0
        r["part_baisse"] = (round(100.0 * -e / perdu, 1)
                            if e < 0 and perdu > 0 and r in juges else None)

    return {
        "entete": entete,
        "lignes": rows,
        "cycle": cycles.libelle(annee, mois),
        "cycle_precedent": cycles.libelle(pa, pm),
        "comparaison": _lib_comparaison(annee, mois, pa, pm),
        "resume": {
            "nb_abonnes": len(rows),
            "nb_non_releves": _n("NON_RELEVE"),
            "nb_releves": _n("RELEVE"),
            "nb_factures": _n("FACTURE"),
            "nb_hors_parc": _n("HORS_PARC"),
            "nb_en_attente": sum(
                1 for r in rows if r["gravite"] in attente
            ),
            # Volume que ces abonnés avaient facturé au cycle N-2 : c'est
            # l'ordre de grandeur qui reste à produire avant de conclure.
            "volume_en_attente": round(a_venir, 1),
            "nb_en_baisse": sum(1 for r in juges if (r["ecart"] or 0) < 0),
            "nb_disparues": sum(1 for r in juges if r["gravite"] == "DISPARU"),
            "nb_volume_nul": sum(
                1 for r in juges if r["gravite"] == "VOLUME_NUL"
            ),
            "nb_nouvelles": sum(1 for r in juges if r["gravite"] == "NOUVEAU"),
            "volume_perdu": round(perdu, 1),
            "nb_80pct": nb_80,
            "nb_perdus": int((entete or {}).get("nb_perdus") or 0),
            "vol_perdus": round(float((entete or {}).get("vol_perdus") or 0), 1),
        },
    }


# ============================================================
# 9. Retard de facturation — la seconde alerte
# ============================================================
# La planification Oracle donne, pour chaque secteur et chaque cycle, une
# date prévue. Un secteur qui dépasse cette date sans être facturé est en
# retard : c'est l'alerte jaune, distincte de l'alerte rouge de baisse.
#
# PLANIF_RELEVE porte le lien secteur ↔ planning (un PLAN_REL_ID par
# secteur et par année), PLANIFDETAILLE porte les dates par cycle. Les
# deux sont agrégées avant jointure : plusieurs lignes peuvent exister
# pour une même clé, on garde la plus récente.

_PLANNING = """
    SELECT STR_ID, SECT_ID, ANNEE, MAX(PLAN_REL_ID) AS PLAN_REL_ID
    FROM raw_planif_releve
    GROUP BY STR_ID, SECT_ID, ANNEE
"""

_PLANNING_DATES = """
    SELECT PLAN_REL_ID, ANNEE, MOIS,
           MAX(DATEDEBUTSEMAINE) AS DATEDEBUTSEMAINE,
           MAX(DATEDEBUTFACT)    AS DATEDEBUTFACT
    FROM raw_planif_detaille
    GROUP BY PLAN_REL_ID, ANNEE, MOIS
"""

# Date d'émission de la première facture du cycle : sert à savoir si la
# facturation a eu lieu avant ou après la date prévue.
_PREMIERE_FACTURE = f"""
    SELECT STR_ID, SECT_ID,
           FACT_ANNEE AS ANNEE, FACT_PERIODE AS MOIS,
           MIN(FACT_DATE) AS premiere_facture
    FROM raw_facture_m
    WHERE COALESCE(FLAGANNULLE, 0) = 0
      AND TYPE_FACT IN ({_TYPES_CONSO})
      AND STR_ID NOT IN ({_EXCLU})
    GROUP BY STR_ID, SECT_ID, FACT_ANNEE, FACT_PERIODE
"""


# Marge de l'adjoint selon la taille du secteur — grille du dashboard.
_MARGE = f"""
    CASE
        WHEN COALESCE(n.NBABO, 0) < 100   THEN {config.DEADLINE_MARGE_SMALL}
        WHEN COALESCE(n.NBABO, 0) <= 300  THEN {config.DEADLINE_MARGE_MEDIUM}
        WHEN COALESCE(n.NBABO, 0) <= 1000 THEN {config.DEADLINE_MARGE_LARGE}
        ELSE {config.DEADLINE_MARGE_XLARGE}
    END
"""

# Releveur ayant réellement saisi le secteur sur le cycle. MODE() prend
# le matricule le plus fréquent, comme le fait le dashboard.
_RELEVEUR = """
    SELECT STR_ID, SECT_ID, REL_ANNEE, REL_MOIS,
           MODE(MATRICULE) AS MATRICULE_RELEVEUR
    FROM raw_releve
    GROUP BY STR_ID, SECT_ID, REL_ANNEE, REL_MOIS
"""


def _sql_retards() -> str:
    col = config.COLONNE_DEADLINE          # DATEDEBUTSEMAINE | DATEDEBUTFACT
    return f"""
    WITH cs AS ({_CS_DEDUP}),
         fa AS ({_FACT_PAR_SECTEUR}),
         pl AS ({_PLANNING}),
         pdt AS ({_PLANNING_DATES}),
         pf AS ({_PREMIERE_FACTURE}),
         rv AS ({_RELEVEUR}),
         n  AS (SELECT * FROM cs WHERE ANNEE = ? AND MOIS = ?),
         fn AS (SELECT * FROM fa WHERE ANNEE = ? AND MOIS = ?),
         pfn AS (SELECT * FROM pf WHERE ANNEE = ? AND MOIS = ?),
    base AS (
        SELECT
            n.STR_ID, n.SECT_ID, n.ANNEE, n.MOIS,
            n.NBABO, n.NBTRAITER, n.NBNONTRAITER, n.POURCTRAITER,
            n.CLOTURE, n.DATE_CLOT,
            COALESCE(fn.nb_factures, 0)   AS nb_factures,
            pfn.premiere_facture,
            pdt.{col}                     AS deadline,
            pdt.DATEDEBUTSEMAINE,
            pdt.DATEDEBUTFACT,
            rv.MATRICULE_RELEVEUR,
            {_MARGE}                      AS marge_adjoint,
            pdt.{col} - INTERVAL ({_MARGE}) DAY
                                          AS deadline_releveur
        FROM n
        LEFT JOIN fn  ON fn.STR_ID = n.STR_ID AND fn.SECT_ID = n.SECT_ID
        LEFT JOIN pfn ON pfn.STR_ID = n.STR_ID AND pfn.SECT_ID = n.SECT_ID
        LEFT JOIN pl  ON pl.STR_ID = n.STR_ID AND pl.SECT_ID = n.SECT_ID
                     AND pl.ANNEE = n.ANNEE
        LEFT JOIN pdt ON pdt.PLAN_REL_ID = pl.PLAN_REL_ID
                     AND pdt.ANNEE = n.ANNEE AND pdt.MOIS = n.MOIS
        LEFT JOIN rv  ON rv.STR_ID = n.STR_ID AND rv.SECT_ID = n.SECT_ID
                     AND rv.REL_ANNEE = n.ANNEE AND rv.REL_MOIS = n.MOIS
    )
    SELECT
        b.STR_ID                                      AS centre_code,
        COALESCE(ce.CENTRE_LIB,
                 '(centre ' || CAST(b.STR_ID AS VARCHAR) || ')')
                                                      AS centre_libelle,
        ce.ZONE_ID                                    AS zone_id,
        b.SECT_ID                                     AS secteur_code,
        COALESCE(NULLIF(TRIM(sec.SECT_CODE), ''),
                 CAST(b.SECT_ID AS VARCHAR))          AS secteur_num,
        COALESCE(sec.SECT_LIB,
                 '(secteur ' || CAST(b.SECT_ID AS VARCHAR) || ')')
                                                      AS secteur_libelle,
        -- Releveur du cycle, avec repli sur celui affecté au secteur.
        COALESCE({_mat("b.MATRICULE_RELEVEUR")}, {_mat("sec.MAT_RELEV")})
                                                      AS matricule_releveur,
        b.ANNEE, b.MOIS,
        b.NBABO                                       AS parc,
        b.NBTRAITER                                   AS saisis,
        b.NBNONTRAITER                                AS restant,
        b.POURCTRAITER                                AS pct_saisie,
        b.CLOTURE                                     AS cloture,
        b.DATE_CLOT                                   AS date_cloture,
        b.nb_factures,
        b.premiere_facture,
        b.deadline,
        b.deadline_releveur,
        b.marge_adjoint,
        b.DATEDEBUTSEMAINE                            AS date_debut_semaine,
        b.DATEDEBUTFACT                               AS date_debut_fact,
        -- Jours restants avant la deadline du releveur. Négatif = dépassée.
        CASE WHEN b.deadline_releveur IS NOT NULL
             THEN CAST(EXTRACT(EPOCH FROM (b.deadline_releveur - now()))
                       / 86400.0 AS INTEGER)
        END                                           AS jours_restants,
        -- Positif = en retard de tant de jours. Négatif = il reste du temps.
        -- Compté en jours calendaires : le jour J vaut 0 (dépassé ce
        -- jour-là), le lendemain 1, la veille -1.
        CASE WHEN b.deadline IS NOT NULL
             THEN date_diff('day', CAST(b.deadline AS DATE), current_date)
        END                                           AS jours_retard,
        CASE
            -- Quelques factures isolées (résiliation, facture de
            -- passage…) peuvent exister sur un secteur non clôturé : il
            -- n'est facturé qu'une fois clôturé.
            WHEN COALESCE(b.nb_factures, 0) > 0
                 AND b.CLOTURE = 1                       THEN 'FACTURE'
            WHEN b.CLOTURE = 1                           THEN 'CLOTURE'
            WHEN COALESCE(b.POURCTRAITER, 0) >= 100      THEN 'SAISIE_FINIE'
            WHEN COALESCE(b.NBTRAITER, 0) > 0            THEN 'EN_COURS'
            ELSE 'NON_COMMENCE'
        END                                           AS etat,
        -- Le verdict de retard. Seul EN_RETARD lève une alerte.
        -- Ordre d'évaluation calqué sur v_deadline_secteur du dashboard,
        -- avec le verdict déplacé sur la facturation.
        --
        -- ⚠️ Un secteur CLÔTURÉ sort du périmètre du retard : il a fait
        -- son travail, la facturation est un traitement qui vient
        -- ensuite. Sans cette distinction, tous les secteurs clôturés
        -- mais pas encore facturés d'un cycle en cours remontaient en
        -- retard — c'est-à-dire presque tous.
        CASE
            -- Facturé = clôturé ET factures émises. Un secteur non
            -- clôturé avec quelques factures isolées reste à surveiller
            -- (cas réel : CARREFOUR2 secteur 62, cycle 11/2026, 0 % de
            -- saisie et 1 facture émise).
            WHEN COALESCE(b.nb_factures, 0) > 0 AND b.CLOTURE = 1
                 AND b.deadline IS NOT NULL
                 AND b.premiere_facture > b.deadline
                THEN 'FACTURE_EN_RETARD'
            WHEN COALESCE(b.nb_factures, 0) > 0 AND b.CLOTURE = 1
                                                         THEN 'FACTURE'
            WHEN b.deadline IS NULL                      THEN 'PAS_DE_PLANNING'
            -- Clôturé, pas encore facturé : à facturer, pas en retard de
            -- saisie. On distingue tout de même le dépassement de date.
            WHEN b.CLOTURE = 1
                 AND current_date >= CAST(b.deadline AS DATE)
                THEN 'A_FACTURER_EN_RETARD'
            WHEN b.CLOTURE = 1                           THEN 'A_FACTURER'
            -- Au-delà de ce seuil d'avancement, le secteur finira à
            -- temps : même exemption que le dashboard.
            WHEN COALESCE(b.POURCTRAITER, 0)
                 >= {int(config.SAISIE_AVANCEE_PCT)}     THEN 'A_TEMPS'
            -- Jour J de facturation passé et le secteur n'est toujours
            -- pas clôturé : il ne pourra pas être facturé. ALERTE.
            -- Le jour J lui-même compte comme dépassé.
            WHEN current_date >= CAST(b.deadline AS DATE) THEN 'EN_RETARD'
            -- Deadline proche : le secteur est entré dans la marge de
            -- l'adjoint (1 à 4 jours selon le parc), règle de l'onglet
            -- Retards du dashboard de suivi.
            WHEN b.deadline_releveur IS NOT NULL
                 AND now() > b.deadline_releveur         THEN 'DEADLINE_PROCHE'
            ELSE 'A_TEMPS'
        END                                           AS urgence
    FROM base b
    LEFT JOIN ref_centre  ce  ON ce.CENTRE_ID = b.STR_ID
    LEFT JOIN ref_secteur sec ON sec.SECT_ID  = b.SECT_ID
    """


def retards(annee: int, mois: int, str_id: int | None = None,
            sect_id: int | None = None,
            urgence: str | None = None,
            seulement_retards: bool = False) -> list[dict]:
    """Suivi des dates prévues de facturation, secteur par secteur."""
    params: list = [annee, mois, annee, mois, annee, mois]

    conds = []
    if str_id is not None:
        conds.append("centre_code = ?")
        params.append(str_id)
    if sect_id is not None:
        conds.append("secteur_code = ?")
        params.append(sect_id)
    if urgence:
        conds.append("urgence = ?")
        params.append(urgence)
    if seulement_retards:
        conds.append("urgence = 'EN_RETARD'")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    rows = db.lignes(f"""
        SELECT * FROM ({_sql_retards()}) t
        {where}
        ORDER BY jours_retard DESC NULLS LAST, centre_libelle, secteur_libelle
    """, params)

    for r in rows:
        r["zone_lib"] = _zone(r.get("zone_id"))
        r["colonne_deadline"] = config.COLONNE_DEADLINE
    return rows


def retards_synthese(annee: int, mois: int,
                     str_id: int | None = None) -> dict:
    """Chiffres de l'en-tête de l'onglet Retards."""
    params: list = [annee, mois, annee, mois, annee, mois]
    cond = ""
    if str_id is not None:
        cond = "WHERE centre_code = ?"
        params.append(str_id)

    row = db.ligne(f"""
        WITH t AS ({_sql_retards()})
        SELECT
            COUNT(*)                                          AS nb_secteurs,
            COUNT(*) FILTER (WHERE urgence = 'EN_RETARD')     AS nb_en_retard,
            COUNT(*) FILTER (WHERE urgence = 'DEADLINE_PROCHE')
                                                              AS nb_proches,
            COUNT(*) FILTER (WHERE urgence = 'FACTURE')        AS nb_factures,
            COUNT(*) FILTER (WHERE urgence = 'FACTURE_EN_RETARD')
                                                              AS nb_fact_retard,
            COUNT(*) FILTER (WHERE urgence = 'A_FACTURER')     AS nb_a_facturer,
            COUNT(*) FILTER (WHERE urgence = 'A_FACTURER_EN_RETARD')
                                                              AS nb_a_fact_retard,
            COUNT(*) FILTER (WHERE urgence = 'PAS_DE_PLANNING')
                                                              AS nb_sans_planning,
            COUNT(*) FILTER (WHERE urgence = 'A_TEMPS')        AS nb_a_temps,
            MAX(jours_retard) FILTER (WHERE urgence = 'EN_RETARD')
                                                              AS retard_max,
            CAST(COALESCE(SUM(parc) FILTER (WHERE urgence = 'EN_RETARD'), 0)
                 AS BIGINT)                                   AS parc_en_retard,
            COUNT(DISTINCT centre_code) FILTER (WHERE urgence = 'EN_RETARD')
                                                              AS nb_centres
        FROM t {cond}
    """, params)

    return {
        **row,
        "cycle": cycles.libelle(annee, mois),
        "colonne_deadline": config.COLONNE_DEADLINE,
        "marges": {
            "moins_100": config.DEADLINE_MARGE_SMALL,
            "100_300": config.DEADLINE_MARGE_MEDIUM,
            "301_1000": config.DEADLINE_MARGE_LARGE,
            "plus_1000": config.DEADLINE_MARGE_XLARGE,
        },
    }
