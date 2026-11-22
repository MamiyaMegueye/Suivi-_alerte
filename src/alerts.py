"""
Détection et journal des alertes de baisse.

Deux alertes distinctes
-----------------------
**Baisse de facturation — rouge.** Levée quand un secteur est
RÉELLEMENT FACTURÉ (au moins une facture émise sur le cycle) et que son
volume baisse par rapport au cycle N-2 (N-1 pour les gros consommateurs) :
volume facturé TOTAL baisse d'au moins SEUIL_BAISSE_M3 m³ (1000 par défaut).
La cause principale est notée : abonnés constants ou abonnés perdus.
Règle : queries.cond_alerte().

Attendre la facturation est le point clé : un secteur clôturé mais pas
encore facturé a un volume incomplet, et un secteur à 40 % de saisie est
forcément « en baisse » puisqu'il manque des relevés. Ces secteurs
restent visibles à l'écran, avec leur état, mais ne déclenchent rien.
Réglages : ALERTE_BAISSE_EXIGE_FACTURE, ALERTER_PROVISOIRE.

**Retard de facturation — jaune.** Levée quand un secteur n'est
TOUJOURS PAS CLÔTURÉ alors que son jour J de facturation est passé : il
ne pourra pas être facturé dans les délais.

Même population que l'onglet Retards du dashboard — les secteurs
`CLOTURE = 0`, avec l'exemption au-delà de 90 % de saisie. Un secteur
déjà clôturé a fait son travail : la facturation qui suit est un
traitement de masse, il apparaît en « à facturer » sans alerter. Sans
cette distinction, presque tous les secteurs d'un cycle en cours
remonteraient en retard.

Les deux alertes sont indépendantes : un secteur peut être en retard sans
être en baisse, et inversement.

Cycle de vie
------------
Une ligne par (cycle, centre, secteur). À chaque passage du watcher les
chiffres sont réactualisés, mais `acknowledged` n'est jamais remis à
FALSE : une alerte consultée reste consultée, et le bandeau rouge ne
réapparaît pas pour elle. Seule une nouvelle alerte le fait revenir.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src import config, cycles, db, queries

# Nombre de cycles récents balayés à chaque détection. Deux suffisent :
# un secteur qui clôture en retard appartient encore au cycle précédent.
CYCLES_SURVEILLES = 2


def _baisse_alertable(s: dict) -> bool:
    """Le secteur est-il dans un état où sa baisse veut dire quelque chose ?

    Règle par défaut : il faut qu'il soit facturé. Tant que les factures
    ne sont pas émises, le volume du cycle est incomplet.
    """
    if config.ALERTER_PROVISOIRE:
        return True
    if config.ALERTE_BAISSE_EXIGE_FACTURE:
        # Clôturé et facturé (voir `etat` dans queries._sql_secteurs).
        return s.get("etat") == "FACTURE"
    return s.get("fiabilite") == "definitive"


def _cycles_a_surveiller() -> list[tuple[int, int]]:
    rows = db.lignes(f"""
        SELECT DISTINCT ANNEE AS a, MOIS AS m, ANNEE * 100 + MOIS AS k
        FROM raw_cloture_secteur
        ORDER BY k DESC
        LIMIT {int(CYCLES_SURVEILLES)}
    """)
    return [(int(r["a"]), int(r["m"])) for r in rows]


# ============================================================
# Détection
# ============================================================

def detecter() -> int:
    """Met à jour le journal. Renvoie le nombre de NOUVELLES alertes."""
    db.initialiser()
    nouvelles = 0
    maintenant = datetime.now()

    for annee, mois in _cycles_a_surveiller():
        ck = cycles.cycle_key(annee, mois)

        # Secteurs ordinaires comparés à N-2, gros consommateurs à N-1.
        secteurs = []
        vus: list[int] = []      # secteurs en alerte à ce passage
        complet = True           # les deux populations ont été évaluées
        for pop, comp in (("bimestriel", "N-2"), ("mensuel", "N-1")):
            try:
                for s in queries.secteurs(annee, mois, seulement_baisses=True,
                                          population=pop):
                    s["comparaison"] = comp
                    secteurs.append(s)
            except Exception:  # noqa: BLE001
                # Cycle de référence absent de la base : rien à comparer.
                complet = False
                continue

        for s in secteurs:
            if not _baisse_alertable(s):
                continue

            str_id = s.get("centre_code")
            sect_id = s.get("secteur_code")
            if str_id is None or sect_id is None:
                continue
            vus.append(int(sect_id))

            existe = db.valeur(
                "SELECT alert_id FROM alertes_baisse "
                "WHERE cycle_key = ? AND STR_ID = ? AND SECT_ID = ?",
                [ck, str_id, sect_id],
            )

            if existe is None:
                nid = (db.valeur(
                    "SELECT COALESCE(MAX(alert_id), 0) + 1 FROM alertes_baisse"
                ) or 1)
                db.executer("""
                    INSERT INTO alertes_baisse
                      (alert_id, cycle_key, ANNEE, MOIS, STR_ID, SECT_ID,
                       vol_n, vol_p, vol_total_n, vol_total_p,
                       ecart, ecart_pct, ecart_total, ecart_total_pct,
                       gravite, etat, fiabilite, comparaison, cause,
                       detected_at, updated_at, acknowledged, acknowledged_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,FALSE,NULL)
                """, [
                    nid, ck, annee, mois, str_id, sect_id,
                    s.get("vol_n"), s.get("vol_p"),
                    s.get("vol_total_n"), s.get("vol_total_p"),
                    s.get("ecart"), s.get("ecart_pct"),
                    s.get("ecart_total"), s.get("ecart_total_pct"),
                    s.get("gravite"), s.get("etat"), s.get("fiabilite"),
                    s.get("comparaison"), s.get("cause"),
                    maintenant, maintenant,
                ])
                nouvelles += 1
            else:
                # Réactualisation des chiffres, sans toucher à
                # l'acquittement : consulté reste consulté.
                db.executer("""
                    UPDATE alertes_baisse
                    SET vol_n = ?, vol_p = ?, vol_total_n = ?, vol_total_p = ?,
                        ecart = ?, ecart_pct = ?,
                        ecart_total = ?, ecart_total_pct = ?,
                        gravite = ?, etat = ?, fiabilite = ?,
                        comparaison = ?, cause = ?, updated_at = ?
                    WHERE alert_id = ?
                """, [
                    s.get("vol_n"), s.get("vol_p"),
                    s.get("vol_total_n"), s.get("vol_total_p"),
                    s.get("ecart"), s.get("ecart_pct"),
                    s.get("ecart_total"), s.get("ecart_total_pct"),
                    s.get("gravite"), s.get("etat"), s.get("fiabilite"),
                    s.get("comparaison"), s.get("cause"),
                    maintenant, existe,
                ])

        # Une alerte qui ne correspond plus à un secteur facturé et en
        # baisse (règle changée, données corrigées) sort du journal et du
        # bandeau, sans être effacée.
        if complet:
            if vus:
                liste = ", ".join(str(x) for x in sorted(set(vus)))
                db.executer(f"UPDATE alertes_baisse SET active = "
                            f"(SECT_ID IN ({liste})) WHERE cycle_key = ?", [ck])
            else:
                db.executer("UPDATE alertes_baisse SET active = FALSE "
                            "WHERE cycle_key = ?", [ck])

    return nouvelles


# ============================================================
# Lecture
# ============================================================

_SELECT = """
    SELECT
        a.alert_id, a.cycle_key, a.ANNEE, a.MOIS,
        a.STR_ID AS centre_code,
        COALESCE(ce.CENTRE_LIB,
                 '(centre ' || CAST(a.STR_ID AS VARCHAR) || ')')
                                              AS centre_libelle,
        ce.ZONE_ID                            AS zone_id,
        a.SECT_ID AS secteur_code,
        COALESCE(NULLIF(TRIM(sec.SECT_CODE), ''),
                 CAST(a.SECT_ID AS VARCHAR))  AS secteur_num,
        COALESCE(sec.SECT_LIB,
                 '(secteur ' || CAST(a.SECT_ID AS VARCHAR) || ')')
                                              AS secteur_libelle,
        a.vol_n, a.vol_p, a.vol_total_n, a.vol_total_p, a.ecart, a.ecart_pct, a.ecart_total, a.ecart_total_pct,
        a.gravite, a.etat, a.fiabilite, a.cause,
        COALESCE(a.comparaison, 'N-2')        AS comparaison,
        COALESCE(NULLIF(TRIM(regexp_replace(CAST(rv.MAT AS VARCHAR), '\\.0+$', '')), ''),
                 NULLIF(TRIM(regexp_replace(CAST(sec.MAT_RELEV AS VARCHAR), '\\.0+$', '')), ''))
                                              AS matricule_releveur,
        a.detected_at, a.updated_at, a.acknowledged, a.acknowledged_at
    FROM alertes_baisse a
    LEFT JOIN ref_centre  ce  ON ce.CENTRE_ID = a.STR_ID
    LEFT JOIN ref_secteur sec ON sec.SECT_ID  = a.SECT_ID
    -- releveur ayant saisi le secteur sur le cycle (matricule le plus fréquent)
    LEFT JOIN (SELECT STR_ID, SECT_ID, REL_ANNEE, REL_MOIS, MODE(MATRICULE) AS MAT
               FROM raw_releve GROUP BY STR_ID, SECT_ID, REL_ANNEE, REL_MOIS) rv
           ON rv.STR_ID = a.STR_ID AND rv.SECT_ID = a.SECT_ID
          AND rv.REL_ANNEE = a.ANNEE AND rv.REL_MOIS = a.MOIS
"""


def lister(
    only_unack: bool = False,
    cycle: int | None = None,
    str_id: int | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    conds, params = ["COALESCE(a.active, TRUE)"], []
    if only_unack:
        conds.append("a.acknowledged = FALSE")
    if cycle is not None:
        conds.append("a.cycle_key = ?")
        params.append(int(cycle))
    if str_id is not None:
        conds.append("a.STR_ID = ?")
        params.append(int(str_id))
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    rows = db.lignes(
        f"{_SELECT} {where} ORDER BY a.ecart_total ASC NULLS LAST "
        f"LIMIT {int(limit)}",
        params,
    )
    for r in rows:
        config.appliquer_sous_zone(r)
        r["cycle"] = cycles.libelle_key(int(r["cycle_key"]))
        a_, m_ = cycles.depuis_key(int(r["cycle_key"]))
        ref = (cycles.mois_precedent(a_, m_) if r.get("comparaison") == "N-1"
               else cycles.cycle_precedent(a_, m_))
        r["cycle_precedent"] = cycles.libelle(*ref)
    return rows


def compter(only_unack: bool = True, cycle: int | None = None,
            zone: int | None = None, str_id: int | None = None) -> dict:
    conds, params = ["COALESCE(active, TRUE)"], []
    if str_id is not None:
        conds.append("STR_ID = ?")
        params.append(int(str_id))
    if only_unack:
        conds.append("acknowledged = FALSE")
    if cycle is not None:
        conds.append("cycle_key = ?")
        params.append(int(cycle))
    if zone is not None:
        _cs = config.centres_de_zone(zone)
        if _cs is not None:
            conds.append("STR_ID IN (" + ", ".join(str(x) for x in _cs) + ")")
        else:
            conds.append("STR_ID IN (SELECT CENTRE_ID FROM ref_centre WHERE ZONE_ID = ?)")
            params.append(int(zone))
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    # Seules les PERTES sont additionnées : un secteur en alerte pour son
    # volume total peut avoir des abonnés constants en hausse, qui ne
    # doivent pas venir réduire la perte affichée.
    row = db.ligne(f"""
        SELECT
            COUNT(*)                                    AS n,
            COUNT(DISTINCT STR_ID)                      AS nb_centres,
            CAST(COALESCE(SUM(-ecart) FILTER (WHERE ecart < 0), 0)
                 AS BIGINT)                             AS volume_perdu,
            CAST(COALESCE(SUM(-ecart_total) FILTER (WHERE ecart_total < 0), 0)
                 AS BIGINT)                             AS baisse_totale,
            COUNT(*) FILTER (WHERE cause = '{queries.CAUSE_CONSTANTS}')
                                                        AS nb_cause_constants,
            COUNT(*) FILTER (WHERE cause = '{queries.CAUSE_PERDUS}')
                                                        AS nb_cause_perdus,
            COUNT(*) FILTER (WHERE gravite = 'FORTE')   AS nb_forte,
            COUNT(*) FILTER (WHERE gravite = 'DISPARU') AS nb_disparus,
            MAX(detected_at)                            AS derniere
        FROM alertes_baisse {where}
    """, params)
    return {
        "n": int(row.get("n") or 0),
        "nb_centres": int(row.get("nb_centres") or 0),
        "volume_perdu": int(row.get("volume_perdu") or 0),
        "baisse_totale": int(row.get("baisse_totale") or 0),
        "nb_cause_constants": int(row.get("nb_cause_constants") or 0),
        "nb_cause_perdus": int(row.get("nb_cause_perdus") or 0),
        "seuil_m3": float(config.SEUIL_BAISSE_M3),
        "nb_forte": int(row.get("nb_forte") or 0),
        "nb_disparus": int(row.get("nb_disparus") or 0),
        "derniere": row.get("derniere"),
    }


# ============================================================
# Acquittement — « ne disparaît que si on le consulte »
# ============================================================

def acquitter(alert_id: int) -> int:
    db.executer("""
        UPDATE alertes_baisse
        SET acknowledged = TRUE, acknowledged_at = ?
        WHERE alert_id = ? AND acknowledged = FALSE
    """, [datetime.now(), int(alert_id)])
    return 1


def acquitter_tout(cycle: int | None = None) -> int:
    conds = ["acknowledged = FALSE"]
    params: list = [datetime.now()]
    if cycle is not None:
        conds.append("cycle_key = ?")
        params.append(int(cycle))

    avant = db.valeur(
        "SELECT COUNT(*) FROM alertes_baisse WHERE "
        + " AND ".join(conds[:1] + (["cycle_key = ?"] if cycle else [])),
        ([int(cycle)] if cycle else []),
    ) or 0

    db.executer(
        "UPDATE alertes_baisse SET acknowledged = TRUE, acknowledged_at = ? "
        "WHERE " + " AND ".join(conds),
        params,
    )
    return int(avant)


# ============================================================
# Alerte jaune — retard de facturation
# ============================================================

def detecter_retards() -> int:
    """Met à jour le journal des retards. Renvoie le nombre de nouvelles.

    Un secteur n'est en retard que s'il a dépassé sa date prévue SANS
    avoir été facturé. Dès qu'une facture est émise, l'alerte cesse
    d'être réactualisée mais reste dans le journal : elle témoigne du
    retard qui a eu lieu.
    """
    db.initialiser()
    nouvelles = 0
    maintenant = datetime.now()

    retenus = {"EN_RETARD"}
    if config.ALERTER_DEADLINE_PROCHE:
        retenus.add("DEADLINE_PROCHE")
    if config.ALERTER_SANS_PLANNING:
        retenus.add("PAS_DE_PLANNING")
    if config.ALERTER_FACTURATION_RETARD:
        retenus.add("A_FACTURER_EN_RETARD")

    for annee, mois in _cycles_a_surveiller():
        ck = cycles.cycle_key(annee, mois)

        try:
            lignes = queries.retards(annee, mois)
        except Exception:  # noqa: BLE001
            continue

        for s in lignes:
            if s.get("urgence") not in retenus:
                continue

            str_id = s.get("centre_code")
            sect_id = s.get("secteur_code")
            if str_id is None or sect_id is None:
                continue

            ligne_ex = db.ligne(
                "SELECT alert_id, urgence FROM alertes_retard "
                "WHERE cycle_key = ? AND STR_ID = ? AND SECT_ID = ?",
                [ck, str_id, sect_id],
            )
            existe = ligne_ex.get("alert_id")

            if existe is None:
                nid = (db.valeur(
                    "SELECT COALESCE(MAX(alert_id), 0) + 1 "
                    "FROM alertes_retard"
                ) or 1)
                db.executer("""
                    INSERT INTO alertes_retard
                      (alert_id, cycle_key, ANNEE, MOIS, STR_ID, SECT_ID,
                       deadline, jours_retard, parc, pct_saisie,
                       cloture, nb_factures, etat, urgence,
                       detected_at, updated_at, acknowledged, acknowledged_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,FALSE,NULL)
                """, [
                    nid, ck, annee, mois, str_id, sect_id,
                    s.get("deadline"), s.get("jours_retard"),
                    s.get("parc"), s.get("pct_saisie"),
                    s.get("cloture"), s.get("nb_factures"),
                    s.get("etat"), s.get("urgence"),
                    maintenant, maintenant,
                ])
                nouvelles += 1
            elif (ligne_ex.get("urgence") != "EN_RETARD"
                  and s.get("urgence") == "EN_RETARD"):
                # Deadline proche devenue retard : c'est un nouvel
                # événement, le bandeau doit revenir même si l'alerte
                # « deadline proche » a déjà été consultée.
                db.executer("""
                    UPDATE alertes_retard
                    SET deadline = ?, jours_retard = ?, pct_saisie = ?,
                        cloture = ?, nb_factures = ?, etat = ?, urgence = ?,
                        detected_at = ?, updated_at = ?,
                        acknowledged = FALSE, acknowledged_at = NULL
                    WHERE alert_id = ?
                """, [
                    s.get("deadline"), s.get("jours_retard"),
                    s.get("pct_saisie"), s.get("cloture"),
                    s.get("nb_factures"), s.get("etat"), s.get("urgence"),
                    maintenant, maintenant, existe,
                ])
                nouvelles += 1
            else:
                db.executer("""
                    UPDATE alertes_retard
                    SET jours_retard = ?, pct_saisie = ?, cloture = ?,
                        nb_factures = ?, etat = ?, urgence = ?, updated_at = ?
                    WHERE alert_id = ?
                """, [
                    s.get("jours_retard"), s.get("pct_saisie"),
                    s.get("cloture"), s.get("nb_factures"),
                    s.get("etat"), s.get("urgence"), maintenant, existe,
                ])

    return nouvelles


_SELECT_RETARD = """
    SELECT
        a.alert_id, a.cycle_key, a.ANNEE, a.MOIS,
        a.STR_ID AS centre_code,
        COALESCE(ce.CENTRE_LIB,
                 '(centre ' || CAST(a.STR_ID AS VARCHAR) || ')')
                                              AS centre_libelle,
        ce.ZONE_ID                            AS zone_id,
        a.SECT_ID AS secteur_code,
        COALESCE(NULLIF(TRIM(sec.SECT_CODE), ''),
                 CAST(a.SECT_ID AS VARCHAR))  AS secteur_num,
        COALESCE(sec.SECT_LIB,
                 '(secteur ' || CAST(a.SECT_ID AS VARCHAR) || ')')
                                              AS secteur_libelle,
        NULLIF(TRIM(regexp_replace(CAST(sec.MAT_RELEV AS VARCHAR),
                                   '\\.0+$', '')), '')
                                              AS matricule_releveur,
        a.deadline, a.jours_retard, a.parc, a.pct_saisie,
        a.cloture, a.nb_factures, a.etat, a.urgence,
        a.detected_at, a.updated_at, a.acknowledged, a.acknowledged_at
    FROM alertes_retard a
    LEFT JOIN ref_centre  ce  ON ce.CENTRE_ID = a.STR_ID
    LEFT JOIN ref_secteur sec ON sec.SECT_ID  = a.SECT_ID
"""


def lister_retards(
    only_unack: bool = False,
    cycle: int | None = None,
    str_id: int | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    conds, params = [], []
    if only_unack:
        conds.append("a.acknowledged = FALSE")
    if cycle is not None:
        conds.append("a.cycle_key = ?")
        params.append(int(cycle))
    if str_id is not None:
        conds.append("a.STR_ID = ?")
        params.append(int(str_id))
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    rows = db.lignes(
        f"{_SELECT_RETARD} {where} "
        f"ORDER BY a.jours_retard DESC NULLS LAST LIMIT {int(limit)}",
        params,
    )
    for r in rows:
        config.appliquer_sous_zone(r)
        r["cycle"] = cycles.libelle_key(int(r["cycle_key"]))
    return rows


def compter_retards(only_unack: bool = True,
                    cycle: int | None = None,
                    zone: int | None = None) -> dict:
    conds, params = [], []
    if only_unack:
        conds.append("acknowledged = FALSE")
    if cycle is not None:
        conds.append("cycle_key = ?")
        params.append(int(cycle))
    if zone is not None:
        _cs = config.centres_de_zone(zone)
        if _cs is not None:
            conds.append("STR_ID IN (" + ", ".join(str(x) for x in _cs) + ")")
        else:
            conds.append("STR_ID IN (SELECT CENTRE_ID FROM ref_centre WHERE ZONE_ID = ?)")
            params.append(int(zone))
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    # Bandeau = situation ACTUELLE, comme l'onglet Retards : une alerte
    # dont le secteur a été facturé depuis reste dans le journal (trace),
    # mais ne doit plus être comptée ni garder son ancienne urgence.
    if cycle is not None:
        try:
            return _compter_retards_actuels(int(cycle), where, params)
        except Exception:  # noqa: BLE001
            pass  # repli : comptage historique ci-dessous

    row = db.ligne(f"""
        SELECT
            COUNT(*)                                  AS n,
            COUNT(DISTINCT STR_ID)                    AS nb_centres,
            CAST(COALESCE(SUM(parc), 0) AS BIGINT)    AS parc_concerne,
            MAX(jours_retard)                         AS retard_max,
            COUNT(*) FILTER (WHERE urgence = 'EN_RETARD')       AS nb_en_retard,
            COUNT(*) FILTER (WHERE urgence = 'DEADLINE_PROCHE') AS nb_proches,
            MIN(deadline)                             AS deadline_min,
            MAX(detected_at)                          AS derniere
        FROM alertes_retard {where}
    """, params)
    return {
        "n": int(row.get("n") or 0),
        "nb_centres": int(row.get("nb_centres") or 0),
        "parc_concerne": int(row.get("parc_concerne") or 0),
        "retard_max": int(row.get("retard_max") or 0),
        "nb_en_retard": int(row.get("nb_en_retard") or 0),
        "nb_proches": int(row.get("nb_proches") or 0),
        "deadline_min": row.get("deadline_min"),
        "derniere": row.get("derniere"),
    }


A_TRAITER_RT = ("EN_RETARD", "DEADLINE_PROCHE")


def _compter_retards_actuels(cycle: int, where: str, params: list) -> dict:
    """Comptage du bandeau limité aux secteurs encore à traiter maintenant,
    avec leur urgence et leur retard du moment (queries.retards)."""
    annee, mois = divmod(cycle, 100)
    actuels = {}
    from src import perf   # perf : calcul gardé jusqu'au prochain passage du watcher
    for s in perf.memo(("retards", annee, mois), lambda: queries.retards(annee, mois)):
        if s.get("urgence") in A_TRAITER_RT:
            actuels[(s.get("centre_code"), s.get("secteur_code"))] = s
    rows = db.lignes(
        f"SELECT STR_ID, SECT_ID, parc, deadline, detected_at "
        f"FROM alertes_retard {where}", params)
    garde = []
    for r in rows:
        s = actuels.get((r["STR_ID"], r["SECT_ID"]))
        if s is not None:
            garde.append((r, s))
    deadlines = [s.get("deadline") or r.get("deadline") for r, s in garde]
    deadlines = [d for d in deadlines if d is not None]
    detections = [r.get("detected_at") for r, _ in garde if r.get("detected_at")]
    return {
        "n": len(garde),
        "nb_centres": len({r["STR_ID"] for r, _ in garde}),
        "parc_concerne": int(sum((s.get("parc") or r.get("parc") or 0) for r, s in garde)),
        "retard_max": int(max([(s.get("jours_retard") or 0) for _, s in garde] or [0])),
        "nb_en_retard": sum(1 for _, s in garde if s.get("urgence") == "EN_RETARD"),
        "nb_proches": sum(1 for _, s in garde if s.get("urgence") == "DEADLINE_PROCHE"),
        "deadline_min": min(deadlines) if deadlines else None,
        "derniere": max(detections) if detections else None,
    }


def acquitter_retard(alert_id: int) -> int:
    db.executer("""
        UPDATE alertes_retard
        SET acknowledged = TRUE, acknowledged_at = ?
        WHERE alert_id = ? AND acknowledged = FALSE
    """, [datetime.now(), int(alert_id)])
    return 1


def acquitter_tout_retard(cycle: int | None = None) -> int:
    params: list = [datetime.now()]
    where = "acknowledged = FALSE"
    if cycle is not None:
        where += " AND cycle_key = ?"
        params.append(int(cycle))

    avant = db.valeur(
        "SELECT COUNT(*) FROM alertes_retard WHERE acknowledged = FALSE"
        + (" AND cycle_key = ?" if cycle is not None else ""),
        ([int(cycle)] if cycle is not None else []),
    ) or 0

    db.executer(
        "UPDATE alertes_retard SET acknowledged = TRUE, acknowledged_at = ? "
        f"WHERE {where}",
        params,
    )
    return int(avant)
