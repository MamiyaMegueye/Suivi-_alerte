"""
Configuration — Alerte Baisse de Facturation (SNDE).

Application AUTONOME. Elle ne partage rien avec le dashboard de suivi
temps réel : sa propre connexion Oracle, sa propre base DuckDB, son propre
environnement virtuel. Seule la « forme » des données est reprise — les
mêmes tables et les mêmes colonnes Oracle.

Les identifiants Oracle sont lus dans le fichier `.env` placé à la racine
de cette application. Ce fichier n'est jamais versionné (voir .gitignore)
et son contenu n'apparaît nulle part dans le code.

Création du .env : copier `.env.example` en `.env` et renseigner les deux
lignes ORACLE_USER / ORACLE_PASSWORD, ou recopier le `.env` du dashboard
(voir README, section 2).
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


# ============================================================
# Lecture du .env — sans dépendance externe
# ============================================================

def _charger_env(chemin: Path) -> None:
    """Injecte les variables du .env dans os.environ.

    Les variables déjà définies dans l'environnement ont la priorité :
    on peut donc surcharger ponctuellement sans toucher au fichier.
    Format accepté : KEY=VALUE, un par ligne, # pour les commentaires,
    guillemets simples ou doubles optionnels autour de la valeur.
    """
    if not chemin.exists():
        return
    for brute in chemin.read_text(encoding="utf-8-sig").splitlines():
        ligne = brute.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, val = ligne.partition("=")
        cle = cle.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(cle, val)


_charger_env(ENV_FILE)


def _txt(cle: str, defaut: str = "") -> str:
    return os.environ.get(cle, defaut).strip()


def _ent(cle: str, defaut: int) -> int:
    try:
        return int(_txt(cle) or defaut)
    except ValueError:
        return defaut


# ============================================================
# Oracle
# ============================================================
ORACLE_USER = _txt("ORACLE_USER")
ORACLE_PASSWORD = _txt("ORACLE_PASSWORD")
ORACLE_HOST = _txt("ORACLE_HOST", "192.168.10.30")
ORACLE_PORT = _ent("ORACLE_PORT", 1521)
ORACLE_SERVICE = _txt("ORACLE_SERVICE", "Sigasnde")

ORACLE_DSN = f"{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE}"

# Garde-fous de production, reprises du dashboard.
ORACLE_QUERY_TIMEOUT_SEC = _ent("ORACLE_QUERY_TIMEOUT_SEC", 600)
ORACLE_FETCH_BATCH = _ent("ORACLE_FETCH_BATCH", 10_000)


def oracle_configure() -> bool:
    """Vrai quand les identifiants sont présents."""
    return bool(ORACLE_USER and ORACLE_PASSWORD)


# ============================================================
# Base locale
# ============================================================
DB_PATH = Path(_txt("BAISSE_DB") or (BASE_DIR / "data" / "baisse.duckdb"))


# ============================================================
# Périmètre métier — mêmes règles que le dashboard
# ============================================================
# Zones S_ZONE : 1 INTERIEUR · 2 NOUAKCHOTT · 3 NOUADHIBOU
ZONE_IDS = _txt("ZONE_IDS", "1,2,3")

ZONE_LIBELLES: dict[int, str] = {
    1: "INTERIEUR",
    2: "NOUAKCHOTT",
    3: "NOUADHIBOU",
}

# Structures qui ne sont PAS des centres d'exploitation (directions et
# entités « zone »). TSTR_ID ne permet pas de les distinguer : les vrais
# centres partagent TSTR_ID = 1, d'où cette liste explicite.
#   1 DIRECTION SNDE · 2 ZONE NOUAKCHOTT · 5 ZONE NOUADHIBOU
#   6 ZONE INTERIEUR · 54 DIR. TECHNICO-COMMERC. · 63 Khadamaty
PSEUDO_STRUCTURES: tuple[int, ...] = (1, 2, 5, 6, 54, 63)


def zone_ids_list() -> list[int]:
    ids = [int(x) for x in ZONE_IDS.split(",") if x.strip()]
    return ids or [2]


def zone_ids_sql() -> str:
    """Prêt à injecter dans un IN (...). Entiers de config, jamais une
    entrée utilisateur — l'injection littérale est sûre."""
    return ", ".join(str(x) for x in zone_ids_list())


def exclude_sql() -> str:
    return ", ".join(str(x) for x in PSEUDO_STRUCTURES)


# ============================================================
# Profondeur d'historique
# ============================================================
# 24 mois : il faut le cycle N-2 de chaque cycle de l'année, donc au moins
# novembre et décembre de l'année précédente pour janvier et février.
HISTORIQUE_MOIS = _ent("HISTORIQUE_MOIS", 24)


# ============================================================
# Règle d'alerte
# ============================================================
# Toute baisse du volume facturé est une alerte. L'écart est donné en m³
# et en pourcentage ; le seuil ci-dessous n'est qu'un filtre d'affichage.
BAISSE_MIN_PCT_DEFAUT = 0.0

# Paliers de gravité — couleur et tri uniquement.
GRAVITE_MOYENNE_PCT = float(_txt("GRAVITE_MOYENNE_PCT", "10") or 10)
GRAVITE_FORTE_PCT = float(_txt("GRAVITE_FORTE_PCT", "25") or 25)

SAISIE_COMPLETE_PCT = 100

# Une alerte de baisse n'est levée que lorsque le secteur est
# RÉELLEMENT FACTURÉ : tant que les factures ne sont pas émises, le
# volume du cycle est incomplet et toute comparaison est trompeuse.
# Un secteur clôturé mais pas encore facturé reste donc en observation.
#
# Mettre ALERTE_BAISSE_EXIGE_FACTURE=0 pour revenir à l'ancienne règle
# (clôturé à 100 % suffit).
ALERTE_BAISSE_EXIGE_FACTURE = _txt(
    "ALERTE_BAISSE_EXIGE_FACTURE", "1") in ("1", "true", "True")

# Alerter même sur un secteur en cours de saisie. Déconseillé : un
# secteur à 40 % de saisie baisse mécaniquement.
ALERTER_PROVISOIRE = _txt("ALERTER_PROVISOIRE", "0") in ("1", "true", "True")


# ============================================================
# Alerte « retard de facturation » — le second niveau d'alerte
# ============================================================
# La planification Oracle donne une date prévue par cycle et par secteur.
# Un secteur qui dépasse cette date sans être facturé déclenche une
# alerte jaune, distincte de l'alerte rouge de baisse.
#
# La date prévue est DATEDEBUTSEMAINE de PLANIFDETAILLE : c'est la même
# que celle de l'onglet Retards du dashboard de suivi temps réel, et
# c'est confirmé côté métier. La seule différence entre les deux
# applications est le verdict :
#   dashboard   → retard si le RELEVEUR dépasse sa deadline (saisie)
#   ici         → retard si le JOUR J DE FACTURATION est dépassé sans
#                 qu'aucune facture n'ait été émise
# COLONNE_DEADLINE reste surchargeable, les deux dates étant affichées
# côte à côte dans l'onglet Retards.
COLONNE_DEADLINE = _txt("COLONNE_DEADLINE", "DATEDEBUTSEMAINE")
if COLONNE_DEADLINE not in ("DATEDEBUTSEMAINE", "DATEDEBUTFACT"):
    COLONNE_DEADLINE = "DATEDEBUTSEMAINE"

# Marge réservée à l'adjoint de facturation avant la date prévue, en
# jours, selon la taille du secteur. Grille reprise à l'identique de
# l'onglet Retards du dashboard de suivi temps réel.
#   deadline_releveur = date prévue − marge
# La marge sert à afficher la deadline du releveur. « Deadline proche »
# = la veille du jour J ; « en retard » dès le jour J (non clôturé).
DEADLINE_MARGE_SMALL = _ent("DEADLINE_MARGE_SMALL", 1)    # < 100 abonnés
DEADLINE_MARGE_MEDIUM = _ent("DEADLINE_MARGE_MEDIUM", 2)  # 100 à 300
DEADLINE_MARGE_LARGE = _ent("DEADLINE_MARGE_LARGE", 3)    # 301 à 1000
DEADLINE_MARGE_XLARGE = _ent("DEADLINE_MARGE_XLARGE", 4)  # > 1000

# Au-delà de ce pourcentage de saisie, un secteur non clôturé est
# considéré comme capable de finir à temps : il n'alerte pas. Même
# exemption que l'onglet Retards du dashboard (90 %).
SAISIE_AVANCEE_PCT = _ent("SAISIE_AVANCEE_PCT", 90)

# Alerter aussi sur les secteurs sans planning. Faux par défaut :
# l'absence de planning est un problème de saisie du planning, pas un
# retard de facturation.
# Lever aussi le bandeau jaune la veille du jour J (« deadline proche »).
# Quand le secteur passe ensuite en retard, le bandeau revient.
ALERTER_DEADLINE_PROCHE = _txt(
    "ALERTER_DEADLINE_PROCHE", "1") in ("1", "true", "True")

ALERTER_SANS_PLANNING = _txt(
    "ALERTER_SANS_PLANNING", "0") in ("1", "true", "True")

# Alerter aussi sur les secteurs CLÔTURÉS dont la date de facturation est
# passée sans qu'aucune facture ne soit émise. Faux par défaut : la
# facturation est un traitement de masse qui suit la clôture de quelques
# jours, et en faire une alerte remplirait le bandeau à chaque cycle.
# Ces secteurs restent visibles dans l'onglet, en « À facturer, date
# dépassée ».
ALERTER_FACTURATION_RETARD = _txt(
    "ALERTER_FACTURATION_RETARD", "0") in ("1", "true", "True")


# ============================================================
# Watcher temps réel
# ============================================================
# CLOTURE_SECTEUR est une petite table : on peut la relire souvent sans
# peser sur Oracle. Les volumes ne bougent qu'à la clôture et à la
# facturation d'un secteur, inutile de descendre sous la minute.
WATCHER_INTERVAL_SEC = _ent("WATCHER_INTERVAL_SEC", 180)

# Les factures et relevés (gros volumes) ne sont rafraîchis qu'un tick
# sur N, et uniquement sur les cycles récents.
WATCHER_FACTURES_TOUS_LES = _ent("WATCHER_FACTURES_TOUS_LES", 10)

# Recharger à CHAQUE passage les factures des 2 cycles surveillés, pour
# que l'alerte rouge parte dès l'émission des factures et non jusqu'à
# 30 minutes plus tard. Mettre 0 si la requête pèse trop sur Oracle.
WATCHER_FACTURES_COURANT = _txt(
    "WATCHER_FACTURES_COURANT", "1") in ("1", "true", "True")

# Mettre WATCHER_ACTIF=0 pour démarrer l'API sans toucher à Oracle
# (lecture de la base locale seulement).
WATCHER_ACTIF = _txt("WATCHER_ACTIF", "1") in ("1", "true", "True")


# ============================================================
# API
# ============================================================
API_HOST = _txt("BAISSE_API_HOST", "0.0.0.0")
API_PORT = _ent("BAISSE_API_PORT", 8020)

NB_CYCLES_LISTE = _ent("NB_CYCLES_LISTE", 24)
