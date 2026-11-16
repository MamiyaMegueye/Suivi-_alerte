# Modèle multiniveau : objectifs par centre

Ce module du projet **contrôle des versements** utilise la connexion Oracle de `backend/.env` et la base locale DuckDB du projet.

## Installation (une seule fois)

```
cd backend
.venv\Scripts\activate
uv pip install -r modele_objectifs\requirements.txt
```

## Lancer le calcul

**Arrêtez d'abord le backend (uvicorn)** : la base DuckDB ne peut être ouverte que par un seul programme à la fois.

```
cd backend\modele_objectifs
python lancer.py
```

- `python lancer.py` : extrait les données (Oracle et DuckDB), puis calcule les objectifs.
- `python lancer.py --sans-extraction` : recalcule seulement le modèle, sur les données déjà extraites. C'est utile après avoir modifié `parametres.py`.

## Données extraites automatiquement (dossier `donnees/`)

| Fichier | Source |
|---|---|
| facturation.csv | Oracle, `S_FACTURE_M.FACT_MNT` (une facture par abonné, groupements compris), par centre, mois et tarif |
| abonnes.csv | Oracle, `S_ABONNEMENT` (ni résiliés ni suspendus) |
| encaissements.csv | DuckDB, avec les mêmes règles que l'écran des encaissements |
| zones.csv | `src/api/zones.py` et `objectifs.py` |
| objectifs_actuels.csv | `src/api/objectifs.py` (objectifs DSP actuels) |
| arrieres.csv | Oracle, `S_FACTURE_RECAP.FRCP_MNT_ARR` (dernière facture de chaque abonné et groupement) |
| production.csv | **à ajouter à la main** (optionnel) : centre ; mois ; volume |

## Résultat

`sorties/objectifs_AAAA-MM.xlsx`, un fichier par mois calculé, avec ces onglets :

- **Objectifs** : l'objectif du mois (total = privés + État, ajusté à la saisonnalité), l'objectif moyen, la part courante et la part arriérés, le statut et la comparaison DSP
- **Poids des critères**
- **Objectifs 12 mois** : les objectifs des 12 prochains mois, selon la saisonnalité
- **Saisonnalité** : l'indice de chaque mois
- **Validation** : le dernier mois est caché au modèle, puis comparé au réel
- **Mois exceptionnels** : les mois écrêtés (gros paiements ponctuels)
- **Diagnostic**, **Alertes**, **Données panel**

`sorties/objectifs_AAAA-MM.csv` est lu automatiquement par l'écran « Objectifs » de l'application pour ce mois-là. Sans ce fichier, l'écran garde les objectifs DSP.

## Paramètres

Tout se règle dans `parametres.py` : objectif national, coefficient du plafond (1,2 × Pic), tarifs des gros consommateurs, période, segment d'encaissement (tous ou privés) et fusion El Mina.
## Méthode (METHODE = "shapley")

1. **Critères** : abonnés actifs, gros consommateurs actifs, CA, CA gros consommateurs, et volume produit pour l'intérieur. Le CA est la moyenne des 3 mois précédents.
2. **Poids** : décomposition de Shapley (méthode LMG). Elle donne la part de l'encaissement expliquée par chaque critère, pour Nouakchott + Nouadhibou d'un côté et l'intérieur de l'autre. Un intervalle de confiance à 90 % est calculé par bootstrap (300 tirages).
3. **Score** du centre = Σ poids × (valeur du centre ÷ total du groupe).
4. **Objectif** = encaissement actuel + hausse nationale × part du groupe × score, sans dépasser le plafond : min(1,2 × Pic 2026, +50 % de la moyenne). Ce qui dépasse est reporté sur les autres centres.
5. **Objectif du mois** = objectif moyen × indice saisonnier, toujours limité à 1,2 × Pic.

L'ancien modèle multiniveau reste disponible avec `METHODE = "mixte"`.

## Méthode « shapley » (version prévision + effort)

1. **Prévision d'encaissement** par centre : plusieurs méthodes simples sont comparées par validation
   chronologique (chaque mois testé n'utilise que les données antérieures). La meilleure méthode sans
   information future est retenue (`METHODE_PREVISION = "auto"`).
2. **Effort supplémentaire** = cible du mois − somme des prévisions, réparti selon `REGLE_EFFORT`
   (par défaut au prorata de la prévision). Il reste **à justifier** : les fichiers extraits ne
   contiennent ni l'ancienneté des créances, ni les échéanciers, ni les règlements de l'État.
3. **Objectif final** = prévision + effort, plafonné à 1,2 × Pic réel 2026 (et +50 % en option)
   après saisonnalité ; ce qui dépasse est redistribué dans les marges ; le non-réparti est affiché.
4. **Calendrier annuel** : 12 objectifs mensuels, solde transféré vers les mois qui ont une marge.

Onglets de contrôle : Contrôle détaillé, Contrôle du total, Réconciliation annuelle,
Validation prévision, Validation allocation, Sensibilité, Poids CA total vs hors GC, Saisonnalité.
Les poids Shapley décrivent l'importance historique des critères ; ils ne servent à répartir
l'effort que si `REGLE_EFFORT = "shapley"` ou `"shapley_retard"`.

### Sans total imposé (`OBJECTIF_NATIONAL = None`, réglage par défaut)
Objectif = prévision + effort justifié, plafonné. Effort justifié (`EFFORT_JUSTIFIE`) :
- rattrapage des centres de même taille (régression sur les critères, par groupe) ;
- rattrapage du taux de recouvrement médian du groupe ;
- « max » : le plus grand des deux. Le total national est la somme des centres.

## Régression quantile régularisée (expérimentation)
`python regression_quantile.py` → `sorties/objectifs_quantile_AAAA-MM.xlsx` (ne modifie pas l'écran Objectifs).
Scénarios Q50/Q60/Q70/Q75, validation chronologique (α choisi sans la période finale réservée),
règle du DG paramétrée (`SENS_REGLE_DG`), plafond +50 % optionnel (`QR_PLAFOND_PROGRESSION`).
