# Prompt — nouvelle session Claude

Copier tout le bloc ci-dessous dans une nouvelle fenêtre Claude.

---

Je suis Data Scientist à la SNDE (Société Nationale de l'Eau, Nouakchott),
au service Suivi de l'Activité Commerciale. J'ai deux applications sur mon
PC et je veux que tu travailles directement dessus.

## Les deux dossiers

**Application 1 — dashboard de suivi de relève en temps réel. RÉFÉRENCE,
NE PAS MODIFIER.**

```
C:\Users\P C\Desktop\version_d\Dashboard-main\Dashboard-main
```

Oracle `CRM_SNDE` → ETL Python (oracledb, pandas, Prefect) → DuckDB
(`backend/data/snde.duckdb`) → FastAPI (port 8010) → Next.js 15 (port 3000).
Huit onglets : Avancement, Alertes, Retards, Historique, Validation,
Comptage, Consommation, Complet.

**Application 2 — Alerte Baisse de Facturation. C'est elle que je
développe.**

```
C:\Users\P C\Desktop\alerte-baisse
```

Totalement autonome : sa propre connexion Oracle, sa propre base DuckDB
(`data/baisse.duckdb`), son propre `.venv`, son propre watcher. Elle ne
partage **rien** avec l'application 1 — seulement la « forme » des données,
c'est-à-dire les mêmes tables et les mêmes colonnes Oracle.

Architecture : FastAPI port 8020 qui sert aussi l'interface. L'interface est
**une page unique** `src/static/index.html` — HTML, CSS et JavaScript
ensemble, **zéro dépendance externe**, pas de npm, pas de build, pas de CDN.
Palette SNDE identique à l'app 1 (`#0A2A4E` → `#1565A0`).

## Règle absolue

**N'utilise jamais et n'affiche jamais le nom d'utilisateur ni le mot de
passe de ma base de données. C'est confidentiel. N'ouvre aucun fichier
`.env`.** Les identifiants sont dans `alerte-baisse/.env`, lu par
`src/config.py` — tu n'as jamais besoin de son contenu.

## Première chose à faire

Lis l'application 1 pour relever le modèle de données exact — noms de
tables, noms de colonnes, règles métier. Les deux fichiers qui font foi :

```
backend/src/extractor.py   les requêtes Oracle
backend/src/marts.py       les schémas DuckDB et les vues
```

Un dictionnaire de données complet existe déjà :
`C:\Users\P C\Desktop\alerte-baisse\DICTIONNAIRE_DONNEES.md`. Sers-t'en
comme point de départ, mais **vérifie toujours dans le code** : c'est le
code qui fait foi, pas le document.

## Les pièges de nommage, déjà identifiés

1. La table Oracle s'appelle **`COMTAGE`**, sans le P.
2. **`ID_COMPTAGE`** dans Oracle est aliasé **`ID_COMP`** partout ensuite.
   Sans l'alias, la jointure sur `ref_comptage` échoue en silence.
3. **`ANCIENNE_REFRENCE`** — la faute d'orthographe est dans Oracle, il
   faut l'écrire ainsi.
4. **`FACT_MNT_TTC` n'est pas le TTC**, c'est le solde. L'app 1 calcule
   `ARRIERES = FACT_MNT_TTC - FACT_MNT`.
5. **`SECT_CODE`** est numérique dans Oracle :
   `TRIM(CAST(SECT_CODE AS VARCHAR2(50)))`.
6. Le code du centre a **trois noms** : `STR_ID` dans les tables brutes et
   les API, `CENTRE_ID` dans `ref_centre` seulement, `centre_code` dans les
   vues.
7. **`TSTR_ID` ne distingue pas** les vrais centres des directions — tous à
   1. D'où la liste explicite de six pseudo-structures à exclure :
   `1, 2, 5, 6, 54, 63`.

## Les règles transverses à ne jamais oublier

- **Dédupliquer `CLOTURE_SECTEUR`** : plusieurs lignes par
  `(STR_ID, SECT_ID, ANNEE, MOIS)`, garder la plus récente par
  `QUALIFY ROW_NUMBER() OVER (PARTITION BY … ORDER BY IDCLOTURE DESC) = 1`.
- **Écarter les factures annulées** : `COALESCE(FLAGANNULLE, 0) = 0`.
- **Filtrer les zones** : `ZONE_ID IN (2, 3)` — Nouakchott et Nouadhibou.
- **Cycle d'une facture** : `FACT_ANNEE * 100 + FACT_PERIODE`.
- **La relève est bimestrielle.** Le cycle de comparaison est toujours
  **N-2**, le précédent de même parité (09 → 07), jamais N-1 : ce ne sont
  pas les mêmes secteurs.
- **Parc actif** : `ABN_RES = 0`.
- **Compteur courant** : dédupliquer `raw_compteur` par
  `CPI_DATE_INS DESC, ID DESC`.
- **`COUNT(DISTINCT CPT_REF)`, jamais `COUNT(*)`** pour compter les
  compteurs relevés. Un compteur relevé deux fois dans le cycle ferait
  dépasser 100 %. C'est un bug réel de l'app 1, qui touche 407 secteurs.
- **`FULL OUTER JOIN` entre deux cycles.** Un secteur ou une référence
  présent en N-2 et absent en N est la pire anomalie qui existe ; une
  jointure classique le ferait disparaître du résultat.

## Ce que fait l'application 2

Deux alertes indépendantes et quatre onglets.

### Alerte rouge — baisse de facturation

Levée quand un secteur est **réellement facturé** (au moins une facture
émise sur le cycle) **et** que son volume facturé est inférieur à celui du
cycle N-2.

- **Toute baisse compte**, aussi petite soit-elle. L'écart est donné en m³
  **et** en pourcentage.
- Volume retenu : somme de `FAT_QTE_CONS` des factures **relevées**
  (`TYPE_FACT` 0, 1, 2 : relevée, estimée, forfaitaire) de `S_FACTURE_M`, **à abonnés constants** (abonnés
  facturés en relevé sur les deux cycles). `VOLUMEFACT` n'est plus utilisé.
- Attendre la facturation est le point décisif : un secteur clôturé mais
  pas encore facturé a un volume incomplet, et un secteur à 40 % de saisie
  est forcément « en baisse » puisqu'il manque des relevés.
- Paliers de gravité, pour la couleur et le tri seulement : ≥ 25 % baisse
  forte, ≥ 10 % baisse, < 10 % baisse légère. Plus les cas particuliers
  « plus facturé » et « facturé à 0 m³ ».

### Alerte jaune — retard de facturation

Levée quand un secteur n'est **toujours pas clôturé** alors que son jour J
de facturation est passé : il ne pourra pas être facturé dans les délais.

- Date prévue : **`DATEDEBUTSEMAINE` de `PLANIFDETAILLE`**, la même que
  l'onglet Retards de l'app 1.
- **Même population que l'app 1** : `WHERE CLOTURE = 0`, avec l'exemption
  au-delà de **90 %** de saisie. Un secteur clôturé a fait son travail, il
  apparaît en « à facturer » sans alerter. Sans cette distinction, presque
  tous les secteurs d'un cycle en cours remontent en retard.
- Même grille de marge pour l'adjoint : < 100 abonnés → 1 jour, 100-300 →
  2, 301-1000 → 3, > 1000 → 4. `deadline_releveur = date prévue − marge`.
- **Seule différence avec l'app 1** : l'app 1 déclenche sur
  `date prévue − marge` (retard du releveur), ici c'est le **jour J**
  lui-même (retard de facturation).

### Comportement commun des deux bandeaux

Un bandeau coloré en haut de page, qui **ne disparaît que lorsqu'on le
consulte**. Journal persistant avec `detected_at` / `acknowledged` /
`acknowledged_at` : le watcher réactualise les chiffres à chaque passage
mais ne remet jamais `acknowledged` à FALSE. Seule une nouvelle alerte fait
revenir le bandeau, avec un clignotement, poussé en SSE.

### Les quatre onglets

1. **Baisses de facturation** — synthèse, par centre, par secteur.
2. **Avancement de la saisie** — du premier relevé jusqu'à la clôture puis
   la facturation. Deux pourcentages côte à côte : le déclaré
   (`POURCTRAITER`) et le réel (compté dans `S_RELEVE`). Clic sur une ligne
   → graphe de progression jour par jour, en SVG inline.
3. **Retards de facturation** — dates prévues, jours de retard, avancement.
4. **Détail par abonné** — assis sur le **parc du secteur**
   (`S_ABONNEMENT`), pas sur les factures, pour être utilisable dès le
   début de la saisie. Statuts : non relevé, relevé, facturé, hors parc.
   Un abonné pas encore relevé ou pas encore facturé a un volume N à zéro
   **par construction** : il est compté à part en « facture à venir », pas
   en baisse.

### Filtres globaux, partagés par les quatre onglets

Calqués sur la `FilterBar` de l'app 1 :

- **Centre** — groupé par zone avec `<optgroup>`, libellé `NN — NOM DU
  CENTRE`.
- **Secteur** — **uniquement les numéros** (`SECT_CODE`). Un même numéro
  peut exister dans plusieurs centres, donc le centre se choisit en
  premier.

### Exports Excel

Mise en forme identique à l'app 1 : titre fusionné bleu SNDE, en-tête figé,
tableau Excel natif avec filtre et lignes alternées. Un classeur par
onglet, plusieurs feuilles, et les feuilles vides ne sont pas créées.
`Response` avec `Content-Length`, jamais `StreamingResponse`. Côté page,
téléchargement par `fetch` + blob pour afficher l'erreur du serveur au lieu
d'ouvrir un onglet vide.

## Comment je veux que tu travailles

1. **Directement sur mon PC**, pas de fichiers à télécharger et recopier.
2. **Ne casse rien de ce qui marche.** Avant de modifier, lis le code
   existant.
3. **Teste avant de me livrer.** `python -m py_compile` sur tout le
   Python, `node --check` sur le JavaScript de la page, et un test réel
   contre une base DuckDB de test avec des données fabriquées exprès pour
   le cas que tu traites.
4. **Dis-moi ce que tu as trouvé**, y compris quand c'est un défaut de mon
   code. Je préfère savoir.
5. Réponds-moi **en français**.

## Pour lancer l'application 2

```powershell
cd "C:\Users\P C\Desktop\alerte-baisse"
.\.venv\Scripts\python.exe -m src.etl          # chargement Oracle complet
.\.venv\Scripts\python.exe -m src.etl --rapide # rafraîchissement court
.\.venv\Scripts\python.exe -m uvicorn src.api:app --host 0.0.0.0 --port 8020
```

Ou double-clic sur `lancer.bat`. Interface sur http://localhost:8020

⚠️ L'application et le script de chargement ne peuvent pas tourner en même
temps : c'est la même base DuckDB locale. Quand l'app tourne, le bouton
**Rafraîchir** de l'interface remplace `python -m src.etl`.

## Pour commencer

Lis d'abord les deux applications, puis dis-moi ce que tu as compris de
l'architecture et du modèle de données avant de toucher à quoi que ce soit.
