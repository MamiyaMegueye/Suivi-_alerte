# =============================================================
#  PARAMÈTRES DU MODÈLE D'OBJECTIFS PAR CENTRE
#  Modifiez uniquement ce fichier.
# =============================================================

# Objectif national (MRU)
OBJECTIF_NATIONAL = 1_800_000_000

# "annuel"  -> l'objectif national est divisé par 12 pour obtenir la cible mensuelle
# "mensuel" -> l'objectif national est déjà une cible mensuelle
PERIODE_OBJECTIF = "annuel"

# Plafond fixé par le DG : objectif mensuel <= COEF_PLAFOND x Pic d'encaissement 2026
COEF_PLAFOND = 1.2

# Règle du DG (1,2 x Pic 2026) :
#   "national" = objectif NATIONAL du mois >= 1,2 x meilleur mois national de 2026, réparti par le modèle
#   "minimum"  = objectif de CHAQUE centre >= 1,2 x son Pic (total très élevé : les pics sont à des mois différents)
#   "maximum"  = objectif de chaque centre <= 1,2 x son Pic
SENS_REGLE_DG = "minimum"
# TOTAL mensuel visé (mode "minimum") : au-dessus, la part au-dessus du minimum COEF_PLAFOND x Pic
# est réduite ; en dessous, tous les centres sont relevés dans la même proportion. None = libre.
OBJECTIF_MAX_MOIS = 184_000_000

# Progression maximale par rapport à l'encaissement moyen 2026 (0.50 = +50 %).
# Évite les objectifs irréalistes quand le pic 2026 est exceptionnel.
# None = pas de limite.
PROGRESSION_MAX = 0.50

# Critères retirés du modèle.
# "nb_abonnes" : déjà contenu dans le CA domestique (poids nul).
# Autres valeurs possibles : "ca_dom", "ca_gc", "nb_gc", "volume_int"
CRITERES_EXCLUS = ["nb_abonnes"]

# Tarifs considérés comme gros consommateurs
TARIFS_GC = ["2220", "1110", "4440"]

# Période d'estimation : fenêtre glissante, recalculée à chaque lancement.
# Par défaut : les FENETRE_MOIS derniers mois complets. Le modèle calcule
# l'objectif du mois suivant (MOIS_OBJECTIF).
FENETRE_MOIS = 12
MOIS_FIN = None      # None = dernier mois complet ; sinon "AAAA-MM" pour forcer
MOIS_DEBUT = None    # None = MOIS_FIN - (FENETRE_MOIS - 1)

# Année du Pic (règle du DG : le pic de référence est celui de 2026)
ANNEE_PIC = 2026

# Un mois dont le CA national est inférieur à SEUIL x la médiane mensuelle est
# considéré comme incomplet (facturation manquante) et exclu du modèle.
# None = pas de contrôle.
SEUIL_MOIS_INCOMPLET = 0.5

# Facturation bimestrielle : lisser le CA sur 2 mois (recommandé)
LISSAGE_BIMESTRIEL = True

# Tarifs facturés chaque mois : leur CA n'est pas lissé
# (4440 = administrations : facturé chaque mois, mais pas gros consommateur).
TARIFS_FACTURATION_MENSUELLE = ["2220", "1110", "4440"]

# Administrations (État) : pas de règle de paiement fixe -> HORS objectifs.
# True = leurs encaissements ne sont pas pris en compte (objectif État = 0).
EXCLURE_ADMINISTRATIONS = False
# Tarifs retirés aussi des critères (CA, effectifs). [] = le tarif 4440 reste dans les critères.
TARIFS_ADMINISTRATIONS = []

# Encaissements modélisés : True = privés seulement (comme l'écran Objectifs et le
# rapport DSP) ; l'État reçoit un objectif à part (médiane mensuelle de ses paiements).
# False = tous les encaissements ensemble.
MODELISER_PRIVES = False

# Mois exceptionnels : un mois > SEUIL_EXCEPTIONNEL x la médiane du centre est
# ramené à ce seuil (gros paiement ponctuel). None = pas d'écrêtage.
SEUIL_EXCEPTIONNEL = 2.0
# Le Pic (règle 1,2 x Pic) est pris hors mois exceptionnels
PIC_HORS_EXCEPTIONNELS = True

# Objectif du mois ajusté selon la saisonnalité (janvier fort, août faible...)
SAISONNALITE = True

# Deux modèles séparés : Nouakchott + Nouadhibou (sans volume) et Intérieur
# (avec le volume produit). Les poids sont alors calculés pour chaque groupe.
MODELES_SEPARES = True

# Validation sur le passé : le dernier mois est caché au modèle puis comparé au réel
VALIDATION = True

# Centres comptés ensemble : Tintane Ancien (79) -> Tintane Nouveau (85)
FUSION_CENTRES = {"79": "85"}

# Dossiers et fichiers
DOSSIER_DONNEES = "donnees"
DOSSIER_SORTIES = "sorties"
FICHIERS = {
    "facturation": "facturation.csv",      # obligatoire
    "encaissements": "encaissements.csv",  # obligatoire
    "abonnes": "abonnes.csv",              # obligatoire
    "zones": "zones.csv",                  # obligatoire (généré par l'extraction depuis zones.py)
    "production": "production.csv",        # optionnel (volume produit intérieur)
    "objectifs_actuels": "objectifs_actuels.csv",  # optionnel (comparaison avec les objectifs DSP)
    "arrieres": "arrieres.csv",            # stock d'arriérés par centre (extraction)
}


# -------------------------------------------------------------
# Modèle multiniveau amélioré (python modele_multiniveau.py)
# -------------------------------------------------------------
HISTORIQUE_ENCAISSEMENTS = 48      # mois d'encaissements extraits (saisonnalité testable)
HISTORIQUE_FACTURATION_AVANT = 22  # mois de facturation extraits avant la fenêtre (CA décalé, validation longue)
MN_MOIS_VALIDATION = 18            # mois testés en validation chronologique (selon l'historique disponible)
MN_MOIS_RESERVES = 3               # derniers mois testés, réservés au contrôle final
MN_ETAT_SEPARE = True              # privés modélisés, État prévu à part (moyenne 12 mois)
MN_MARGE_SELECTION = 1.0           # écart minimal (points de WAPE) pour préférer un modèle plus complexe
MN_SPECIFICATION = "auto"          # "auto" = meilleure spécification en validation
# Passage de la prévision à l'objectif : règle d'effort EXPLICITE (aucune majoration automatique)
#   "aucun"        : objectif = prévision
#   "pourcentage"  : objectif = prévision x (1 + MN_EFFORT_PCT)      (décision de la direction)
#   "reference_mn" : rattrapage de la référence multiniveau          (non démontré récupérable)
#   "repere"       : effort commun pour atteindre MN_REPERE_MENSUEL  (enveloppe nationale)
MN_REGLE_EFFORT = "aucun"
MN_EFFORT_PCT = 0.0
MN_REPERE_MENSUEL = None           # repère facultatif, désactivé ; ex. 150_000_000 (1,8 Md / 12)
MN_SCENARIOS_EFFORT = [0.05, 0.10, 0.15, 0.20]   # scénarios comparés dans l'Excel
MN_EXTRACTION_OBLIGATOIRE = True   # arrêt si l'extraction échoue (sauf --jeu-sauvegarde)
MN_PROGRESSION_MAX = 0.50          # plafond +50 % OPTIONNEL : 0.50 = appliqué, None = seule la règle 1,2 x Pic
MN_ECRAN_APP = False               # True = l'écran Objectifs affiche ce modèle

# -------------------------------------------------------------
# Calcul automatique de la période (ne pas modifier)
# -------------------------------------------------------------
from datetime import date as _date


def _decaler(mois: str, n: int) -> str:
    a, m = map(int, mois.split("-"))
    k = a * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


if MOIS_FIN is None:
    MOIS_FIN = _decaler(_date.today().strftime("%Y-%m"), -1)
if MOIS_DEBUT is None:
    MOIS_DEBUT = _decaler(MOIS_FIN, -(FENETRE_MOIS - 1))
MOIS_OBJECTIF = _decaler(MOIS_FIN, 1)
# Les encaissements sont lus depuis janvier de ANNEE_PIC au moins, pour le Pic.
# Historique plus long pour le modèle amélioré (validation chronologique, saisonnalité) :
MOIS_DEBUT_ENCAISSEMENTS = min(MOIS_DEBUT, f"{ANNEE_PIC}-01",
                               _decaler(MOIS_FIN, -(HISTORIQUE_ENCAISSEMENTS - 1)))
MOIS_DEBUT_FACTURATION = _decaler(MOIS_DEBUT, -HISTORIQUE_FACTURATION_AVANT)
