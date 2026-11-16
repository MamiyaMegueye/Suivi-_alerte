"""
Extraction Oracle — mêmes tables et mêmes colonnes que le dashboard.

Six requêtes, toutes filtrées sur le périmètre (zones configurées, hors
pseudo-structures) :

  CLOTURE_SECTEUR   volumes facturés par secteur et par cycle
  S_FACTURE_M       volume facturé par abonné
  S_RELEVE          index et état du comptage
  S_ABONNEMENT      adresse
  S_COMPTEUR_INS    numéro de compteur physique
  références        S_STRUCTURE, S_SECTEUR, COMTAGE

Les deux premières grosses tables (factures, relevés) sont bornées aux
cycles utiles : `cycle_min` = N-24 mois par défaut. Sans cette borne on
rapatrierait des millions de lignes inutiles.
"""

from __future__ import annotations

import pandas as pd

from src import config, oracle_client

_Z = config.zone_ids_sql
_X = config.exclude_sql


# ============================================================
# CLOTURE_SECTEUR — le cœur de l'application
# ============================================================

def cloture_secteur(cycle_min: int) -> pd.DataFrame:
    sql = f"""
        SELECT
            cs.IDCLOTURE, cs.STR_ID, cs.SECT_ID,
            cs.ANNEE, cs.MOIS,
            cs.NBABO, cs.NBTRAITER, cs.NBNONTRAITER,
            cs.POURCTRAITER, cs.POURCNONTRAITER,
            cs.VOLUMEFACT, cs.VOLUMESTIM,
            cs.CLOTURE, cs.FLAG_CALCUL, cs.FLAG_VALID,
            cs.MATRICULE, cs.DATE_CLOT
        FROM CRM_SNDE.CLOTURE_SECTEUR cs
        JOIN CRM_SNDE.S_STRUCTURE s ON s.STR_ID = cs.STR_ID
        WHERE s.ZONE_ID IN ({_Z()})
          AND cs.STR_ID NOT IN ({_X()})
          AND (cs.ANNEE * 100 + cs.MOIS) >= :cycle_min
        ORDER BY cs.ANNEE DESC, cs.MOIS DESC, cs.STR_ID
    """
    return oracle_client.requete(sql, {"cycle_min": cycle_min})


# ============================================================
# S_FACTURE_M — volume facturé par abonné
# ============================================================

def facture_m(cycle_min: int) -> pd.DataFrame:
    sql = f"""
        SELECT
            fm.FACT_ID, fm.FACT_NUM, fm.STR_ID, fm.SECT_ID,
            fm.ABN_ID, fm.CLT_ID, fm.REL_ID, fm.TOUR_ID,
            fm.FACT_DATE, fm.FACT_PERIODE, fm.FACT_ANNEE,
            fm.TYPE_FACT, fm.CODE_FACTURE,
            fm.FAT_QTE_CONS, fm.FACT_MNT, fm.FACT_MNT_TTC,
            fm.NBESTIM, fm.FLAGANNULLE, fm.FLAG_REMB
        FROM CRM_SNDE.S_FACTURE_M fm
        JOIN CRM_SNDE.S_STRUCTURE s ON s.STR_ID = fm.STR_ID
        WHERE s.ZONE_ID IN ({_Z()})
          AND fm.STR_ID NOT IN ({_X()})
          AND (fm.FACT_ANNEE * 100 + fm.FACT_PERIODE) >= :cycle_min
    """
    return oracle_client.requete(sql, {"cycle_min": cycle_min})


def facture_m_cycles(cles: list[tuple[int, int]]) -> pd.DataFrame:
    """Factures de quelques cycles précis seulement — appelé à chaque
    passage du watcher pour que l'alerte parte dès l'émission des
    factures. Prédicat ANNEE / PERIODE en égalité, plus léger pour Oracle
    qu'une borne calculée."""
    if not cles:
        return pd.DataFrame()
    conds, params = [], {}
    for i, (a, m) in enumerate(cles):
        conds.append(f"(fm.FACT_ANNEE = :a{i} AND fm.FACT_PERIODE = :m{i})")
        params[f"a{i}"], params[f"m{i}"] = int(a), int(m)
    sql = f"""
        SELECT
            fm.FACT_ID, fm.FACT_NUM, fm.STR_ID, fm.SECT_ID,
            fm.ABN_ID, fm.CLT_ID, fm.REL_ID, fm.TOUR_ID,
            fm.FACT_DATE, fm.FACT_PERIODE, fm.FACT_ANNEE,
            fm.TYPE_FACT, fm.CODE_FACTURE,
            fm.FAT_QTE_CONS, fm.FACT_MNT, fm.FACT_MNT_TTC,
            fm.NBESTIM, fm.FLAGANNULLE, fm.FLAG_REMB
        FROM CRM_SNDE.S_FACTURE_M fm
        JOIN CRM_SNDE.S_STRUCTURE s ON s.STR_ID = fm.STR_ID
        WHERE s.ZONE_ID IN ({_Z()})
          AND fm.STR_ID NOT IN ({_X()})
          AND ({" OR ".join(conds)})
    """
    return oracle_client.requete(sql, params)


# ============================================================
# S_RELEVE — index et état du comptage
# ============================================================

def releve(cycle_min: int) -> pd.DataFrame:
    # ⚠️ ID_COMPTAGE est aliasé ID_COMP : c'est le nom retenu partout
    # ailleurs (ref_comptage.ID_COMP).
    sql = f"""
        SELECT
            r.REL_ID, r.ABN_ID, r.CPT_REF, r.LOC_ID, r.TOUR_ID,
            r.STR_ID, r.SECT_ID,
            -- CAST explicite : MATRICULE est numérique dans Oracle et
            -- ressortirait en « 7804.0 » après le passage par pandas.
            TRIM(CAST(r.MATRICULE AS VARCHAR2(50))) AS MATRICULE,
            r.REL_DATE, r.REL_ANNEE, r.REL_MOIS,
            r.DATE_GEN, r.REL_DATE_ANC_INDEX,
            r.REL_INDEX, r.REL_ANCIEN_INDEX, r.REL_CONSOM_CALCUL,
            r.REL_MOYENNE_CONSOM, r.REL_NBR_JR,
            r.ID_COMPTAGE AS ID_COMP, r.TYP_REL_ID, r.REL_VALIDE,
            r.REL_ESTIMATIF, r.REL_FACTURABLE_FLAG,
            r.REL_MAT, r.REL_MESSAGE, r.REL_ORIGINE
        FROM CRM_SNDE.S_RELEVE r
        JOIN CRM_SNDE.S_STRUCTURE s ON s.STR_ID = r.STR_ID
        WHERE s.ZONE_ID IN ({_Z()})
          AND r.STR_ID NOT IN ({_X()})
          AND (r.REL_ANNEE * 100 + r.REL_MOIS) >= :cycle_min
    """
    return oracle_client.requete(sql, {"cycle_min": cycle_min})


# ============================================================
# S_ABONNEMENT — adresse
# ============================================================

def abonnement() -> pd.DataFrame:
    # ANCIENNE_REFRENCE : la faute est dans Oracle, on la reprend telle quelle.
    sql = f"""
        SELECT
            a.ABN_ID, a.STR_ID, a.SECT_ID, a.CLI_ID,
            a.ABN_RES,
            a.ABN_ADRESSE, a.COMP_ADRESSE,
            a.QUARTIER, a.NLOT,
            a.ANCIENNE_REFRENCE, a.TAR_ID, a.GRP_ID, a.CAL_ID
        FROM CRM_SNDE.S_ABONNEMENT a
        JOIN CRM_SNDE.S_STRUCTURE s ON s.STR_ID = a.STR_ID
        WHERE s.ZONE_ID IN ({_Z()})
          AND a.STR_ID NOT IN ({_X()})
    """
    return oracle_client.requete(sql)


# ============================================================
# S_COMPTEUR_INS — numéro gravé sur le compteur
# ============================================================

def compteur() -> pd.DataFrame:
    sql = f"""
        SELECT
            c.ID, c.ABN_ID,
            c.CPT_SERIE, c.CPI_REF,
            c.CPI_ACTIF, c.CPI_DATE_INS, c.CPI_DATE_DEPOSE
        FROM CRM_SNDE.S_COMPTEUR_INS c
        JOIN CRM_SNDE.S_ABONNEMENT a ON a.ABN_ID = c.ABN_ID
        JOIN CRM_SNDE.S_STRUCTURE  s ON s.STR_ID = a.STR_ID
        WHERE s.ZONE_ID IN ({_Z()})
          AND a.STR_ID NOT IN ({_X()})
          AND c.CPI_DATE_DEPOSE IS NULL
    """
    return oracle_client.requete(sql)


# ============================================================
# Planification de la relève et de la facturation
# ============================================================

def planif_releve() -> pd.DataFrame:
    """Lien secteur ↔ planning. Petite table, chargée en entier."""
    sql = f"""
        SELECT pr.PLAN_REL_ID, pr.SECT_ID, pr.STR_ID,
               pr.SEMAINEDEBT, pr.ANNEE
        FROM CRM_SNDE.PLANIF_RELEVE pr
        WHERE pr.STR_ID IS NOT NULL
          AND pr.STR_ID NOT IN ({_X()})
    """
    return oracle_client.requete(sql)


def planif_detaille(annee_min: int) -> pd.DataFrame:
    """Dates prévues par cycle.

    DATEDEBUTSEMAINE et DATEDEBUTFACT sont toutes deux remontées : la
    première est la deadline retenue par défaut (voir
    config.COLONNE_DEADLINE), la seconde est affichée à côté pour
    pouvoir vérifier laquelle correspond vraiment à la facturation.
    """
    sql = """
        SELECT pd.IDDET, pd.NUMSEMAINE, pd.PLAN_REL_ID, pd.TYPE,
               pd.DATEDEBUTSEMAINE, pd.DATEDEBUTFACT,
               pd.MOIS, pd.ANNEE
        FROM CRM_SNDE.PLANIFDETAILLE pd
        WHERE pd.ANNEE >= :annee_min
    """
    return oracle_client.requete(sql, {"annee_min": annee_min})


# ============================================================
# Tables de référence
# ============================================================

def ref_centre() -> pd.DataFrame:
    sql = f"""
        SELECT STR_ID AS CENTRE_ID, STR_LIB_LT AS CENTRE_LIB, ZONE_ID
        FROM CRM_SNDE.S_STRUCTURE
        WHERE ZONE_ID IN ({_Z()})
    """
    return oracle_client.requete(sql)


def ref_secteur() -> pd.DataFrame:
    # SECT_CODE est le numéro métier du secteur, celui que les agents
    # utilisent. Le CAST puis le TRIM sont nécessaires : la colonne est
    # numérique dans Oracle et ressortirait avec des espaces.
    sql = """
        SELECT SECT_ID,
               SECT_LIBLT AS SECT_LIB,
               TRIM(CAST(SECT_CODE AS VARCHAR2(50))) AS SECT_CODE,
               STR_ID,
               TRIM(CAST(MAT_RELEV AS VARCHAR2(50))) AS MAT_RELEV
        FROM CRM_SNDE.S_SECTEUR
    """
    return oracle_client.requete(sql)


def ref_comptage() -> pd.DataFrame:
    # ⚠️ La table Oracle s'appelle bien COMTAGE (sans le P). Colonnes
    # réelles, vérifiées le 12/11/2026 : ID_COMP, LIBELLE, LIB_AR
    # (et non COMP_LIBLT, qui faisait échouer tout le chargement).
    sql = "SELECT ID_COMP, LIBELLE FROM CRM_SNDE.COMTAGE"
    return oracle_client.requete(sql)
