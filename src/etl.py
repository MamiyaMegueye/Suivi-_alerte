"""
Chargement Oracle → base locale.

Deux modes :

  complet       toutes les tables sur `HISTORIQUE_MOIS` mois.
                À lancer une fois à l'installation, puis quand on veut
                élargir l'historique.

  rapide        CLOTURE_SECTEUR seule, sur les cycles récents.
                C'est ce que fait le watcher à chaque tick : la table est
                petite et c'est elle qui porte les volumes facturés.

Usage, depuis le dossier de l'application :

    python -m src.etl                 # chargement complet
    python -m src.etl --rapide        # CLOTURE_SECTEUR seule
    python -m src.etl --mois 36       # historique plus profond
"""

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime

from src import alerts, config, cycles, db, extractor


# Un seul chargement à la fois : la veille automatique et le bouton
# « Rafraîchir » écrivaient en même temps dans la base (erreur
# « Duplicate key run_id »). Réentrant : complet() peut appeler les
# étapes qui le prennent aussi.
verrou = threading.RLock()


class ChargementEnCours(RuntimeError):
    """Un chargement Oracle tourne déjà."""


def cycle_min(mois: int | None = None) -> int:
    """Clé de cycle la plus ancienne à charger.

    On part d'aujourd'hui et on remonte de `mois` mois. 24 par défaut :
    il faut novembre et décembre de l'année précédente pour que janvier
    et février de l'année courante aient leur cycle N-2.
    """
    n = mois if mois is not None else config.HISTORIQUE_MOIS
    a, m = datetime.now().year, datetime.now().month
    total = a * 12 + (m - 1) - n
    return (total // 12) * 100 + (total % 12) + 1


def _etape(nom: str, fn, *a, verbeux: bool = True):
    debut = datetime.now()
    try:
        n = fn(*a)
    except Exception as exc:  # noqa: BLE001
        db.journaliser(nom, 0, debut, datetime.now(), "ERREUR", str(exc))
        if verbeux:
            print(f"  {nom:22} ÉCHEC — {str(exc).splitlines()[0][:110]}",
                  flush=True)
        raise
    fin = datetime.now()
    db.journaliser(nom, n, debut, fin, "OK")
    if verbeux:
        print(f"  {nom:22} {n:>10,} lignes  "
              f"({(fin - debut).total_seconds():.1f}s)", flush=True)
    return n


# ============================================================
# Chargements
# ============================================================

def charger_cloture(cmin: int) -> int:
    return db.upsert(extractor.cloture_secteur(cmin),
                     "raw_cloture_secteur", "IDCLOTURE")


def charger_factures(cmin: int) -> int:
    return db.upsert(extractor.facture_m(cmin), "raw_facture_m", "FACT_ID")


def charger_factures_cycles_surveilles() -> int:
    """Factures des cycles surveillés par la détection (les 2 derniers)."""
    cles = alerts._cycles_a_surveiller()
    return db.upsert(extractor.facture_m_cycles(cles), "raw_facture_m",
                     "FACT_ID")


def charger_releves(cmin: int) -> int:
    return db.upsert(extractor.releve(cmin), "raw_releve", "REL_ID")


def charger_abonnements() -> int:
    return db.upsert(extractor.abonnement(), "raw_abonnement", "ABN_ID")


def charger_compteurs() -> int:
    return db.upsert(extractor.compteur(), "raw_compteur", "ID")


def charger_planification() -> int:
    """Planning de relève et dates prévues. Petites tables."""
    n = db.remplacer(extractor.planif_releve(), "raw_planif_releve")
    annee_min = datetime.now().year - 1
    n += db.upsert(extractor.planif_detaille(annee_min),
                   "raw_planif_detaille", "IDDET")
    return n


def charger_references() -> int:
    n = db.remplacer(extractor.ref_centre(), "ref_centre")
    n += db.remplacer(extractor.ref_secteur(), "ref_secteur")
    # Non bloquant : sans libellés d'état, l'application fonctionne (repli
    # dans queries.py) et le reste du chargement doit continuer.
    try:
        n += db.remplacer(extractor.ref_comptage(), "ref_comptage")
    except Exception as exc:  # noqa: BLE001
        print(f"  ref_comptage ignorée — {str(exc).splitlines()[0][:110]}",
              flush=True)
    return n


# ============================================================
# Orchestration
# ============================================================

def complet(mois: int | None = None, verbeux: bool = True) -> dict:
    """Charge tout. C'est le chargement d'installation."""
    with verrou:
        return _complet(mois, verbeux)


def _complet(mois: int | None, verbeux: bool) -> dict:
    db.initialiser()
    cmin = cycle_min(mois)
    if verbeux:
        print(f"Chargement complet depuis le cycle "
              f"{cycles.libelle_key(cmin)}", flush=True)
        print(f"Base locale : {config.DB_PATH}", flush=True)

    t0 = datetime.now()
    _etape("references", charger_references, verbeux=verbeux)
    _etape("planification", charger_planification, verbeux=verbeux)
    _etape("cloture_secteur", charger_cloture, cmin, verbeux=verbeux)
    _etape("abonnements", charger_abonnements, verbeux=verbeux)
    _etape("compteurs", charger_compteurs, verbeux=verbeux)
    _etape("factures", charger_factures, cmin, verbeux=verbeux)
    _etape("releves", charger_releves, cmin, verbeux=verbeux)

    nb = alerts.detecter()
    nbr = alerts.detecter_retards()
    if verbeux:
        print(f"\nAlertes de baisse détectées   : {nb}", flush=True)
        print(f"Alertes de retard détectées   : {nbr}", flush=True)
        print(f"Terminé en {(datetime.now() - t0).total_seconds():.0f}s",
              flush=True)
    return {"cycle_min": cmin, "alertes": nb, "alertes_retard": nbr}


def _references_manquantes() -> bool:
    r = db.lignes("""
        SELECT
          (SELECT COUNT(DISTINCT cs.STR_ID) FROM raw_cloture_secteur cs
           LEFT JOIN ref_centre ce ON ce.CENTRE_ID = cs.STR_ID
           WHERE ce.CENTRE_ID IS NULL)
        + (SELECT COUNT(DISTINCT cs.SECT_ID) FROM raw_cloture_secteur cs
           LEFT JOIN ref_secteur se ON se.SECT_ID = cs.SECT_ID
           WHERE se.SECT_ID IS NULL) AS n
    """)
    return bool(r and r[0]["n"])


def rapide(verbeux: bool = False) -> dict:
    """CLOTURE_SECTEUR + planification, puis détection. Appelé par le
    watcher à chaque tick.

    La planification est rechargée elle aussi : elle est minuscule, et
    une date prévue corrigée dans Oracle doit se répercuter tout de
    suite sur les alertes de retard.
    """
    with verrou:
        return _rapide(verbeux)


def _rapide(verbeux: bool) -> dict:
    db.initialiser()
    # Six mois suffisent : au-delà, les volumes ne bougent plus.
    cmin = cycle_min(6)
    n = _etape("cloture_secteur", charger_cloture, cmin, verbeux=verbeux)
    # Centres ou secteurs inconnus (ex. zone ajoutée dans ZONE_IDS sans
    # chargement complet) : on recharge les libellés, sinon l'écran
    # affiche « (centre 24) » et « (zone inconnue) ». Non bloquant.
    try:
        if _references_manquantes():
            _etape("references", charger_references, verbeux=verbeux)
    except Exception:  # noqa: BLE001
        pass
    try:
        _etape("planification", charger_planification, verbeux=verbeux)
    except Exception:  # noqa: BLE001
        # Non bloquant : les baisses restent détectables sans planning.
        pass
    # Factures des cycles en cours, à chaque passage : c'est leur
    # émission qui déclenche l'alerte rouge. Non bloquant.
    if config.WATCHER_FACTURES_COURANT:
        try:
            _etape("factures_courant", charger_factures_cycles_surveilles,
                   verbeux=verbeux)
        except Exception:  # noqa: BLE001
            pass
    nb = alerts.detecter()
    nbr = alerts.detecter_retards()
    return {"lignes": n, "nouvelles_alertes": nb,
            "nouvelles_alertes_retard": nbr}


def _a_recharger(etape: str, table: str, heures: int = 24) -> bool:
    """Vrai si la table est vide ou si son dernier chargement réussi date
    de plus de `heures` heures (21/11/2026 : abonnements et compteurs
    n'étaient chargés qu'à l'installation, n° compteur vide partout)."""
    try:
        if not db.valeur(f"SELECT COUNT(*) FROM {table}"):
            return True
        fin = db.valeur(
            "SELECT MAX(fin) FROM etl_runs WHERE etape = ? AND statut = 'OK'",
            [etape])
        return fin is None or (datetime.now() - fin).total_seconds() > heures * 3600
    except Exception:  # noqa: BLE001
        return True


def factures_recentes(verbeux: bool = False) -> dict:
    """Factures et relevés des cycles récents — pour le détail par
    référence. Plus lourd, donc appelé un tick sur N."""
    with verrou:
        db.initialiser()
        cmin = cycle_min(6)
        nf = _etape("factures", charger_factures, cmin, verbeux=verbeux)
        nr = _etape("releves", charger_releves, cmin, verbeux=verbeux)
        # Abonnements (secteur actuel) et compteurs (n° gravé) : une fois
        # par jour, ou tout de suite si la table est vide. Non bloquant.
        for nom, fn, table in (("abonnements", charger_abonnements, "raw_abonnement"),
                               ("compteurs", charger_compteurs, "raw_compteur")):
            try:
                if _a_recharger(nom, table):
                    _etape(nom, fn, verbeux=verbeux)
            except Exception:  # noqa: BLE001
                pass
        return {"factures": nf, "releves": nr}


def factures_puis_detections() -> dict:
    """Factures et relevés, puis les deux détections, d'un seul tenant."""
    with verrou:
        factures_recentes()
        return {"nouvelles_alertes": alerts.detecter(),
                "nouvelles_alertes_retard": alerts.detecter_retards()}


def rafraichir(complet: bool = False) -> dict:
    """Chargement demandé depuis l'interface. Refusé tout de suite si
    la veille automatique est déjà en train de charger."""
    if not verrou.acquire(blocking=False):
        raise ChargementEnCours(
            "Un chargement Oracle est déjà en cours (veille automatique). "
            "Les données se mettront à jour toutes seules à la fin, "
            "inutile de cliquer à nouveau."
        )
    try:
        r = rapide()
        if complet:
            _etape("references", charger_references, verbeux=False)
            r.update(factures_recentes())
        return r
    finally:
        verrou.release()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Chargement Oracle → base locale Alerte Baisse."
    )
    ap.add_argument("--rapide", action="store_true",
                    help="CLOTURE_SECTEUR seule (ce que fait le watcher)")
    ap.add_argument("--mois", type=int, default=None,
                    help=f"profondeur d'historique en mois "
                         f"(défaut {config.HISTORIQUE_MOIS})")
    args = ap.parse_args()

    if not config.oracle_configure():
        print(
            "Identifiants Oracle absents.\n\n"
            f"Crée le fichier {config.ENV_FILE} en copiant .env.example, "
            "puis renseigne ORACLE_USER et ORACLE_PASSWORD.",
            file=sys.stderr,
        )
        return 2

    try:
        if args.rapide:
            r = rapide(verbeux=True)
            print(f"CLOTURE_SECTEUR : {r['lignes']} lignes")
            print(f"  nouvelles alertes de baisse : "
                  f"{r['nouvelles_alertes']}")
            print(f"  nouvelles alertes de retard : "
                  f"{r['nouvelles_alertes_retard']}")
        else:
            complet(mois=args.mois, verbeux=True)
    except Exception as exc:  # noqa: BLE001
        print(f"\nÉchec : {exc}", file=sys.stderr)
        return 1
    finally:
        db.fermer()
    return 0


if __name__ == "__main__":
    sys.exit(main())
