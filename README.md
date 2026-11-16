# SNDE — Alerte Baisse de Facturation

Application **totalement autonome** : sa propre connexion Oracle, sa propre
base DuckDB, son propre environnement Python, son propre watcher temps réel.

Elle ne partage **rien** avec le dashboard de suivi temps réel — ni fichier,
ni processus, ni environnement virtuel. Les deux tournent en parallèle sans
jamais se gêner. La seule chose reprise du dashboard est la **forme des
données** : les mêmes tables et les mêmes colonnes Oracle.

Elle détecte les secteurs dont le volume facturé baisse d'un cycle de relève
à l'autre, affiche un bandeau rouge persistant, et permet de descendre
jusqu'aux références responsables de la baisse.

---

## 1. Le principe

### Pourquoi N-2 et pas N-1

La relève est **bimestrielle**. Un secteur relevé en septembre l'est de
nouveau en novembre, jamais en octobre. Les secteurs se répartissent donc en
deux groupes :

```
groupe impair : … 07 → 09 → 11 …
groupe pair   : … 08 → 10 → 12 …
```

Comparer 09/2026 à 08/2026 n'a aucun sens — ce ne sont pas les mêmes
secteurs. La comparaison se fait toujours avec le cycle précédent de **même
parité**, soit **N-2**.

### Les quatre onglets

**1 — Baisses de facturation.** Où le volume a chuté.

```
Bandeau rouge  →  Journal des alertes
Synthèse (volume N, volume N-2, écart m³ et %)
   └─ Par centre
        └─ Par secteur   ← clic = onglet 3
```

**2 — Avancement de la saisie.** Suivi de la relève, du premier
compteur saisi jusqu'à la clôture puis la facturation.

```
Par secteur : parc, saisis, restant, % déclaré, % réel,
              1er relevé, dernier relevé, jours de relève,
              date de clôture, nombre de factures
   └─ clic = progression jour par jour (graphe)
```

Deux pourcentages sont donnés côte à côte :

| | Source | Ce que ça dit |
|---|---|---|
| **% déclaré** | `POURCTRAITER` de CLOTURE_SECTEUR | ce que le système de facturation affiche |
| **% réel** | compté dans `S_RELEVE` sur le parc | ce que le terrain a réellement saisi |

Un écart de plus de 10 points entre les deux est signalé sous le tableau :
soit les données locales ne sont pas encore à jour, soit des relevés ont
été saisis hors du compteur déclaré.

**3 — Retards de facturation.** Quels secteurs ont dépassé leur date
prévue sans être facturés.

L'onglet **n'affiche par défaut que les secteurs à traiter** — ceux en
retard et ceux dont la deadline approche — comme l'onglet Retards du
dashboard. Des chips permettent de basculer, avec le compte de chacune :

```
À traiter (N) · Deadline dépassée (N) · Deadline proche (N)
À facturer, date dépassée (N) · Sans planning (N) · Tous (N)
```

Colonnes calquées sur celles du dashboard — statut, centre, secteur,
releveur, avancement, parc sous la forme `saisis / parc`, deadline du
releveur avec sa marge, et le temps restant formulé « dépassé de N j » —
plus les deux colonnes propres à cette application : la **date prévue de
facturation** et le retard sur cette date.

```
   └─ clic = détail par abonné
```

**4 — Détail par abonné.** Quelles références portent la chute, dans un
secteur donné.

```
Par abonné : référence, statut de saisie, adresse, n° compteur,
             date de relevé, index N-2, index début, index fin,
             état compteur, type de facture,
             volume N-2, volume N, écart m³, écart %
```

Cette vue part du **parc du secteur** (`S_ABONNEMENT`), pas des factures.
C'est ce qui la rend utilisable dès le début de la saisie : on voit qui a
été relevé, qui ne l'est pas encore, puis qui a été facturé.

| Statut | Signification |
|---|---|
| **Non relevé** | dans le parc, aucun relevé sur ce cycle |
| **Relevé** | relevé saisi, facture pas encore émise |
| **Facturé** | facture émise |
| **Hors parc** | relevé ou facturé mais absent du parc du secteur |

Les deux premiers statuts ont un volume N à zéro **par construction**. Ils
sont donc exclus du calcul « en baisse » et du volume perdu, et comptés à
part sous « facture à venir », avec le volume qu'ils avaient produit au
cycle N-2. Sans cette séparation, un secteur à mi-saisie afficherait tous
ses abonnés comme « plus facturés ».

**Gros consommateurs.** Les secteurs facturés en relevé **tous les
mois** ont leur propre onglet, à côté de Baisses de facturation, avec la
même structure (synthèse, par centre, par secteur, export Excel, clic vers
le détail par abonné). Leur volume est comparé au **mois précédent (N-1)**
et non au cycle N-2 ; ils sont retirés de l'onglet Baisses.

Un secteur est repéré comme gros consommateur, sur les 12 mois qui se
terminent au cycle affiché, quand au moins la moitié de ses factures
relevées concernent un abonné déjà facturé en relevé le mois précédent
(`PART_MENSUELLE_MIN`, et au moins 30 factures sur la période). Un secteur
qui mélange deux groupes bimestriels — l'un les mois impairs, l'autre les
mois pairs — est facturé tous les mois mais jamais deux fois de suite pour
le même abonné : il reste comparé à N-2. L'alerte rouge couvre aussi les
gros consommateurs ; le journal indique « (N-1) » pour eux.

**Référence de comparaison.** Pour chaque abonné facturé en relevé sur
les deux cycles, le volume de référence est la **médiane de ses 3
dernières factures relevées** jusqu'au cycle N-2 (N-1 pour les gros
consommateurs). Un pic isolé — une fuite au cycle N-2 — ne fabrique plus de
fausse baisse. Le volume brut du cycle N-2 reste affiché à côté.

**Abonnés perdus.** Abonnés facturés en relevé au cycle de référence et
sans facture relevée au cycle N (estimés, non facturés, sortis du
secteur). Ils ne sont pas dans la comparaison ; leur nombre et le volume
qu'ils représentaient sont donnés par secteur, par centre et en synthèse.

**Part de la baisse.** Dans le détail par abonné, chaque abonné en baisse
porte sa part de la baisse du secteur, et le résumé indique combien
d'abonnés en font 80 % : ce sont les premiers à contrôler.

**Temps réel.** À chaque passage du watcher (3 min), les factures des deux
cycles en cours sont rechargées avant la détection : l'alerte part dès
l'émission des factures (`WATCHER_FACTURES_COURANT`, 1 par défaut).

### Les deux alertes

L'application lève **deux alertes indépendantes**. Un secteur peut être en
retard sans être en baisse, et inversement.

#### Bandeau rouge — baisse de facturation

Levée quand un secteur est **réellement facturé** — au moins une facture
émise sur le cycle — **et** que son volume facturé est inférieur à celui du
cycle N-2. Toute baisse compte, aussi petite soit-elle, et l'écart est donné
en m³ **et** en pourcentage.

Attendre la facturation est le point décisif. Un secteur clôturé mais pas
encore facturé a un volume incomplet ; un secteur à 40 % de saisie est
forcément « en baisse » puisqu'il manque des relevés. Ces secteurs restent
visibles dans les tableaux, avec leur état et le badge « provisoire », mais
ne déclenchent rien.

Réglages : `ALERTE_BAISSE_EXIGE_FACTURE` (1 par défaut),
`ALERTER_PROVISOIRE` (0 par défaut).

#### Bandeau jaune — retard de facturation

La planification Oracle donne, pour chaque secteur et chaque cycle, une
**date prévue**. L'alerte jaune se lève quand un secteur n'est **toujours
pas clôturé** alors que son jour J de facturation est passé : il ne
pourra pas être facturé dans les délais.

**La population est exactement celle de l'onglet Retards du dashboard** —
les secteurs `CLOTURE = 0`, avec l'exemption au-delà de 90 % de saisie
(`SAISIE_AVANCEE_PCT`). Un secteur déjà clôturé a fait son travail : la
facturation qui suit est un traitement de masse, il apparaît en
« à facturer » sans alerter.

Sans cette distinction, **presque tous les secteurs d'un cycle en cours
remonteraient en retard**, puisqu'ils sont clôturés mais pas encore
facturés.

La source est exactement celle de l'onglet Retards du dashboard de suivi
temps réel : `DATEDEBUTSEMAINE` de `PLANIFDETAILLE`, avec la même grille
de marge pour l'adjoint de facturation selon la taille du secteur.

| Parc (`NBABO`) | Marge |
|---|---|
| < 100 | 1 jour |
| 100 à 300 | 2 jours |
| 301 à 1000 | 3 jours |
| > 1000 | 4 jours |

`deadline_releveur = date prévue − marge`. Les mêmes informations que le
dashboard sont remontées : marge, deadline du releveur, jours restants,
et le matricule du releveur ayant réellement saisi le secteur
(`MODE(MATRICULE)` sur les relevés du cycle, repli sur `MAT_RELEV`).

**La seule différence avec le dashboard est le verdict :**

| | Condition du retard |
|---|---|
| Dashboard de suivi | le **releveur** dépasse sa deadline, soit `date prévue − marge` |
| Ici | le **jour J de facturation** lui-même est dépassé, secteur toujours pas clôturé |

Même population, même grille de marge, même exemption à 90 % — seul le
seuil de date change : la marge pour le dashboard, le jour J pour ici.

Six situations sont distinguées :

| Situation | Signification | Alerte |
|---|---|---|
| **En retard** | **non clôturé**, jour J dépassé, saisie < 90 % | 🟡 **oui** |
| Deadline proche | non clôturé, la veille du jour J | non |
| Dans les délais | date à venir, ou saisie ≥ 90 % | non |
| À facturer | clôturé, facturation à venir | non |
| À facturer, date dépassée | clôturé, date passée, aucune facture | non par défaut |
| Facturé | facturé dans les délais | non |
| Facturé après la date | facturé, mais après la date prévue | non — constat |
| Sans planning | aucune date prévue dans Oracle | non par défaut |

`ALERTER_FACTURATION_RETARD=1` fait alerter la cinquième ligne, si tu
veux aussi être prévenu quand la facturation elle-même traîne.

Les deux colonnes de dates de `PLANIFDETAILLE` restent affichées côte à
côte dans l'onglet et dans l'export, et `COLONNE_DEADLINE` permet de
basculer sur `DATEDEBUTFACT` si besoin.

L'état d'avancement est suivi pour chaque secteur, exactement comme tu l'as
décrit : `Non commencé` → `Saisie en cours` → `Saisie finie` → `Clôturé` →
`Facturé`.

### Le bandeau rouge

Il apparaît en haut de la page dès qu'il existe au moins une alerte non
consultée, et affiche le nombre de secteurs, le volume perdu, le nombre de
centres touchés.

**Il ne disparaît que lorsqu'on le consulte.** Le bouton « Consulter les
alertes » acquitte les alertes et ouvre le journal. Une alerte acquittée
reste acquittée : le watcher réactualise ses chiffres à chaque passage mais
ne la fait jamais réapparaître. Seule une **nouvelle** alerte fait revenir
le bandeau — avec un clignotement, poussé en temps réel par SSE.

### Les couleurs

| Situation | Couleur | Signification |
|---|---|---|
| Plus facturé | rouge plein | le secteur ou la référence a disparu de la facturation |
| Facturé à 0 m³ | rouge | facture émise mais volume nul — compteur bloqué, index recopié |
| Baisse forte | rouge clair | ≥ 25 % |
| Baisse | rose | ≥ 10 % |
| Baisse légère | ambre | < 10 % |
| Stable / Hausse / Nouveau | gris / vert / bleu | pas d'alerte |

Les seuils se règlent dans le `.env` (`GRAVITE_MOYENNE_PCT`,
`GRAVITE_FORTE_PCT`).

---

## 2. Installation

Double-clic sur **`installer.bat`**. Il fait quatre choses :

1. crée l'environnement virtuel `.venv` propre à cette application ;
2. installe les dépendances (`fastapi`, `uvicorn`, `duckdb`, `pandas`,
   `openpyxl`, `oracledb`) ;
3. crée le fichier `.env` à partir de `.env.example` ;
4. teste la connexion Oracle.

### Les identifiants Oracle

Le `.env` de cette application est le **seul** endroit où ils figurent. Il
n'est jamais versionné (voir `.gitignore`) et aucun identifiant n'apparaît
dans le code.

Le plus simple est de recopier celui du dashboard, en une commande :

```powershell
copy "C:\Users\P C\Desktop\version_d\Dashboard-main\Dashboard-main\backend\.env" .env
```

Sinon, ouvre `.env` dans le Bloc-notes et renseigne `ORACLE_USER` et
`ORACLE_PASSWORD`.

### Chargement initial

```powershell
.venv\Scripts\python.exe -m src.etl
```

Charge 24 mois d'historique : clôtures, factures, relevés, abonnements,
compteurs, références. Compte quelques minutes la première fois.

`lancer.bat` le fait tout seul si la base locale est absente.

---

## 3. Lancement

Double-clic sur **`lancer.bat`**, ou :

```powershell
.venv\Scripts\python.exe -m uvicorn src.api:app --host 0.0.0.0 --port 8020
```

→ **http://localhost:8020**

Pas de `npm install`, pas de build, pas de second serveur : l'interface est
une page unique servie par FastAPI, sans aucune bibliothèque externe. Elle
fonctionne sans accès Internet.

Le port 8020 est libre : 8000 est pris par une autre application, 8010 par
le dashboard.

### Partage sur le réseau

L'interface n'appelle que des chemins relatifs — SSE compris. Donc
`http://<ton-IP>:8020` suffit pour tes collègues, **sans aucune
configuration**. Pense simplement à autoriser le port 8020 dans le
pare-feu Windows.

C'est une différence avec le dashboard, dont les flux SSE attaquent le
backend en direct et imposaient de régler `NEXT_PUBLIC_API_URL`.

---

## 4. Le watcher temps réel

Il démarre avec l'application. Toutes les **180 secondes** par défaut :

1. recharge `CLOTURE_SECTEUR` et la planification depuis Oracle ;
2. relance la détection de baisses ;
3. publie en SSE s'il y a du nouveau → le bandeau rouge apparaît et
   clignote, sans rechargement de page.

Un tick sur 10 (une demi-heure) recharge aussi les factures et les relevés
des cycles récents, pour que le détail par référence reste à jour.

**Pourquoi 180 s et pas 10 s comme le dashboard** : ici on surveille le
volume facturé, qui ne bouge qu'à la clôture et à la facturation d'un
secteur. Descendre sous la minute n'apporterait rien et chargerait Oracle
pour rien. Réglable par `WATCHER_INTERVAL_SEC`.

Le bouton **Rafraîchir** de l'en-tête force une lecture immédiate sans
attendre le tick suivant.

Pour ouvrir l'interface sans toucher à Oracle : `WATCHER_ACTIF=0`.

---

## 5. Filtres et choix du cycle

### Filtre par centre et par secteur

Une barre de filtres au-dessus des onglets, **partagée par les quatre**,
calquée sur celle du dashboard de suivi temps réel :

- **Centre** — groupé par zone, libellé `NN — NOM DU CENTRE`, avec le
  nombre de secteurs du cycle.
- **Secteur** — **uniquement les numéros**. Un même numéro peut exister
  dans plusieurs centres, donc le centre se choisit en premier ; tant
  qu'aucun centre n'est sélectionné, la liste affiche « Choisir un centre
  d'abord ».

Plus une recherche libre et un bouton de réinitialisation. Un clic sur
une ligne dans l'un des trois premiers onglets règle les deux filtres et
bascule sur le détail par abonné.

### Choix du cycle

Deux sélecteurs dans l'en-tête : **année**, puis **cycle de cette année**.
Tous les cycles chargés sont proposés — avec 24 mois d'historique, les six
cycles de l'année courante et ceux de l'année précédente.

Un cycle dont le N-2 n'est pas dans la base est marqué `(pas de N-2)` : il
n'y a rien à comparer. Le cycle ouvert par défaut est le plus récent qui
possède un N-2 comparable.

Pour élargir l'historique :

```powershell
.venv\Scripts\python.exe -m src.etl --mois 36
```

---

## 6. Données utilisées

Huit requêtes Oracle, toutes en **lecture seule** et filtrées sur le
périmètre (zones `ZONE_IDS`, hors pseudo-structures).

| Table Oracle | Ce qu'on en tire |
|---|---|
| `CRM_SNDE.CLOTURE_SECTEUR` | `NBABO`, `NBTRAITER`, `POURCTRAITER`, `CLOTURE`, `DATE_CLOT` (`VOLUMEFACT` n'est plus utilisé pour la comparaison) |
| `CRM_SNDE.S_FACTURE_M` | `FAT_QTE_CONS` (volume facturé par abonné), `TYPE_FACT`, `FACT_ANNEE × 100 + FACT_PERIODE` = cycle |
| `CRM_SNDE.S_RELEVE` | `REL_INDEX`, `REL_ANCIEN_INDEX`, `REL_CONSOM_CALCUL`, `ID_COMPTAGE` |
| `CRM_SNDE.S_ABONNEMENT` | `ABN_ADRESSE`, `COMP_ADRESSE`, `QUARTIER`, `ANCIENNE_REFRENCE` |
| `CRM_SNDE.S_COMPTEUR_INS` | `CPT_SERIE` (n° gravé sur le compteur) |
| `CRM_SNDE.PLANIF_RELEVE` | `PLAN_REL_ID` — lien secteur ↔ planning |
| `CRM_SNDE.PLANIFDETAILLE` | `DATEDEBUTSEMAINE`, `DATEDEBUTFACT` — dates prévues |
| `S_STRUCTURE`, `S_SECTEUR`, `COMTAGE` | libellés |

Règles métier reprises à l'identique :

- une seule ligne par `(STR_ID, SECT_ID, ANNEE, MOIS)`, celle dont
  l'`IDCLOTURE` est le plus récent ;
- factures annulées écartées (`COALESCE(FLAGANNULLE, 0) = 0`) ;
- six pseudo-structures exclues — `1, 2, 5, 6, 54, 63` — qui ne sont pas des
  centres d'exploitation.

### Sécurité Oracle

La couche d'accès reprend les garde-fous du dashboard : seules les requêtes
`SELECT` / `WITH` passent (liste blanche sur le début de requête, liste
noire sur les mots-clés d'écriture, commentaires retirés avant validation),
`autocommit` est coupé, un `rollback` est forcé à la fermeture, et un
timeout est posé côté driver. **Aucune écriture ne peut partir vers la base
de production**, même par erreur de code.

### Deux choix de jointure qui comptent

Les comparaisons utilisent un **FULL OUTER JOIN** entre le cycle N et le
cycle N-2, au niveau secteur comme au niveau référence. Un secteur — ou une
référence — **facturé en N-2 et absent en N** est la baisse la plus grave
qui existe ; avec une jointure classique il disparaîtrait de la liste.

Au niveau référence, deux causes de volume nul sont distinguées :

- **Plus facturé** — aucune facture au cycle N. Problème de gestion
  (abonné résilié, sorti du secteur, oublié).
- **Facturé à 0 m³** — facture émise avec un volume nul. Problème de
  terrain (compteur bloqué, index recopié).

Le badge **« index figé »** signale les références dont l'index de fin est
identique à celui du cycle N-2.

---

## 7. Interface

| Élément | Rôle |
|---|---|
| Bandeau rouge | alertes non consultées ; disparaît à la consultation |
| Journal des alertes | historique, avec statut `nouvelle` / `consultée` |
| Sélecteurs année + cycle | n'importe quel cycle chargé |
| Pastille « temps réel » | état du flux SSE (vert en ligne, rouge hors ligne) |
| Pastille « données » | fraîcheur du dernier chargement et période de veille |
| Onglets | compteur sur chacun : secteurs en baisse, saisies non finies, abonnés |
| Filtres globaux | centre (groupé par zone, `NN — NOM`), secteur (numéros seuls), recherche |
| 4 cartes KPI (onglet 1) | volume N, écart global, secteurs en baisse, volume perdu |
| 4 cartes KPI (onglet 2) | avancement du cycle, non commencés, en cours, clôturés |
| Graphe de progression | barres par jour + courbe de cumul en % du parc, en SVG |
| Colonnes | toutes cliquables pour trier |

### Exports Excel

Mise en forme identique à celle du dashboard — titre bleu SNDE, en-tête
figé, tableau Excel natif avec filtre et lignes alternées.

| Bouton | Contenu |
|---|---|
| **Export Excel** (onglet 1) | `Par centre`, `Par secteur`, `Journal des alertes` |
| **Export Excel** (onglet 2) | `Avancement`, `Saisie non terminée` |
| **Export Excel** (onglet 3) | `Suivi des dates prévues`, `Secteurs en retard`, `Deadline proche`, `Sans planning`, `Journal des alertes` |
| **Export Excel** (onglet 4) | tous les abonnés, les non relevés, ceux en baisse, ceux à 0 m³, ceux disparus |
| **Exporter** (bandeau) | les alertes non consultées |

Les feuilles vides ne sont pas créées.

---

## 8. Structure

```
Bureau\alerte-baisse\
├── installer.bat           installation en un double-clic
├── lancer.bat              démarrage
├── .env.example            modèle de configuration
├── .env                    identifiants Oracle (non versionné)
├── requirements.txt
├── data\baisse.duckdb      base locale (regénérable)
└── src\
    ├── config.py           .env, périmètre, seuils
    ├── oracle_client.py    accès Oracle en lecture seule
    ├── extractor.py        les huit requêtes Oracle
    ├── db.py               base locale DuckDB + schéma
    ├── etl.py              chargement complet / rapide
    ├── cycles.py           parité bimestrielle, calcul de N-2
    ├── queries.py          SQL métier des comparaisons
    ├── alerts.py           les deux détections, journaux, acquittement
    ├── sse.py              diffusion temps réel
    ├── watcher.py          boucle Oracle en tâche de fond
    ├── excel.py            mise en forme des classeurs
    ├── api.py              FastAPI + service de l'interface
    └── static\index.html   interface : 3 onglets, page unique,
                            zéro dépendance externe
```

### API

| Endpoint | Rôle |
|---|---|
| `GET /api/cycles` | cycles et années disponibles, cycle par défaut |
| `GET /api/synthese?cycle=202609` | chiffres globaux |
| `GET /api/centres?cycle=` | agrégat par centre |
| `GET /api/secteurs?cycle=&str_id=&baisse_min_pct=&toutes=` | par secteur |
| `GET /api/centres-list` · `/api/secteurs-list?str_id=` | listes des filtres |
| `GET /api/avancement?cycle=&str_id=&sect_id=&statut=` | avancement par secteur |
| `GET /api/avancement/jours?str_id=&sect_id=` | progression quotidienne |
| `GET /api/abonnes?cycle=&str_id=&sect_id=` | détail par abonné |
| `GET /api/retards?cycle=&str_id=&sect_id=&urgence=` | dates prévues et retards |
| `GET /api/retards/synthese` | chiffres de l'onglet Retards |
| `GET /api/alertes-retard` · `/count` | journal et compteur du bandeau jaune |
| `POST /api/alertes-retard/acknowledge` · `/acknowledge-all` | consultation |
| `GET /api/alertes` · `/api/alertes/count` | journal et compteur du bandeau |
| `POST /api/alertes/acknowledge` · `/acknowledge-all` | consultation |
| `GET /api/stream/alertes` | flux SSE |
| `POST /api/refresh?complet=true` | chargement Oracle immédiat |
| `GET /api/etat` · `/api/oracle/test` | fraîcheur, watcher, diagnostic |
| `GET /api/export/secteurs` · `/avancement` · `/retards` · `/references` · `/alertes` | classeurs |

Tous les endpoints de données acceptent `?cycle=202609` ou
`?annee=2026&mois=9`. Sans paramètre, le cycle par défaut est utilisé.

---

## 9. Limites connues

- **L'application et le script de chargement ne peuvent pas tourner en même
  temps** : c'est la même base DuckDB locale. Quand l'application tourne,
  utilise le bouton **Rafraîchir** plutôt que `python -m src.etl`. Le
  message d'erreur le rappelle.
- **Le volume comparé est la somme de `FAT_QTE_CONS` des factures
  relevées (`TYPE_FACT` 0, 1, 2 : relevée, estimée, forfaitaire), à abonnés constants** : seuls comptent les
  abonnés facturés en relevé sur les deux cycles. Un abonné disparu, estimé
  ou en mutation ne fait donc pas baisser le secteur.
- **Un secteur en cours de saisie baisse mécaniquement**, d'où le badge
  « provisoire » et l'absence d'alerte dans ce cas.
- **La comparaison porte sur le volume total du secteur**, pas sur le volume
  par abonné. Un secteur qui perd des abonnés baisse donc sans que la
  consommation unitaire ait changé — le parc des deux cycles est affiché
  pour le vérifier.
- **Le % réel de l'onglet Avancement dépend des relevés chargés.** Le
  watcher ne recharge `S_RELEVE` qu'un tick sur dix : en pleine journée de
  relève, le % réel peut être en retard sur le % déclaré. Le bouton
  Rafraîchir les remet d'aplomb.
- **Le détail par abonné dépend des factures chargées.** Si le watcher
  n'a pas encore fait son tick « factures », un secteur tout juste facturé
  peut apparaître sans détail. Le bouton Rafraîchir force le chargement.
