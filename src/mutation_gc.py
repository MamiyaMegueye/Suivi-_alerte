"""
Onglet « Mutation GC » — changements de compteur des gros consommateurs
(secteurs 04 / 08).

Module indépendant : il interroge Oracle directement (lecture seule, via
oracle_client.requete). Il ne fait que LIRE la table locale raw_compteur
(DuckDB) pour afficher le n° de compteur, et n'y écrit rien.
Repris de l'onglet « Nouveautés › Gros Conso » de snde-audit.

Fonctionnement (même principe que snde-audit) :
  - un job en arrière-plan extrait d'Oracle toutes les REFRESH_MIN minutes
    les demandes des FENETRE_JOURS derniers jours et les garde en mémoire
    (copie aussi dans data/mutation_gc_cache.pkl pour un démarrage instantané) ;
  - l'interface ne lit que cette copie : filtres et alertes instantanés ;
  - Oracle n'est interrogé à la demande que pour une période plus ancienne
    que la fenêtre (avec cache CACHE_SEC secondes).

Routes :
    GET /api/mutation-gc/centres   liste des centres (zones de config)
    GET /api/mutation-gc           demandes GC sur une période
    GET /api/mutation-gc/export    même chose en Excel
"""

from __future__ import annotations

import io
import logging
import pickle
import threading
import time
from datetime import date, datetime, timedelta
from threading import Lock

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from src import config, db, oracle_client

router = APIRouter(tags=["mutation-gc"])

S = "CRM_SNDE"
CACHE_SEC = 120
REFRESH_MIN = 5          # extraction Oracle en arrière-plan
FENETRE_JOURS = 60       # profondeur gardée en mémoire

log = logging.getLogger("mutation_gc")

_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
_cache_lock = Lock()
_centres_cache: tuple[float, list] | None = None


def _sql_centres() -> str:
    return f"""
SELECT str.STR_ID      AS STR_ID,
       str.ZONE_ID     AS ZONE_ID,
       str.STR_LIB_LT  AS CENTRE
FROM   {S}.S_STRUCTURE str
WHERE  str.ZONE_ID IN ({config.zone_ids_sql()})
  AND  str.STR_ID NOT IN ({config.exclude_sql()})
ORDER  BY str.STR_LIB_LT
"""


def _sql_demandes(resiliation: bool = False) -> str:
    if resiliation:
        filtre_type = """  -- Résiliations (règle R8 de snde-audit)
  AND  UPPER(TRIM(td.TYPD_LIB_LT)) LIKE '%SILIATION%'"""
    else:
        filtre_type = """  -- Uniquement les changements de compteur (mutation « Compteur »,
  -- hors « Référence compteur »), même règle que snde-audit R1
  AND  TRIM(td.TYPD_LIB_LT) = 'Mutation'
  AND  LOWER(d.MSG_MUTATION) LIKE '%compteur%'
  AND  LOWER(d.MSG_MUTATION) NOT LIKE '%rence compteur%'"""
    return f"""
SELECT
    TO_CHAR(d.DMD_ID)                                   AS NUM_DEMANDE,
    TO_CHAR(COALESCE(abn_nb.ABN_ID, abn_anc.ABN_ID))    AS REF_ABONNEMENT,
    d.DMD_NOM                                           AS CLIENT,
    td.TYPD_LIB_LT                                      AS TYPE_DEMANDE,
    d.MSG_MUTATION                                      AS TYPE_MUTATION,
    CASE WHEN d.FLAG_ANNUL   = 1 THEN 'Annulé'
         WHEN d.DMD_FLAG_VLD = 1 THEN 'Validé'
         ELSE 'En attente' END                          AS STATUT,
    d.DMD_DATE                                          AS DATE_DEMANDE,
    str.STR_ID                                          AS STR_ID,
    str.ZONE_ID                                         AS ZONE_ID,
    str.STR_LIB_LT                                      AS CENTRE,
    LPAD(TRIM(CAST(s.SECT_CODE AS VARCHAR2(50))), 2, '0') AS SECTEUR,
    TO_CHAR(d.UTIL_TRAC)                                AS CREE_PAR,
    -- Agent demandeur : S_DEMANDE.MAT_DEMANDEUR = S_PERSONNEL.PERS_MAT
    -- (vérifié le 21/11/2026 : 936 → 2333 SIDI MOHAMED O/ SIDI ALY)
    TRIM(CAST(p.MATRICULE AS VARCHAR2(50)))             AS MATRICULE_AGENT,
    TRIM(p.PERS_NOM || ' ' || p.PERS_PRENOM)            AS NOM_AGENT,
    d.DMD_ADRESSE                                       AS ADRESSE
FROM       {S}.S_DEMANDE      d
JOIN       {S}.S_STRUCTURE    str ON str.STR_ID  = d.STR_ID
JOIN       {S}.S_SECTEUR      s   ON s.SECT_ID   = d.SECT_ID
JOIN       {S}.S_TYPE_DEMANDE td  ON td.TYPD_ID  = d.TYPD_ID
LEFT JOIN  {S}.S_ABONNEMENT   abn_nb  ON abn_nb.DMD_ID  = d.DMD_ID
LEFT JOIN  {S}.S_ABONNEMENT   abn_anc ON abn_anc.ABN_ID = d.ABN_ANC
LEFT JOIN  {S}.S_PERSONNEL    p   ON p.PERS_MAT = d.MAT_DEMANDEUR
WHERE  d.DMD_DATE >= :date_debut
  AND  d.DMD_DATE <  :date_fin_excl
  AND  str.ZONE_ID IN ({config.zone_ids_sql()})
  AND  str.STR_ID NOT IN ({config.exclude_sql()})
  AND  TRIM(CAST(s.SECT_CODE AS VARCHAR2(50))) IN ('04', '4', '08', '8')
{filtre_type}
ORDER  BY d.DMD_DATE DESC, d.DMD_ID DESC
"""


def _executer(sql: str, params: dict | None = None) -> pd.DataFrame:
    try:
        return oracle_client.requete(sql, params)
    except oracle_client.OracleIndisponible as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail="Erreur Oracle : " + str(exc).splitlines()[0][:200],
        ) from exc


def _compteurs_oracle(refs: list[int]) -> dict[str, str]:
    """Compteur actuel par abonnement, lu dans S_COMPTEUR_INS (à jour).
    Si aucun compteur n'est en place, on prend le dernier posé."""
    res: dict[str, str] = {}
    for k in range(0, len(refs), 900):
        lot = refs[k:k + 900]
        ph = ", ".join(f":a{n}" for n in range(len(lot)))
        df = oracle_client.requete(f"""
            SELECT ABN_ID, CPT_SERIE FROM (
                SELECT c.ABN_ID, c.CPT_SERIE,
                       ROW_NUMBER() OVER (
                           PARTITION BY c.ABN_ID
                           ORDER BY CASE WHEN c.CPI_DATE_DEPOSE IS NULL THEN 0 ELSE 1 END,
                                    c.CPI_DATE_INS DESC NULLS LAST, c.ID DESC) AS RN
                FROM {S}.S_COMPTEUR_INS c
                WHERE c.ABN_ID IN ({ph}) AND c.CPT_SERIE IS NOT NULL
            ) WHERE RN = 1
        """, {f"a{n}": v for n, v in enumerate(lot)})
        for _, r in df.iterrows():
            res[str(int(r["ABN_ID"]))] = r["CPT_SERIE"]
    return res


def _compteurs_local(refs: list[int]) -> dict[str, str]:
    ph = ",".join(["?"] * len(refs))
    rows = db.lignes(
        f"""SELECT ABN_ID, CPT_SERIE
            FROM raw_compteur
            WHERE ABN_ID IN ({ph}) AND CPT_SERIE IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY ABN_ID
                ORDER BY CPI_DATE_INS DESC NULLS LAST, ID DESC) = 1""",
        refs,
    )
    return {str(r["ABN_ID"]): r["CPT_SERIE"] for r in rows}


def _ajouter_compteurs(df: pd.DataFrame) -> pd.DataFrame:
    """N° de compteur : Oracle d'abord, base locale raw_compteur en repli."""
    df = df.copy()
    df["NUM_COMPTEUR"] = None
    refs = sorted({int(r) for r in df["REF_ABONNEMENT"].dropna() if str(r).isdigit()})
    if not refs:
        return df
    m: dict[str, str] = {}
    try:
        m = _compteurs_oracle(refs)
    except Exception as exc:  # noqa: BLE001
        log.warning("mutation_gc : compteurs Oracle indisponibles : %s", exc)
    manquants = [r for r in refs if str(r) not in m]
    if manquants:
        try:
            m.update({k: v for k, v in _compteurs_local(manquants).items() if k not in m})
        except Exception as exc:  # noqa: BLE001
            log.warning("mutation_gc : compteurs locaux indisponibles : %s", exc)
    df["NUM_COMPTEUR"] = df["REF_ABONNEMENT"].map(lambda x: m.get(str(x)) if x else None)
    log.info("mutation_gc : compteur trouvé pour %d / %d abonnements",
             sum(1 for r in refs if str(r) in m), len(refs))
    return df


# ============================================================
# Anomalie « Conso nulle » : facture MUTATION à consommation nulle
# ============================================================
# Règle (validée sur les données le 19/11/2026) :
#   demande de changement de compteur GC VALIDÉE → 1re facture de type
#   Mutation (TYPE_FACT = 4) de l'abonné émise entre la date de demande et
#   +45 jours ; consommation nulle ou vide = anomalie, sauf si la facture
#   précédant la demande est Forfaitaire (TYPE_FACT = 2).
#   Une facture n'est rattachée qu'à une seule demande (la plus récente
#   avant elle), pour éviter les doublons demande annulée / refaite.

TYPES_FACT = {0: "Relevée", 1: "Estimée", 2: "Forfaitaire", 3: "Arrêt du compte",
              4: "Mutation", 5: "Débit intercalaire", 6: "Pénalité",
              7: "Redressement", 8: "Nv abonnement", 9: "Réabonnement",
              10: "Estimé migré", 11: "Migrée"}
TYPE_MUTATION = 4
TYPE_FORFAIT = 2
DELAI_FACT_JOURS = 45
HISTO_JOURS = 120

TYPE_ARRET = 3          # Facture Arrêt du Compte (résiliation)
ZONES = {1: "Intérieur", 2: "Nouakchott", 3: "Nouadhibou"}

COLS_CN = ["TYPE_DEMANDE", "NUM_DEMANDE", "DATE_DEMANDE", "REF_ABONNEMENT", "CLIENT", "CENTRE",
           "STR_ID", "ZONE_ID", "SECTEUR", "NUM_COMPTEUR", "MATRICULE_AGENT", "NOM_AGENT", "NUM_FACTURE",
           "DATE_FACTURE", "DELAI_JOURS", "CONSOMMATION", "MONTANT",
           "INDEX_DEBUT", "INDEX_FIN", "FACTURE_PRECEDENTE"]


def _factures_abonnes(refs: list[int], depuis: date) -> pd.DataFrame:
    morceaux = []
    for k in range(0, len(refs), 900):
        lot = refs[k:k + 900]
        ph = ", ".join(f":a{n}" for n in range(len(lot)))
        p = {f"a{n}": v for n, v in enumerate(lot)}
        p["depuis"] = depuis
        morceaux.append(oracle_client.requete(f"""
            SELECT fm.ABN_ID, fm.FACT_ID, fm.FACT_NUM, fm.FACT_DATE, fm.TYPE_FACT,
                   fm.FAT_QTE_CONS, fm.FACT_MNT,
                   rel.REL_ANCIEN_INDEX AS INDEX_DEBUT, rel.REL_INDEX AS INDEX_FIN,
                   TRIM(CAST(rel.MATRICULE AS VARCHAR2(50))) AS MAT_AGENT
            FROM {S}.S_FACTURE_M fm
            LEFT JOIN {S}.S_RELEVE rel ON rel.REL_ID = fm.REL_ID
            WHERE fm.ABN_ID IN ({ph})
              AND fm.FACT_DATE >= :depuis
              AND NVL(fm.FLAGANNULLE, 0) = 0
        """, p))
    return pd.concat(morceaux, ignore_index=True) if morceaux else pd.DataFrame()


def _mat(x) -> str | None:
    """Matricule propre : « 3155.0 » → « 3155 »."""
    if x is None or (not isinstance(x, str) and pd.isna(x)):
        return None
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s or None


def _conso_nulle_complet(dem: pd.DataFrame, res: pd.DataFrame | None,
                         agents: dict | None = None) -> pd.DataFrame:
    """Changements de compteur (facture Mutation) + résiliations (facture
    Arrêt du compte), consommation nulle."""
    a = _calculer_conso_nulle(dem, agents)
    b = pd.DataFrame(columns=COLS_CN)
    if res is not None and not res.empty:
        b = _calculer_conso_nulle(res, None, type_fact=TYPE_ARRET,
                                  libelle="Résiliation", exception_forfait=False,
                                  depuis_debut_mois=True)
    parts = [x for x in (a, b) if x is not None and not x.empty]
    if not parts:
        return pd.DataFrame(columns=COLS_CN)
    out = pd.concat(parts, ignore_index=True)
    return out.sort_values("DATE_DEMANDE", ascending=False)


def _calculer_conso_nulle(dem: pd.DataFrame, agents: dict | None = None,
                          type_fact: int = TYPE_MUTATION,
                          libelle: str = "Changement compteur",
                          exception_forfait: bool = True,
                          depuis_debut_mois: bool = False) -> pd.DataFrame:
    """Anomalies « conso nulle ». Si `agents` est fourni, il est rempli avec
    NUM_DEMANDE → matricule de l'agent qui a saisi le relevé de la facture
    de mutation (l'agent qui a fait le changement de compteur)."""
    vide = pd.DataFrame(columns=COLS_CN)
    if dem is None or dem.empty:
        return vide
    v = dem[(dem["STATUT"] == "Validé") & dem["REF_ABONNEMENT"].notna()].copy()
    v = v[v["REF_ABONNEMENT"].astype(str).str.isdigit()]
    if v.empty:
        return vide
    v["DD"] = pd.to_datetime(v["DATE_DEMANDE"], errors="coerce").dt.normalize()
    v = v[v["DD"].notna()]
    refs = sorted({int(r) for r in v["REF_ABONNEMENT"]})
    depuis = (v["DD"].min() - pd.Timedelta(days=HISTO_JOURS)).date()
    f = _factures_abonnes(refs, depuis)
    if f.empty:
        return vide
    f["ABN"] = f["ABN_ID"].astype("int64").astype(str)
    f["FD"] = pd.to_datetime(f["FACT_DATE"], errors="coerce")
    f = f[f["FD"].notna()].sort_values(["ABN", "FD", "FACT_ID"])
    par_abn = {k: g for k, g in f.groupby("ABN")}

    # facture mutation → demande validée la plus récente avant elle (≤ 45 j)
    retenue: dict = {}            # NUM_DEMANDE → ligne facture
    for abn, dv in v.groupby(v["REF_ABONNEMENT"].astype(str)):
        fa = par_abn.get(abn)
        if fa is None:
            continue
        dv = dv.sort_values("DD")
        for _, fac in fa[fa["TYPE_FACT"] == type_fact].iterrows():
            debut = (dv["DD"] - pd.to_timedelta(dv["DD"].dt.day - 1, unit="D")
                     if depuis_debut_mois else dv["DD"])
            cand = dv[(debut <= fac["FD"]) &
                      (fac["FD"] <= dv["DD"] + pd.Timedelta(days=DELAI_FACT_JOURS))]
            if cand.empty:
                continue
            nd = cand.iloc[-1]["NUM_DEMANDE"]
            if nd not in retenue or fac["FD"] < retenue[nd]["FD"]:
                retenue[nd] = fac

    if agents is not None:
        for nd, fac in retenue.items():
            agents[nd] = _mat(fac.get("MAT_AGENT"))

    lignes = []
    for _, d in v.iterrows():
        fac = retenue.get(d["NUM_DEMANDE"])
        if fac is None:
            continue
        conso = fac["FAT_QTE_CONS"]
        if conso is not None and not pd.isna(conso) and float(conso) != 0:
            continue
        fa = par_abn.get(str(d["REF_ABONNEMENT"]))
        prec = (fa[(fa["FD"] < d["DD"]) & (fa["FACT_ID"] != fac["FACT_ID"])]
                if fa is not None else None)
        type_prec = None
        if prec is not None and not prec.empty:
            type_prec = int(prec.iloc[-1]["TYPE_FACT"])
        if exception_forfait and type_prec == TYPE_FORFAIT:
            continue  # conso nulle justifiée par un forfait
        lignes.append({
            "TYPE_DEMANDE": libelle,
            "NUM_DEMANDE": d["NUM_DEMANDE"], "DATE_DEMANDE": d["DATE_DEMANDE"],
            "REF_ABONNEMENT": d["REF_ABONNEMENT"], "CLIENT": d.get("CLIENT"),
            "CENTRE": d.get("CENTRE"), "STR_ID": d.get("STR_ID"),
            "ZONE_ID": d.get("ZONE_ID"), "SECTEUR": d.get("SECTEUR"),
            "NUM_COMPTEUR": d.get("NUM_COMPTEUR"),
            "MATRICULE_AGENT": _mat(d.get("MATRICULE_AGENT")),
            "NOM_AGENT": d.get("NOM_AGENT"),
            "NUM_FACTURE": str(fac["FACT_NUM"]) if fac["FACT_NUM"] is not None else None,
            "DATE_FACTURE": fac["FD"],
            "DELAI_JOURS": max(0, int((fac["FD"].normalize() - d["DD"]).days)),
            "CONSOMMATION": 0 if conso is None or pd.isna(conso) else float(conso),
            "MONTANT": None if pd.isna(fac["FACT_MNT"]) else float(fac["FACT_MNT"]),
            "INDEX_DEBUT": None if pd.isna(fac["INDEX_DEBUT"]) else float(fac["INDEX_DEBUT"]),
            "INDEX_FIN": None if pd.isna(fac["INDEX_FIN"]) else float(fac["INDEX_FIN"]),
            "FACTURE_PRECEDENTE": TYPES_FACT.get(type_prec, "—" if type_prec is None else f"Type {type_prec}"),
        })
    out = pd.DataFrame(lignes, columns=COLS_CN)
    return out.sort_values("DATE_DEMANDE", ascending=False) if not out.empty else out


# ============================================================
# Copie locale rafraîchie en arrière-plan (comme snde-audit)
# ============================================================
_local = {"df": None, "debut": None, "maj": None, "duree": None,
          "erreur": None, "en_cours": False, "anoms": None}
_pret = threading.Event()
_FICHIER = config.DB_PATH.parent / "mutation_gc_cache.pkl"


def _rafraichir() -> None:
    debut = date.today() - timedelta(days=FENETRE_JOURS)
    t0 = time.time()
    _local["en_cours"] = True
    try:
        df = oracle_client.requete(_sql_demandes(), {
            "date_debut": debut,
            "date_fin_excl": date.today() + timedelta(days=1)})
        df = _ajouter_compteurs(df)
        agents: dict = {}
        try:
            res = oracle_client.requete(_sql_demandes(resiliation=True), {
                "date_debut": debut,
                "date_fin_excl": date.today() + timedelta(days=1)})
            res = _ajouter_compteurs(res)
        except Exception as exc:  # noqa: BLE001
            log.warning("mutation_gc : résiliations non chargées : %s", exc)
            res = None
        try:
            anoms = _conso_nulle_complet(df, res, agents)
        except Exception as exc:  # noqa: BLE001
            log.warning("mutation_gc : conso nulle non calculée : %s", exc)
            anoms = _local.get("anoms")
        # (l'agent vient maintenant de la demande : MAT_DEMANDEUR)
        _local.update(df=df, res=res, debut=debut, maj=datetime.now(), anoms=anoms,
                      duree=round(time.time() - t0, 1), erreur=None)
        _pret.set()
        try:
            with open(_FICHIER, "wb") as f:
                pickle.dump({"df": df, "res": res, "debut": debut,
                             "maj": _local["maj"], "anoms": anoms}, f)
        except Exception:  # noqa: BLE001
            pass
        log.info("mutation_gc : %d demandes chargées en %.1fs", len(df), _local["duree"])
    except Exception as exc:  # noqa: BLE001
        _local["erreur"] = str(exc).splitlines()[0][:200]
        log.warning("mutation_gc : échec du rafraîchissement : %s", _local["erreur"])
    finally:
        _local["en_cours"] = False


def _boucle() -> None:
    while True:
        _rafraichir()
        time.sleep(REFRESH_MIN * 60)


def _demarrer() -> None:
    # Démarrage instantané depuis la dernière copie sur disque
    try:
        with open(_FICHIER, "rb") as f:
            sauv = pickle.load(f)
        _local.update(df=sauv["df"], res=sauv.get("res"), debut=sauv["debut"],
                      maj=sauv["maj"], anoms=sauv.get("anoms"))
        _pret.set()
    except Exception:  # noqa: BLE001
        pass
    threading.Thread(target=_boucle, name="mutation_gc", daemon=True).start()


_demarrer()


NATURE_CC, NATURE_RES = "Changement compteur", "Résiliation"


def _demandes(date_debut: date | None, date_fin: date | None,
              str_id: int | None) -> pd.DataFrame:
    """Onglet Demandes : changements de compteur + résiliations (colonne NATURE)."""
    cc = _demandes_cc(date_debut, date_fin, str_id)
    try:  # une panne Oracle sur les résiliations ne bloque pas l'onglet
        rs = _demandes_cc(date_debut, date_fin, str_id, resiliation=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("mutation_gc : résiliations indisponibles : %s", exc)
        rs = None
    parts = []
    if cc is not None and not cc.empty:
        parts.append(cc.assign(NATURE=NATURE_CC))
    if rs is not None and not rs.empty:
        parts.append(rs.assign(NATURE=NATURE_RES))
    if not parts:
        return (cc if cc is not None else pd.DataFrame()).assign(NATURE=None)
    out = pd.concat(parts, ignore_index=True)
    dd = pd.to_datetime(out["DATE_DEMANDE"], errors="coerce")
    return out.assign(_dd=dd).sort_values("_dd", ascending=False).drop(columns="_dd")


def _demandes_cc(date_debut: date | None, date_fin: date | None,
                 str_id: int | None, resiliation: bool = False) -> pd.DataFrame:
    d1 = date_debut or date.today()
    d2 = date_fin or d1
    if d2 < d1:
        d1, d2 = d2, d1

    # Période couverte par la copie locale → lecture instantanée
    if not _pret.is_set():
        _pret.wait(timeout=180)  # tout premier démarrage
    loc = _local.get("res") if resiliation else _local["df"]
    if loc is not None and _local["debut"] and d1 >= _local["debut"]:
        dd = pd.to_datetime(loc["DATE_DEMANDE"], errors="coerce").dt.date
        df = loc[(dd >= d1) & (dd <= d2)]
        if str_id is not None and not df.empty:
            df = df[df["STR_ID"] == int(str_id)]
        return df

    # Période plus ancienne → Oracle à la demande
    cle = (d1, d2, resiliation)
    with _cache_lock:  # une seule requête Oracle à la fois par période
        hit = _cache.get(cle)
        if hit and time.time() - hit[0] < CACHE_SEC:
            df = hit[1]
        else:
            df = _executer(_sql_demandes(resiliation=resiliation), {
                "date_debut": d1, "date_fin_excl": d2 + timedelta(days=1)})
            df = _ajouter_compteurs(df)
            _cache[cle] = (time.time(), df)
            if len(_cache) > 50:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:-50]:
                    _cache.pop(k, None)
    if str_id is not None and not df.empty:
        df = df[df["STR_ID"] == int(str_id)]
    return df


def _sz(df: pd.DataFrame) -> pd.DataFrame:
    """ZONE_ID → sous-zone (11-18) d'après le centre (config.SOUS_ZONES)."""
    if df is None or df.empty or "STR_ID" not in df.columns:
        return df
    out = df.copy()
    sz = out["STR_ID"].map(config.sous_zone_de)
    out["ZONE_ID"] = sz.where(sz.notna(), out.get("ZONE_ID"))
    return out


def _records(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    out = _sz(df)
    if "DATE_DEMANDE" in out.columns:
        out["DATE_DEMANDE"] = pd.to_datetime(out["DATE_DEMANDE"], errors="coerce")
        out["DATE_DEMANDE"] = out["DATE_DEMANDE"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    out = out.astype(object).where(pd.notna(out), None)
    return out.to_dict(orient="records")


@router.get("/api/mutation-gc/centres")
def mgc_centres() -> list[dict]:
    global _centres_cache
    if _centres_cache and time.time() - _centres_cache[0] < 3600:
        return _centres_cache[1]
    res = _records(_executer(_sql_centres()))
    _centres_cache = (time.time(), res)
    return res


@router.get("/api/mutation-gc/etat")
def mgc_etat() -> dict:
    return {
        "derniere_maj": _local["maj"].isoformat(timespec="seconds") if _local["maj"] else None,
        "duree_sec": _local["duree"],
        "en_cours": _local["en_cours"],
        "erreur": _local["erreur"],
        "fenetre_debut": _local["debut"].isoformat() if _local["debut"] else None,
        "nb": 0 if _local["df"] is None else int(len(_local["df"])),
        "intervalle_min": REFRESH_MIN,
    }


@router.get("/api/mutation-gc")
def mgc_liste(
    date_debut: date | None = Query(None, description="YYYY-MM-DD (défaut : aujourd'hui)"),
    date_fin: date | None = Query(None, description="YYYY-MM-DD (défaut : date_debut)"),
    str_id: int | None = Query(None, description="Centre (STR_ID)"),
) -> dict:
    df = _demandes(date_debut, date_fin, str_id)
    return {"nb": int(len(df)), "rows": _records(df)}


COLONNES_EXPORT = [
    ("NATURE", "Type"),
    ("NUM_DEMANDE", "Num Demande"),
    ("REF_ABONNEMENT", "Réf Abo"),
    ("CLIENT", "Client"),
    ("TYPE_DEMANDE", "Type Demande"),
    ("TYPE_MUTATION", "Type Mutation"),
    ("STATUT", "Statut"),
    ("DATE_DEMANDE", "Date"),
    ("CENTRE", "Centre"),
    ("SECTEUR", "Secteur"),
    ("NUM_COMPTEUR", "Num Compteur"),
    ("CREE_PAR", "Créé par"),
    ("MATRICULE_AGENT", "Agent (matricule)"),
    ("NOM_AGENT", "Agent (nom)"),
    ("ADRESSE", "Adresse"),
]


def _conso_nulle(date_debut: date | None, date_fin: date | None,
                 str_id: int | None) -> pd.DataFrame:
    d1 = date_debut or date.today()
    d2 = date_fin or d1
    if d2 < d1:
        d1, d2 = d2, d1
    if not _pret.is_set():
        _pret.wait(timeout=180)
    if _local["debut"] and d1 >= _local["debut"]:
        an = _local.get("anoms")
        if an is None:                       # ancienne copie sans ce calcul
            an = _calculer_conso_nulle(_local["df"])   # copie ancienne : sans résiliations
            _local["anoms"] = an
    else:                                    # période ancienne : calcul à la demande
        try:
            res_old = _executer(_sql_demandes(resiliation=True), {
                "date_debut": d1, "date_fin_excl": d2 + timedelta(days=1)})
            an = _conso_nulle_complet(_demandes_cc(d1, d2, None), res_old)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail="Erreur Oracle : " + str(exc)[:200]) from exc
    if an is None or an.empty:
        return pd.DataFrame(columns=COLS_CN)
    dd = pd.to_datetime(an["DATE_DEMANDE"], errors="coerce").dt.date
    an = an[(dd >= d1) & (dd <= d2)]
    if str_id is not None and not an.empty:
        an = an[an["STR_ID"] == int(str_id)]
    return an


def _records_cn(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    out = _sz(df)
    for c in ("DATE_DEMANDE", "DATE_FACTURE"):
        out[c] = pd.to_datetime(out[c], errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%S")
    out = out.astype(object).where(pd.notna(out), None)
    return out.to_dict(orient="records")


@router.get("/api/mutation-gc/conso-nulle")
def mgc_conso_nulle(
    date_debut: date | None = Query(None),
    date_fin: date | None = Query(None),
    str_id: int | None = Query(None),
) -> dict:
    df = _conso_nulle(date_debut, date_fin, str_id)
    return {"nb": int(len(df)), "rows": _records_cn(df)}


COLONNES_EXPORT_CN = [
    ("TYPE_DEMANDE", "Type"), ("NUM_DEMANDE", "Num Demande"), ("DATE_DEMANDE", "Date demande"),
    ("REF_ABONNEMENT", "Réf Abo"), ("CLIENT", "Client"), ("CENTRE", "Centre"),
    ("SECTEUR", "Secteur"), ("NUM_COMPTEUR", "Num Compteur"),
    ("MATRICULE_AGENT", "Agent (matricule)"),
    ("NOM_AGENT", "Agent (nom)"),
    ("NUM_FACTURE", "N° facture mutation"), ("DATE_FACTURE", "Date facture"),
    ("DELAI_JOURS", "Délai (j)"), ("CONSOMMATION", "Consommation"),
    ("MONTANT", "Montant"), ("INDEX_DEBUT", "Index début"), ("INDEX_FIN", "Index fin"),
    ("FACTURE_PRECEDENTE", "Facture précédente"),
]


@router.get("/api/mutation-gc/conso-nulle/export")
def mgc_conso_nulle_export(
    date_debut: date | None = Query(None),
    date_fin: date | None = Query(None),
    str_id: int | None = Query(None),
    zone: int | None = Query(None),
):
    from openpyxl import Workbook
    from openpyxl.styles import (Alignment, Border, Font, GradientFill,
                                 PatternFill, Side)

    df = _conso_nulle(date_debut, date_fin, str_id)
    if zone is not None and not df.empty:
        df = _sz(df)
        df = df[df["ZONE_ID"] == int(zone)]
    wb = Workbook()
    ws = wb.active
    ws.title = "Conso nulle"
    ws.append([lib for _, lib in COLONNES_EXPORT_CN])
    # En-tête beige en dégradé, texte brun foncé
    trait = Side(style="thin", color="D9C7A3")
    for c in ws[1]:
        c.fill = GradientFill(type="linear", degree=90,
                              stop=("F7EEDC", "E3CFA6"))
        c.font = Font(bold=True, color="5C4528")
        c.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        c.border = Border(bottom=Side(style="medium", color="B8996A"))
    ws.row_dimensions[1].height = 30
    for _, r in df.iterrows():
        ligne = []
        for col, _ in COLONNES_EXPORT_CN:
            val = r.get(col)
            if val is None or (not isinstance(val, str) and pd.isna(val)):
                val = None
            elif col in ("DATE_DEMANDE", "DATE_FACTURE"):
                val = pd.Timestamp(val).to_pydatetime()
            ligne.append(val)
        ws.append(ligne)
    # Lignes alternées beige très clair / blanc, fines bordures beige
    zebre = PatternFill("solid", fgColor="FBF6EC")
    for n, ligne_xl in enumerate(ws.iter_rows(min_row=2), start=0):
        for c in ligne_xl:
            if n % 2 == 0:
                c.fill = zebre
            c.border = Border(bottom=trait)
            c.font = Font(color="3F3324")
    for i, (col, _) in enumerate(COLONNES_EXPORT_CN, start=1):
        lettre = ws.cell(row=1, column=i).column_letter
        ws.column_dimensions[lettre].width = 30 if col in ("CLIENT", "CENTRE") else 15
        if col in ("DATE_DEMANDE", "DATE_FACTURE"):
            for cell in ws[lettre][1:]:
                if isinstance(cell.value, datetime):
                    cell.value = cell.value.date()   # jour seulement, sans l'heure
                cell.number_format = "dd/mm/yyyy"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    nom = f"mutation_gc_conso_nulle_{date.today():%Y%m%d}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )


@router.get("/api/mutation-gc/conso-nulle/synthese")
def mgc_conso_nulle_synthese(
    date_debut: date | None = Query(None),
    date_fin: date | None = Query(None),
    str_id: int | None = Query(None),
    zone: int | None = Query(None),
):
    """Synthèse pour la direction : nombre d'anomalies « conso nulle » par
    centre, changements de compteur et résiliations."""
    from openpyxl import Workbook
    from openpyxl.styles import (Alignment, Border, Font, GradientFill,
                                 PatternFill, Side)

    d1 = date_debut or date.today()
    d2 = date_fin or d1
    df = _conso_nulle(d1, d2, str_id)
    if zone is not None and not df.empty:
        df = _sz(df)
        df = df[df["ZONE_ID"] == int(zone)]

    lignes = []
    if not df.empty:
        g = (df.assign(n=1)
               .pivot_table(index="CENTRE", columns="TYPE_DEMANDE", values="n",
                            aggfunc="sum", fill_value=0))
        for centre, r in g.iterrows():
            cc = int(r.get("Changement compteur", 0))
            rs = int(r.get("Résiliation", 0))
            lignes.append((str(centre), cc, rs, cc + rs))
        lignes.sort(key=lambda x: (-x[3], x[0]))
    tot_cc = sum(x[1] for x in lignes)
    tot_rs = sum(x[2] for x in lignes)

    brun, brun_fonce = "1F3A5F", "17304F"
    entete_fill = GradientFill(type="linear", degree=90, stop=("DCEBF8", "A9CBEA"))
    total_fill = GradientFill(type="linear", degree=90, stop=("C9DFF3", "8DB6DE"))
    zebre = PatternFill("solid", fgColor="F2F7FC")
    trait = Side(style="thin", color="C3D6EA")

    wb = Workbook()
    ws = wb.active
    ws.title = "Synthèse DG"
    ws.sheet_view.showGridLines = False
    ws.merge_cells("A1:D1")
    ws["A1"] = "Anomalies « consommation nulle » — gros consommateurs (secteurs 04 / 08)"
    ws["A1"].font = Font(bold=True, size=14, color=brun)
    ws.merge_cells("A2:D2")
    ws["A2"] = (f"Demandes validées du {d1:%d/%m/%Y} au {d2:%d/%m/%Y}"
                + (f" — zone {config.zone_libelle(zone)}" if zone is not None else " — toutes zones")
                + f" — édité le {datetime.now():%d/%m/%Y}")
    ws["A2"].font = Font(italic=True, size=10, color="4A6688")
    ws.row_dimensions[1].height = 24

    entetes = ["Centre", "Changement de compteur", "Résiliation", "Total"]
    ws.append([])
    ws.append(entetes)
    for c in ws[4]:
        c.fill = entete_fill
        c.font = Font(bold=True, color=brun)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = Border(bottom=Side(style="medium", color="6F9CCB"))
    ws.row_dimensions[4].height = 30

    for i, (centre, cc, rs, tt) in enumerate(lignes):
        ws.append([centre, cc, rs, tt])
        for c in ws[ws.max_row]:
            if i % 2 == 0:
                c.fill = zebre
            c.border = Border(bottom=trait)
            c.font = Font(color=brun_fonce, bold=(c.column == 4))
            if c.column > 1:
                c.alignment = Alignment(horizontal="center")

    ws.append(["TOTAL", tot_cc, tot_rs, tot_cc + tot_rs])
    for c in ws[ws.max_row]:
        c.fill = total_fill
        c.font = Font(bold=True, color=brun)
        c.border = Border(top=Side(style="medium", color="6F9CCB"))
        if c.column > 1:
            c.alignment = Alignment(horizontal="center")
    if not lignes:
        ws.append(["Aucune anomalie sur la période."])

    ws.column_dimensions["A"].width = 32
    for col in "BCD":
        ws.column_dimensions[col].width = 20

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    nom = f"synthese_conso_nulle_{date.today():%Y%m%d}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )


@router.get("/api/mutation-gc/export")
def mgc_export(
    date_debut: date | None = Query(None),
    date_fin: date | None = Query(None),
    str_id: int | None = Query(None),
    statut: str | None = Query(None, description="Validé / En attente / Annulé"),
    secteur: str | None = Query(None, description="04 ou 08"),
):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    df = _demandes(date_debut, date_fin, str_id)
    if statut:
        df = df[df["STATUT"] == statut]
    if secteur:
        df = df[df["SECTEUR"] == secteur]

    wb = Workbook()
    ws = wb.active
    ws.title = "Mutation GC"
    entete = PatternFill("solid", fgColor="0A2A4E")
    ws.append([lib for _, lib in COLONNES_EXPORT])
    for c in ws[1]:
        c.fill = entete
        c.font = Font(bold=True, color="FFFFFF")
        c.alignment = Alignment(vertical="center")

    for _, r in df.iterrows():
        ligne = []
        for col, _ in COLONNES_EXPORT:
            v = r.get(col)
            if v is None or (not isinstance(v, str) and pd.isna(v)):
                v = None
            elif col == "DATE_DEMANDE":
                v = pd.Timestamp(v).to_pydatetime()
            ligne.append(v)
        ws.append(ligne)

    for i, (col, _) in enumerate(COLONNES_EXPORT, start=1):
        lettre = ws.cell(row=1, column=i).column_letter
        ws.column_dimensions[lettre].width = 30 if col in ("CLIENT", "ADRESSE", "CENTRE") else 16
        if col == "DATE_DEMANDE":
            for cell in ws[lettre][1:]:
                if isinstance(cell.value, datetime):
                    cell.value = cell.value.date()   # jour seulement
                cell.number_format = "dd/mm/yyyy"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    nom = f"mutation_gc_{date.today():%Y%m%d}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )
