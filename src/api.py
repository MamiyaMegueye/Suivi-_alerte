"""
API + interface — Alerte Baisse de Facturation (SNDE).

Application autonome : sa propre connexion Oracle, sa propre base DuckDB,
son propre watcher temps réel. Elle ne lit ni n'écrit rien du dashboard.

Lancement, depuis le dossier de l'application :
    python -m uvicorn src.api:app --host 0.0.0.0 --port 8020

L'interface est servie par cette même application sur
http://localhost:8020 — il n'y a pas de second serveur à démarrer.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import (
    FileResponse, JSONResponse, Response, StreamingResponse,
)

from src import (
    alerts, config, cycles, db, etl, excel, oracle_client, queries, sse,
    watcher,
)

STATIC = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Le schéma doit exister avant le premier appel de l'interface.
    try:
        db.initialiser()
    except Exception:  # noqa: BLE001
        pass
    await watcher.demarrer()
    yield
    await watcher.arreter()
    db.fermer()


app = FastAPI(
    title="SNDE — Alerte Baisse de Facturation",
    version="2.0.0",
    description=(
        "Détection des secteurs dont le volume facturé baisse par rapport "
        "au cycle N-2 (même parité de relève), puis identification des "
        "références responsables de la baisse. Application autonome."
    ),
    lifespan=lifespan,
)


# ============================================================
# Garde-fous
# ============================================================

def _garde(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except db.BaseIndisponible as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _cycle(cycle: int | None, annee: int | None, mois: int | None):
    """Accepte ?cycle=202609 ou ?annee=2026&mois=9."""
    if cycle is not None:
        return cycles.depuis_key(int(cycle))
    if annee is not None and mois is not None:
        return int(annee), int(mois)
    defaut = _garde(queries.liste_cycles)["defaut"]
    if defaut is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Aucun cycle dans la base locale. Lance d'abord le "
                "chargement : python -m src.etl"
            ),
        )
    return cycles.depuis_key(int(defaut))


# ============================================================
# Interface
# ============================================================

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/healthz", tags=["meta"])
def healthz() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/api/etat", tags=["meta"])
def etat() -> dict:
    """Fraîcheur des données, état du watcher, état d'Oracle."""
    info: dict[str, Any] = {"base": None, "erreur": None}
    try:
        info["base"] = db.info_source()
    except Exception as exc:  # noqa: BLE001
        info["erreur"] = str(exc)[:300]

    info["watcher"] = dict(watcher.etat)
    info["oracle"] = {
        "configure": config.oracle_configure(),
        "dsn": config.ORACLE_DSN if config.oracle_configure() else None,
    }
    info["clients_sse"] = sse.diffuseur.nb_clients
    return info


@app.get("/api/oracle/test", tags=["meta"])
def oracle_test() -> dict:
    """Diagnostic de connexion. Ne renvoie jamais les identifiants."""
    return oracle_client.tester()


# ============================================================
# Données
# ============================================================

@app.get("/api/cycles", tags=["données"])
def api_cycles() -> dict:
    d = _garde(queries.liste_cycles)
    # Années disponibles, pour le sélecteur « cycle de cette année ».
    d["annees"] = sorted(
        {int(c["annee"]) for c in d["cycles"]}, reverse=True
    )
    return d


@app.get("/api/synthese", tags=["données"])
def api_synthese(
    cycle: int | None = Query(None, description="clé 202609"),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    population: str = Query(
        "bimestriel", pattern="^(bimestriel|mensuel)$",
        description="bimestriel (N-2) ou mensuel (gros consommateurs, N-1)",
    ),
    zone: int | None = Query(None, ge=1, le=9, description="1 Intérieur, 2 Nouakchott, 3 Nouadhibou"),
) -> dict:
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.synthese, a, m, population, zone)


@app.get("/api/centres", tags=["données"])
def api_centres(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    population: str = Query(
        "bimestriel", pattern="^(bimestriel|mensuel)$",
        description="bimestriel (N-2) ou mensuel (gros consommateurs, N-1)",
    ),
    zone: int | None = Query(None, ge=1, le=9, description="1 Intérieur, 2 Nouakchott, 3 Nouadhibou"),
) -> list[dict]:
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.centres, a, m, population, zone)


@app.get("/api/secteurs", tags=["données"])
def api_secteurs(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    str_id: int | None = Query(None, description="filtre centre"),
    baisse_min_pct: float = Query(
        config.BAISSE_MIN_PCT_DEFAUT, ge=0, le=100,
        description="0 = toutes les baisses",
    ),
    toutes: bool = Query(
        False, description="true = inclure aussi hausses et stables",
    ),
    population: str = Query(
        "bimestriel", pattern="^(bimestriel|mensuel)$",
        description="bimestriel (N-2) ou mensuel (gros consommateurs, N-1)",
    ),
    zone: int | None = Query(None, ge=1, le=9, description="1 Intérieur, 2 Nouakchott, 3 Nouadhibou"),
) -> list[dict]:
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.secteurs, a, m, str_id, baisse_min_pct, not toutes,
                  population, zone)


@app.get("/api/references", tags=["données"], deprecated=True)
def api_references(
    str_id: int = Query(...),
    sect_id: int = Query(...),
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
) -> dict:
    """Conservé comme alias de /api/abonnes, qui est plus complet."""
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.abonnes, a, m, str_id, sect_id)


@app.get("/api/centres-list", tags=["données"])
def api_centres_list(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
) -> list[dict]:
    """Centres du cycle — alimente le filtre par centre."""
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.centres_list, a, m)


@app.get("/api/secteurs-list", tags=["données"])
def api_secteurs_list(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    str_id: int | None = Query(None, description="restreint à un centre"),
) -> list[dict]:
    """Secteurs du cycle — alimente le filtre par secteur."""
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.secteurs_list, a, m, str_id)


# ============================================================
# Avancement de la saisie
# ============================================================

@app.get("/api/avancement", tags=["avancement"])
def api_avancement(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    str_id: int | None = Query(None, description="filtre centre"),
    sect_id: int | None = Query(None, description="filtre secteur"),
    statut: str | None = Query(
        None,
        description="NON_COMMENCE, EN_COURS, SAISIE_FINIE, CLOTURE, FACTURE",
    ),
) -> list[dict]:
    """Avancement secteur par secteur, du premier relevé à la clôture."""
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.avancement, a, m, str_id, sect_id, statut)


@app.get("/api/avancement/jours", tags=["avancement"])
def api_avancement_jours(
    str_id: int = Query(...),
    sect_id: int = Query(...),
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
) -> dict:
    """Progression quotidienne de la saisie d'un secteur."""
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.avancement_jours, a, m, str_id, sect_id)


# ============================================================
# Détail par abonné
# ============================================================

@app.get("/api/abonnes", tags=["données"])
def api_abonnes(
    str_id: int = Query(..., description="code centre (STR_ID)"),
    sect_id: int = Query(..., description="code secteur (SECT_ID)"),
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
) -> dict:
    """Ligne par abonné du secteur : relevé, facture, volumes, écart.

    Assis sur le parc du secteur, donc exploitable dès le début de la
    saisie — avant toute facturation.
    """
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.abonnes, a, m, str_id, sect_id)


# ============================================================
# Alertes — bandeau rouge persistant
# ============================================================

@app.get("/api/alertes", tags=["alertes"])
def api_alertes(
    only_unack: bool = Query(False),
    cycle: int | None = Query(None),
    str_id: int | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
) -> list[dict]:
    return _garde(alerts.lister, only_unack, cycle, str_id, limit)


@app.get("/api/alertes/count", tags=["alertes"])
def api_alertes_count(
    only_unack: bool = Query(True),
    cycle: int | None = Query(None),
) -> dict:
    return _garde(alerts.compter, only_unack, cycle)


@app.post("/api/alertes/acknowledge", tags=["alertes"])
def api_ack(alert_id: int = Query(...)) -> dict:
    _garde(alerts.acquitter, alert_id)
    return {"ok": True, "restant": _garde(alerts.compter, True)["n"]}


@app.post("/api/alertes/acknowledge-all", tags=["alertes"])
def api_ack_all(cycle: int | None = Query(None)) -> dict:
    """Consulter les alertes les fait disparaître du bandeau rouge."""
    n = _garde(alerts.acquitter_tout, cycle)
    return {
        "ok": True,
        "acquittees": n,
        "restant": _garde(alerts.compter, True)["n"],
    }


# ============================================================
# Retard de facturation — bandeau jaune
# ============================================================

@app.get("/api/retards", tags=["retards"])
def api_retards(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    str_id: int | None = Query(None),
    sect_id: int | None = Query(None),
    urgence: str | None = Query(
        None,
        description="A_TEMPS, DEADLINE_PROCHE, EN_RETARD, FACTURE, "
                    "FACTURE_EN_RETARD, PAS_DE_PLANNING",
    ),
    seulement_retards: bool = Query(False),
) -> list[dict]:
    """Dates prévues de facturation et retards, secteur par secteur."""
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.retards, a, m, str_id, sect_id, urgence,
                  seulement_retards)


@app.get("/api/retards/synthese", tags=["retards"])
def api_retards_synthese(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    str_id: int | None = Query(None),
) -> dict:
    a, m = _cycle(cycle, annee, mois)
    return _garde(queries.retards_synthese, a, m, str_id)


@app.get("/api/alertes-retard", tags=["retards"])
def api_alertes_retard(
    only_unack: bool = Query(False),
    cycle: int | None = Query(None),
    str_id: int | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
) -> list[dict]:
    return _garde(alerts.lister_retards, only_unack, cycle, str_id, limit)


@app.get("/api/alertes-retard/count", tags=["retards"])
def api_alertes_retard_count(
    only_unack: bool = Query(True),
    cycle: int | None = Query(None),
) -> dict:
    return _garde(alerts.compter_retards, only_unack, cycle)


@app.post("/api/alertes-retard/acknowledge", tags=["retards"])
def api_ack_retard(alert_id: int = Query(...)) -> dict:
    _garde(alerts.acquitter_retard, alert_id)
    return {"ok": True, "restant": _garde(alerts.compter_retards, True)["n"]}


@app.post("/api/alertes-retard/acknowledge-all", tags=["retards"])
def api_ack_all_retard(cycle: int | None = Query(None)) -> dict:
    n = _garde(alerts.acquitter_tout_retard, cycle)
    return {
        "ok": True,
        "acquittees": n,
        "restant": _garde(alerts.compter_retards, True)["n"],
    }


@app.get("/api/stream/alertes", tags=["alertes"])
async def stream_alertes() -> StreamingResponse:
    """Flux SSE : « connecte », « nouvelle_baisse », « nouveau_retard »,
    « maj », « erreur »."""
    file = sse.diffuseur.connecter()
    return StreamingResponse(
        sse.diffuseur.flux(file),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Évite la mise en tampon par un proxy intermédiaire.
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# Rafraîchissement manuel
# ============================================================

@app.post("/api/refresh", tags=["données"])
async def refresh(
    complet: bool = Query(
        False, description="true = recharge aussi factures et relevés",
    ),
) -> dict:
    """Déclenche un chargement Oracle immédiat, sans attendre le watcher."""
    if not config.oracle_configure():
        raise HTTPException(
            status_code=503,
            detail=(
                "Identifiants Oracle absents du .env — impossible de "
                "rafraîchir. L'interface continue de fonctionner sur les "
                "données déjà chargées."
            ),
        )
    try:
        r = await asyncio.to_thread(etl.rafraichir, complet)
    except etl.ChargementEnCours as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=(
                "Échec du chargement Oracle : "
                f"{str(exc).splitlines()[0][:200]}"
            ),
        ) from exc

    if r.get("nouvelles_alertes"):
        c = alerts.compter(True)
        await sse.diffuseur.publier("nouvelle_baisse", {
            "nouvelles": r["nouvelles_alertes"],
            "total_non_lues": c["n"],
            "volume_perdu": c["volume_perdu"],
            "baisse_totale": c["baisse_totale"],
            "nb_centres": c["nb_centres"],
        })
    if r.get("nouvelles_alertes_retard"):
        cr = alerts.compter_retards(True)
        await sse.diffuseur.publier("nouveau_retard", {
            "nouvelles": r["nouvelles_alertes_retard"],
            "total_non_lues": cr["n"],
            "retard_max": cr["retard_max"],
            "nb_centres": cr["nb_centres"],
        })
    return r


# ============================================================
# Exports Excel
# ============================================================

_COLS_SECTEUR = {
    "zone_lib": "Zone",
    "centre_code": "Code centre",
    "centre_libelle": "Centre",
    "secteur_num": "N° secteur",
    "secteur_libelle": "Secteur",
    "etat": "État cycle",
    "fiabilite": "Comparaison",
    "parc_n": "Parc",
    "saisis_n": "Saisis",
    "pct_saisie_n": "% saisie",
    "nb_factures_n": "Factures N",
    "nb_factures_p": "Factures N-2",
    "nb_abonnes_compares": "Abonnés comparés",
    "nb_perdus": "Abonnés perdus",
    "nb_ajoutes": "Abonnés ajoutés",
    "vol_perdus": "Volume des perdus (m3)",
    "vol_total_p": "Volume total N-2 (m3)",
    "vol_total_n": "Volume total N (m3)",
    "vol_total_hors_grp_p": "Volume total N-2 sans groupés (m3)",
    "vol_total_hors_grp_n": "Volume total N sans groupés (m3)",
    "ecart_total": "Écart volume total (m3)",
    "ecart_total_pct": "Écart volume total (%)",
    "cause": "Cause principale",
    "gravite": "Gravité",
}

_COLS_AVANCEMENT = {
    "zone_lib": "Zone",
    "centre_code": "Code centre",
    "centre_libelle": "Centre",
    "secteur_num": "N° secteur",
    "secteur_libelle": "Secteur",
    "matricule_releveur": "Releveur",
    "etat": "État",
    "parc": "Parc",
    "saisis_declare": "Saisis déclarés",
    "saisis_reel": "Saisis réels",
    "restant": "Restant",
    "pct_declare": "% saisie déclaré",
    "pct_reel": "% saisie réel",
    "saisis_estimes": "Dont estimés",
    "nb_jours_actifs": "Jours de relève",
    "premier_releve": "1er relevé",
    "dernier_releve": "Dernier relevé",
    "cloture": "Clôturé",
    "date_cloture": "Date clôture",
    "nb_factures": "Factures",
    "vol_total_p": "Volume total N-2 (m3)",
    "vol_total_n": "Volume total N (m3)",
    "vol_p": "Volume comparé N-2 (m3)",
    "vol_n": "Volume comparé N (m3)",
    "ecart": "Écart (m3)",
    "ecart_pct": "Écart (%)",
}

_COLS_REF = {
    "reference": "Référence",
    "ancienne_reference": "Ancienne réf.",
    "adresse": "Adresse",
    "complement_adresse": "Complément adresse",
    "quartier": "Quartier",
    "num_compteur": "N° compteur",
    "ref_compteur": "Réf. compteur",
    "statut_saisie": "Statut saisie",
    "date_releve": "Date relevé N",
    "index_debut": "Index début N",
    "index_fin": "Index fin N",
    "index_fin_p": "Index fin N-2",
    "conso_relevee": "Conso relevée N (m3)",
    "etat_compteur": "État compteur N",
    "releve_valide": "Relevé validé",
    "releve_estimatif": "Relevé estimatif",
    "type_facture": "Type facture N",
    "num_facture": "N° facture N",
    "date_facture": "Date facture N",
    "vol_p_cycle": "Volume N-2 (m3)",
    "vol_n": "Volume N (m3)",
    "ecart": "Écart (m3)",
    "ecart_pct": "Écart (%)",
    "part_baisse": "Part de la baisse (%)",
    "gravite": "Situation",
    "montant": "Montant N",
}

# Export des références d'un secteur : colonnes réduites, à la demande
# du service (12/11/2026).
_COLS_REF_EXPORT = {
    "centre_libelle": "Centre",
    "secteur_num": "Secteur",
    "reference": "Référence",
    "adresse": "Adresse",
    "num_compteur": "N° compteur",
    "index_debut": "Index début",
    "index_fin": "Index fin",
    "vol_p": "Volume avant (m3)",
    "vol_n": "Volume après (m3)",
    "ecart": "Écart (m3)",
}

_COLS_CENTRE = {
    "zone_lib": "Zone",
    "centre_code": "Code centre",
    "centre_libelle": "Centre",
    "nb_secteurs": "Secteurs",
    "nb_en_baisse": "Secteurs en baisse",
    "nb_disparus": "Secteurs disparus",
    "nb_perdus": "Abonnés perdus",
    "vol_perdus": "Volume des perdus (m3)",
    "vol_p": "Volume N-2 (m3)",
    "vol_n": "Volume N (m3)",
    "ecart": "Écart (m3)",
    "ecart_pct": "Écart (%)",
    "volume_perdu": "Volume perdu abonnés constants (m3)",
    "baisse_totale": "Baisse du volume total (m3)",
    "gravite": "Gravité",
}

_COLS_RETARD = {
    "zone_lib": "Zone",
    "centre_code": "Code centre",
    "centre_libelle": "Centre",
    "secteur_num": "N° secteur",
    "secteur_libelle": "Secteur",
    "matricule_releveur": "Releveur",
    "urgence": "Situation",
    "etat": "État cycle",
    "parc": "Parc",
    "saisis": "Saisis",
    "restant": "Restant",
    "pct_saisie": "% avancement saisie",
    "deadline": "Date prevue facturation",
    "jours_retard": "Jours de retard",
    "marge_adjoint": "Marge adjoint (j)",
    "deadline_releveur": "Deadline releveur",
    "jours_restants": "Jours restants releveur",
    "date_debut_semaine": "DATEDEBUTSEMAINE",
    "date_debut_fact": "DATEDEBUTFACT",
    "cloture": "Cloture",
    "date_cloture": "Date cloture",
    "nb_factures": "Factures emises",
    "premiere_facture": "1re facture",
}

_COLS_ALERTE_RETARD = {
    "zone_lib": "Zone",
    "centre_libelle": "Centre",
    "secteur_num": "N° secteur",
    "secteur_libelle": "Secteur",
    "cycle": "Cycle",
    "urgence": "Situation",
    "etat": "État cycle",
    "parc": "Parc",
    "pct_saisie": "% avancement saisie",
    "deadline": "Date prevue",
    "jours_retard": "Jours de retard",
    "nb_factures": "Factures emises",
    "detected_at": "Détectée le",
    "acknowledged": "Consultée",
    "acknowledged_at": "Consultée le",
}

_COLS_ALERTE = {
    "zone_lib": "Zone",
    "centre_libelle": "Centre",
    "secteur_num": "N° secteur",
    "secteur_libelle": "Secteur",
    "cycle": "Cycle",
    "cycle_precedent": "Comparé à",
    "comparaison": "Comparaison",
    "etat": "État",
    "vol_total_p": "Volume total N-2 (m3)",
    "vol_total_n": "Volume total N (m3)",
    "ecart_total": "Écart total (m3)",
    "ecart_total_pct": "Écart total (%)",
    "vol_p": "Volume comparé N-2 (m3)",
    "vol_n": "Volume comparé N (m3)",
    "ecart": "Écart comparé (m3)",
    "ecart_pct": "Écart comparé (%)",
    "gravite": "Gravité",
    "detected_at": "Détectée le",
    "acknowledged": "Consultée",
    "acknowledged_at": "Consultée le",
}


def _mise_en_forme(rows: list[dict], mapping: dict[str, str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame({k: [] for k in mapping.values()})
    presentes = [c for c in mapping if c in df.columns]
    return df[presentes].rename(columns=mapping)


def _xlsx(contenu: bytes, nom: str) -> Response:
    # Response plutôt que StreamingResponse : avec Content-Length, le
    # navigateur affiche la progression et ne coupe pas le téléchargement.
    return Response(
        content=contenu,
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{nom}"',
            "Content-Length": str(len(contenu)),
        },
    )


@app.get("/api/export/secteurs", tags=["export"])
def export_secteurs(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    str_id: int | None = Query(None),
    baisse_min_pct: float = Query(config.BAISSE_MIN_PCT_DEFAUT, ge=0, le=100),
    toutes: bool = Query(False),
    population: str = Query(
        "bimestriel", pattern="^(bimestriel|mensuel)$",
        description="bimestriel (N-2) ou mensuel (gros consommateurs, N-1)",
    ),
    zone: int | None = Query(None, ge=1, le=9, description="1 Intérieur, 2 Nouakchott, 3 Nouadhibou"),
) -> Response:
    a, m = _cycle(cycle, annee, mois)
    syn = _garde(queries.synthese, a, m, population, zone)
    cen = _garde(queries.centres, a, m, population, zone)
    sec = _garde(queries.secteurs, a, m, str_id, baisse_min_pct, not toutes,
                 population, zone)
    alt = _garde(alerts.lister, False, cycles.cycle_key(a, m), str_id, 2000)
    gros = population == "mensuel"
    # Journal : seulement les alertes de cette population.
    alt = [r for r in alt if (r.get("comparaison") == "N-1") == gros]

    base = (
        (f"Gros consommateurs — cycle {syn['cycle']} comparé au mois "
         f"précédent {syn['cycle_precedent']} (facturation mensuelle)"
         if gros else
         f"Baisse de facturation — cycle {syn['cycle']} comparé au cycle "
         f"{syn['cycle_precedent']} (même parité de relève)")
        + f" — généré le {excel.horodatage()}"
    )

    def _n1(cols: dict[str, str]) -> dict[str, str]:
        return ({k: v.replace("N-2", "N-1") for k, v in cols.items()}
                if gros else cols)

    feuilles = [
        ("Par centre", f"Volume facturé par centre — {base}",
         _mise_en_forme(cen, _n1(_COLS_CENTRE))),
        ("Par secteur", f"Secteurs en baisse — {base}",
         _mise_en_forme(sec, _n1(_COLS_SECTEUR))),
    ]
    if alt:
        feuilles.append((
            "Journal des alertes",
            f"Alertes levées sur ce cycle — {base}",
            _mise_en_forme(alt, _COLS_ALERTE),
        ))

    return _xlsx(
        excel.classeur(feuilles),
        (f"gros_consommateurs_{a}_{m:02d}.xlsx" if gros
         else f"baisse_facturation_{a}_{m:02d}.xlsx"),
    )


def _abonnes_en_baisse(a: int, m: int, str_id: int | None,
                       population: str,
                       sects: set[int] | None = None) -> tuple[int, list[dict]]:
    """Abonnés en baisse de chaque secteur en baisse. population :
    bimestriel, mensuel ou tous (les deux, chacun à sa référence)."""
    pops = ("bimestriel", "mensuel") if population == "tous" else (population,)
    secs: list[dict] = []
    for pop in pops:
        secs += _garde(queries.secteurs, a, m, str_id, 0.0, True, pop)
    if sects:
        secs = [x for x in secs if int(x["secteur_code"]) in sects]
    lignes: list[dict] = []
    for sct in secs:
        d = _garde(queries.abonnes, a, m, sct["centre_code"], sct["secteur_code"])
        for r in d["lignes"]:
            if (r.get("ecart") or 0) < 0:
                r["centre_code"] = sct["centre_code"]
                r["secteur_code"] = sct["secteur_code"]
                r["centre_libelle"] = sct["centre_libelle"]
                r["secteur_num"] = sct["secteur_num"]
                r["ecart_secteur"] = sct.get("ecart")
                r["comparaison"] = sct.get("comparaison")
                lignes.append(r)
    lignes.sort(key=lambda r: (str(r.get("centre_libelle")),
                               str(r.get("secteur_num")), r.get("ecart") or 0))
    return len(secs), lignes


_POP_LISTE = Query("tous", pattern="^(tous|bimestriel|mensuel)$")


def _sects(sect_id: int | None, sects: str | None) -> set[int] | None:
    """sect_id=…  ou  sects=12,34,56 (secteurs du journal des alertes)."""
    if sect_id is not None:
        return {int(sect_id)}
    if sects:
        return {int(x) for x in sects.split(",") if x.strip().isdigit()}
    return None


@app.get("/api/abonnes-baisse", tags=["données"])
def api_abonnes_baisse(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    str_id: int | None = Query(None),
    population: str = _POP_LISTE,
    sect_id: int | None = Query(None),
    sects: str | None = Query(None),
) -> dict:
    """Aperçu à l'écran de la liste des abonnés en baisse."""
    a, m = _cycle(cycle, annee, mois)
    nb, lignes = _abonnes_en_baisse(a, m, str_id, population,
                                    _sects(sect_id, sects))
    return {"cycle": cycles.libelle(a, m), "nb_secteurs": nb,
            "volume_perdu": round(sum(-(r.get("ecart") or 0) for r in lignes), 1),
            "lignes": lignes}


@app.get("/api/export/abonnes-baisse", tags=["export"])
def export_abonnes_baisse(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    str_id: int | None = Query(None),
    population: str = _POP_LISTE,
    sect_id: int | None = Query(None),
    sects: str | None = Query(None),
) -> Response:
    """Pour chaque secteur en baisse, les abonnés qui portent la chute."""
    a, m = _cycle(cycle, annee, mois)
    nb, lignes = _abonnes_en_baisse(a, m, str_id, population,
                                    _sects(sect_id, sects))
    cols = {
        **_COLS_REF_EXPORT,
        "part_baisse": "Part de la baisse du secteur (%)",
        "ecart_secteur": "Écart du secteur (m3)",
    }
    titre = (
        f"Abonnés en baisse — cycle {cycles.libelle(a, m)} — {nb} secteur(s), "
        f"{len(lignes)} abonné(s) — généré le {excel.horodatage()}"
    )
    return _xlsx(
        excel.classeur([("Abonnes en baisse", titre,
                         _mise_en_forme(lignes, cols))]),
        f"abonnes_en_baisse_{a}_{m:02d}.xlsx",
    )


@app.get("/api/export/references", tags=["export"])
def export_references(
    str_id: int = Query(...),
    sect_id: int = Query(...),
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
) -> Response:
    a, m = _cycle(cycle, annee, mois)
    data = _garde(queries.abonnes, a, m, str_id, sect_id)
    ent = data.get("entete") or {}

    centre = ent.get("centre_libelle") or f"Centre {str_id}"
    secteur = ent.get("secteur_libelle") or f"Secteur {sect_id}"
    res = data["resume"]

    titre = (
        f"{centre} · {secteur} — cycle {data['cycle']} comparé au cycle "
        f"{data['cycle_precedent']} — {res['nb_abonnes']} abonnés : "
        f"{res['nb_non_releves']} non relevés, {res['nb_releves']} relevés, "
        f"{res['nb_factures']} facturés — {res['nb_en_baisse']} en baisse, "
        f"{res['nb_disparues']} disparues, {res['nb_volume_nul']} à 0 m3, "
        f"{res['volume_perdu']:.0f} m3 perdus — généré le "
        f"{excel.horodatage()}"
    )

    lignes = data["lignes"]
    for r in lignes:
        r["centre_libelle"] = centre
        r["secteur_num"] = ent.get("secteur_num") or sect_id
    feuilles = [
        ("Tous les abonnes", titre, _mise_en_forme(lignes, _COLS_REF_EXPORT)),
    ]

    non_rel = [r for r in lignes if r.get("statut_saisie") == "NON_RELEVE"]
    if non_rel:
        feuilles.append((
            "Non releves",
            f"Abonnés du parc sans relevé sur ce cycle — {titre}",
            _mise_en_forme(non_rel, _COLS_REF_EXPORT),
        ))

    baisses = [r for r in lignes if (r.get("ecart") or 0) < 0]
    if baisses:
        feuilles.append((
            "References en baisse",
            f"Références en baisse uniquement — {titre}",
            _mise_en_forme(baisses, _COLS_REF_EXPORT),
        ))

    nuls = [r for r in lignes if r.get("gravite") == "VOLUME_NUL"]
    if nuls:
        feuilles.append((
            "Facturees a zero",
            "Références facturées à 0 m³ — compteur bloqué ou index "
            f"recopié d'un cycle à l'autre — {titre}",
            _mise_en_forme(nuls, _COLS_REF_EXPORT),
        ))

    disparues = [r for r in lignes if r.get("gravite") == "DISPARU"]
    if disparues:
        feuilles.append((
            "References disparues",
            "Références facturées au cycle précédent et absentes du cycle "
            f"courant — {titre}",
            _mise_en_forme(disparues, _COLS_REF_EXPORT),
        ))

    nom = (
        f"baisse_{excel.slug(centre)}_{excel.slug(secteur)}"
        f"_{a}_{m:02d}.xlsx"
    )
    return _xlsx(excel.classeur(feuilles), nom)


@app.get("/api/export/avancement", tags=["export"])
def export_avancement(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    str_id: int | None = Query(None),
    sect_id: int | None = Query(None),
    statut: str | None = Query(None),
) -> Response:
    a, m = _cycle(cycle, annee, mois)
    rows = _garde(queries.avancement, a, m, str_id, sect_id, statut)

    fini = sum(1 for r in rows if r.get("etat") in ("CLOTURE", "FACTURE"))
    titre = (
        f"Avancement de la saisie — cycle {cycles.libelle(a, m)} — "
        f"{len(rows)} secteur(s), {fini} clôturé(s) ou facturé(s) — "
        f"généré le {excel.horodatage()}"
    )

    feuilles = [
        ("Avancement", titre, _mise_en_forme(rows, _COLS_AVANCEMENT)),
    ]
    en_retard = [
        r for r in rows
        if r.get("etat") in ("NON_COMMENCE", "EN_COURS")
    ]
    if en_retard:
        feuilles.append((
            "Saisie non terminee",
            f"Secteurs dont la saisie n'est pas finie — {titre}",
            _mise_en_forme(en_retard, _COLS_AVANCEMENT),
        ))

    return _xlsx(
        excel.classeur(feuilles),
        f"avancement_saisie_{a}_{m:02d}.xlsx",
    )


@app.get("/api/export/retards", tags=["export"])
def export_retards(
    cycle: int | None = Query(None),
    annee: int | None = Query(None),
    mois: int | None = Query(None, ge=1, le=12),
    str_id: int | None = Query(None),
    sect_id: int | None = Query(None),
    urgence: str | None = Query(None),
) -> Response:
    a, m = _cycle(cycle, annee, mois)
    rows = _garde(queries.retards, a, m, str_id, sect_id, urgence, False)
    syn = _garde(queries.retards_synthese, a, m, str_id)

    titre = (
        f"Retard de facturation — cycle {cycles.libelle(a, m)} — "
        f"{syn.get('nb_en_retard', 0)} secteur(s) en retard sur "
        f"{syn.get('nb_secteurs', 0)}, retard maximum "
        f"{syn.get('retard_max') or 0} jour(s) — date prévue lue dans "
        f"{syn.get('colonne_deadline')} — généré le {excel.horodatage()}"
    )

    feuilles = [
        ("Suivi des dates prevues", titre,
         _mise_en_forme(rows, _COLS_RETARD)),
    ]

    en_retard = [r for r in rows if r.get("urgence") == "EN_RETARD"]
    if en_retard:
        feuilles.append((
            "Secteurs en retard",
            f"Secteurs ayant dépassé la date prévue sans être facturés "
            f"— {titre}",
            _mise_en_forme(en_retard, _COLS_RETARD),
        ))

    proches = [r for r in rows if r.get("urgence") == "DEADLINE_PROCHE"]
    if proches:
        feuilles.append((
            "Deadline proche",
            "Secteurs dont la date prévue est demain "
            f"— {titre}",
            _mise_en_forme(proches, _COLS_RETARD),
        ))

    sans = [r for r in rows if r.get("urgence") == "PAS_DE_PLANNING"]
    if sans:
        feuilles.append((
            "Sans planning",
            f"Secteurs sans date prévue dans la planification — {titre}",
            _mise_en_forme(sans, _COLS_RETARD),
        ))

    journal = _garde(alerts.lister_retards, False,
                     cycles.cycle_key(a, m), str_id, 2000)
    if journal:
        feuilles.append((
            "Journal des alertes",
            f"Alertes de retard levées sur ce cycle — {titre}",
            _mise_en_forme(journal, _COLS_ALERTE_RETARD),
        ))

    # Gabarit beige, comme l'export des retards du dashboard.
    return _xlsx(
        excel.classeur(feuilles, style="beige"),
        f"retard_facturation_{a}_{m:02d}.xlsx",
    )


@app.get("/api/export/alertes", tags=["export"])
def export_alertes(
    only_unack: bool = Query(False),
    cycle: int | None = Query(None),
) -> Response:
    rows = _garde(alerts.lister, only_unack, cycle, None, 2000)
    titre = (
        ("Alertes non consultées — " if only_unack
         else "Journal des alertes — ")
        + f"généré le {excel.horodatage()}"
    )
    return _xlsx(
        excel.classeur([
            ("Alertes", titre, _mise_en_forme(rows, _COLS_ALERTE)),
        ]),
        "alertes_baisse.xlsx",
    )


# ============================================================
# Erreurs
# ============================================================

@app.exception_handler(db.BaseIndisponible)
async def _indispo(request, exc: db.BaseIndisponible):  # noqa: ANN001
    return JSONResponse(status_code=503, content={"detail": str(exc)})
