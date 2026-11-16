# Dictionnaire de données — SNDE

Inventaire des tables et colonnes utilisées par le **dashboard de suivi
temps réel** (application 1), extrait de son code source
(`backend/src/extractor.py` et `backend/src/marts.py`), pour servir de
référence à l'**Alerte Baisse de Facturation** (application 2) et aux
suivantes.

---

## 1. La chaîne

```
Oracle CRM_SNDE  ──12 requêtes──▶  DuckDB raw_* / ref_*  ──▶  vues v_*  ──▶  marts mart_*
```

Trois niveaux à distinguer :

| Niveau | Nature | Exemple |
|---|---|---|
| `raw_*` | copie fidèle d'une table Oracle | `raw_releve` |
| `ref_*` | table de référence, rechargée entièrement | `ref_centre` |
| `v_*` | vue : jointures + colonnes calculées | `v_releve` |
| `mart_*` | table matérialisée, figée par le pipeline | `mart_secteur_cycle` |

---

## 2. Tables Oracle interrogées — 12

| Table Oracle | Rôle | Copiée dans |
|---|---|---|
| `CRM_SNDE.S_RELEVE` | les relevés de compteurs | `raw_releve` |
| `CRM_SNDE.CLOTURE_SECTEUR` | avancement et volumes par secteur et cycle | `raw_cloture_secteur` |
| `CRM_SNDE.S_FACTURE_M` | entête de facture | `raw_facture_m` |
| `CRM_SNDE.S_FACTURE_DET` | lignes de facture (tranches, redevance) | `raw_facture_det` |
| `CRM_SNDE.S_ABONNEMENT` | le parc d'abonnés, les adresses | `raw_abonnement` |
| `CRM_SNDE.S_COMPTEUR_INS` | compteurs physiques installés | `raw_compteur` |
| `CRM_SNDE.S_CLIENT` | identité du client | `raw_client` |
| `CRM_SNDE.S_STRUCTURE` | les centres | `ref_centre` |
| `CRM_SNDE.S_SECTEUR` | les secteurs | `ref_secteur` |
| `CRM_SNDE.COMTAGE` | libellés d'état de comptage | `ref_comptage` |
| `CRM_SNDE.PLANIF_RELEVE` | planification, entête | `raw_planif_releve` |
| `CRM_SNDE.PLANIFDETAILLE` | planification, détail par semaine | `raw_planif_detaille` |

⚠️ `COMTAGE` s'écrit bien **sans le P**. Ce n'est pas une faute de frappe
dans le code, c'est le nom réel de la table.

---

## 3. Colonnes, table par table

### 3.1 `S_RELEVE` → `raw_releve` — 26 colonnes

La table la plus volumineuse : 11,6 millions de lignes dans le dashboard.

| Colonne | Type | Signification |
|---|---|---|
| `REL_ID` | BIGINT **PK** | identifiant du relevé |
| `ABN_ID` | BIGINT | abonnement relevé |
| `CPT_REF` | VARCHAR | référence du compteur au relevé |
| `LOC_ID` | BIGINT | localisation |
| `TOUR_ID` | BIGINT | tournée |
| `STR_ID` | BIGINT | **code du centre** |
| `SECT_ID` | BIGINT | **code du secteur** |
| `MATRICULE` | VARCHAR | matricule du releveur |
| `REL_DATE` | TIMESTAMP | date de la saisie — base du suivi quotidien |
| `REL_ANNEE` | BIGINT | année du cycle |
| `REL_MOIS` | BIGINT | mois du cycle |
| `DATE_GEN` | TIMESTAMP | date de génération |
| `REL_DATE_ANC_INDEX` | TIMESTAMP | date de l'index précédent |
| `REL_INDEX` | DOUBLE | **index relevé** |
| `REL_ANCIEN_INDEX` | DOUBLE | **index précédent** |
| `REL_CONSOM_CALCUL` | DOUBLE | **consommation calculée** (m³) |
| `REL_MOYENNE_CONSOM` | DOUBLE | consommation moyenne de l'abonné |
| `REL_NBR_JR` | BIGINT | nombre de jours de la période |
| `ID_COMP` | BIGINT | **état du comptage** (voir § 5.1) |
| `TYP_REL_ID` | BIGINT | type de relevé |
| `REL_VALIDE` | BIGINT | 1 = relevé validé |
| `REL_ESTIMATIF` | BIGINT | 1 = relevé estimé |
| `REL_FACTURABLE_FLAG` | BIGINT | facturable |
| `REL_MAT` | VARCHAR | matricule de repli |
| `REL_MESSAGE` | VARCHAR | message du releveur |
| `REL_ORIGINE` | VARCHAR | origine de la saisie |

**Piège de nommage.** Dans Oracle la colonne s'appelle `ID_COMPTAGE`. Le
dashboard l'aliase en `ID_COMP` à l'extraction :

```sql
r.ID_COMPTAGE AS ID_COMP
```

C'est le nom `ID_COMP` qui est utilisé partout ensuite, et c'est lui qui
joint sur `ref_comptage.ID_COMP`. Oublier l'alias casse la jointure
silencieusement.

### 3.2 `CLOTURE_SECTEUR` → `raw_cloture_secteur` — 17 colonnes

Petite table, mais c'est le cœur du suivi : un enregistrement par
(centre, secteur, cycle).

| Colonne | Type | Signification |
|---|---|---|
| `IDCLOTURE` | BIGINT **PK** | identifiant — sert à la déduplication |
| `STR_ID` | BIGINT | code du centre |
| `SECT_ID` | BIGINT | code du secteur |
| `ANNEE` | BIGINT | année du cycle |
| `MOIS` | BIGINT | mois du cycle |
| `NBABO` | BIGINT | **parc du secteur** sur ce cycle |
| `NBTRAITER` | BIGINT | **nombre de relevés saisis** |
| `NBNONTRAITER` | BIGINT | restant à saisir |
| `POURCTRAITER` | BIGINT | **% d'avancement déclaré** |
| `POURCNONTRAITER` | BIGINT | % restant |
| `VOLUMEFACT` | DOUBLE | **volume facturé** (m³) |
| `VOLUMESTIM` | DOUBLE | **volume estimé** (m³) |
| `CLOTURE` | BIGINT | 0 = en cours, 1 = clôturé |
| `FLAG_CALCUL` | BIGINT | calcul effectué |
| `FLAG_VALID` | BIGINT | 1 = validé par l'adjoint |
| `MATRICULE` | VARCHAR | agent |
| `DATE_CLOT` | TIMESTAMP | date de clôture |

**Déduplication obligatoire.** Plusieurs lignes peuvent exister pour un
même (centre, secteur, cycle). Il faut toujours garder la plus récente :

```sql
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY STR_ID, SECT_ID, ANNEE, MOIS
    ORDER BY IDCLOTURE DESC
) = 1
```

Sans ça, les volumes sont comptés deux fois.

### 3.3 `S_FACTURE_M` → `raw_facture_m` — 19 colonnes

| Colonne | Type | Signification |
|---|---|---|
| `FACT_ID` | BIGINT **PK** | identifiant technique |
| `FACT_NUM` | BIGINT | numéro de facture |
| `STR_ID` | BIGINT | code du centre |
| `SECT_ID` | BIGINT | code du secteur |
| `ABN_ID` | BIGINT | abonnement facturé |
| `CLT_ID` | BIGINT | client |
| `REL_ID` | BIGINT | **relevé d'origine** — lien vers `S_RELEVE` |
| `TOUR_ID` | BIGINT | tournée |
| `FACT_DATE` | TIMESTAMP | date d'émission |
| `FACT_ANNEE` | BIGINT | année du cycle facturé |
| `FACT_PERIODE` | BIGINT | **mois** du cycle facturé |
| `TYPE_FACT` | BIGINT | **type de facture** (voir § 5.3) |
| `CODE_FACTURE` | BIGINT | code |
| `FAT_QTE_CONS` | DOUBLE | **volume facturé par abonné** (m³) |
| `FACT_MNT` | DOUBLE | montant |
| `FACT_MNT_TTC` | DOUBLE | montant TTC — en réalité le **solde** |
| `NBESTIM` | BIGINT | nombre d'estimations |
| `FLAGANNULLE` | BIGINT | **1 = facture annulée** |
| `FLAG_REMB` | BIGINT | remboursement |

Trois règles non négociables :

```sql
-- 1. Le cycle d'une facture
FACT_ANNEE * 100 + FACT_PERIODE = cycle_key

-- 2. Toujours écarter les annulées
WHERE COALESCE(FLAGANNULLE, 0) = 0

-- 3. FACT_MNT_TTC n'est pas le TTC
--    Le dashboard l'expose comme SOLDE, et calcule :
--    ARRIERES = FACT_MNT_TTC - FACT_MNT
```

### 3.4 `S_FACTURE_DET` → `raw_facture_det` — 8 colonnes

Pas de clé primaire simple : la clé est composite `(FACT_ID, FDET_LIGNE)`.

| Colonne | Type | Signification |
|---|---|---|
| `FACT_ID` | BIGINT | facture mère |
| `FDET_LIGNE` | BIGINT | numéro de ligne |
| `TARF_TR_ID` | BIGINT | **tranche tarifaire** (1, 2, 3) |
| `PRD_ID` | BIGINT | **nature de la ligne** (voir § 5.4) |
| `FDET_QTE` | DOUBLE | quantité de la tranche |
| `FDET_PU` | DOUBLE | prix unitaire |
| `FDET_PTHT` | DOUBLE | montant HT |
| `FDET_TTVA` | DOUBLE | TVA |

### 3.5 `S_ABONNEMENT` → `raw_abonnement` — 20 colonnes

| Colonne | Type | Signification |
|---|---|---|
| `ABN_ID` | BIGINT **PK** | **la « référence » de l'abonné** |
| `STR_ID` | BIGINT | centre |
| `SECT_ID` | BIGINT | secteur d'affectation |
| `CLI_ID` | BIGINT | client |
| `ABN_RES` | BIGINT | **0 = actif** — le dashboard filtre là-dessus |
| `ABN_ADRESSE` | VARCHAR | **adresse** |
| `COMP_ADRESSE` | VARCHAR | complément d'adresse |
| `QUARTIER` | VARCHAR | quartier |
| `NLOT` | VARCHAR | numéro de lot |
| `ABN_DATE` | TIMESTAMP | date d'abonnement |
| `TAR_ID` | BIGINT | code tarif |
| `GRP_ID` | BIGINT | groupe |
| `PAY_ID` | BIGINT | code paiement |
| `TOUR_ID` | BIGINT | tournée |
| `ANCIENNE_REFRENCE` | VARCHAR | **ancienne référence** |
| `ANC_REF_MIG` | VARCHAR | référence de migration |
| `ANC_MNT_ASC` | DOUBLE | ancien montant |
| `FLAG_ASS` | BIGINT | assainissement |
| `FLAG_TAXE` | BIGINT | taxe |
| `CAL_ID` | BIGINT | calibre du compteur |

⚠️ `ANCIENNE_REFRENCE` — la faute d'orthographe (« REFRENCE » sans le
deuxième E) est **dans Oracle**. Il faut l'écrire ainsi, sinon la colonne
est introuvable.

### 3.6 `S_COMPTEUR_INS` → `raw_compteur` — 7 colonnes

| Colonne | Type | Signification |
|---|---|---|
| `ID` | BIGINT **PK** | identifiant de pose |
| `ABN_ID` | BIGINT | abonnement |
| `CPT_SERIE` | VARCHAR | **numéro gravé sur le compteur** |
| `CPI_REF` | VARCHAR | référence compteur |
| `CPI_ACTIF` | BIGINT | actif |
| `CPI_DATE_INS` | TIMESTAMP | date de pose |
| `CPI_DATE_DEPOSE` | TIMESTAMP | date de dépose — **NULL = encore posé** |

Un abonné a plusieurs lignes dans l'historique. Pour obtenir le compteur
courant, il faut dédupliquer :

```sql
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY ABN_ID
    ORDER BY CPI_DATE_INS DESC NULLS LAST, ID DESC
) = 1
```

Le dashboard filtre en plus `CPI_DATE_DEPOSE IS NULL` à l'extraction.

### 3.7 `S_CLIENT` → `raw_client` — 9 colonnes

| Colonne | Type | Signification |
|---|---|---|
| `CLT_ID` | BIGINT **PK** | client |
| `CLT_TITRE` | VARCHAR | civilité |
| `CLT_NOM` | VARCHAR | nom |
| `CLT_PRENOM` | VARCHAR | prénom |
| `CLT_NOMCONCAT` | VARCHAR | nom complet — c'est celui qu'affiche l'EGF |
| `CLT_NNI` | VARCHAR | numéro national d'identité |
| `CLT_MOBILE` | VARCHAR | mobile |
| `CLT_TELFIXE` | VARCHAR | fixe |
| `ACT_ID` | BIGINT | code activité |

### 3.8 Tables de référence

```sql
-- ref_centre : les centres
SELECT STR_ID AS CENTRE_ID, STR_LIB_LT AS CENTRE_LIB, ZONE_ID
FROM CRM_SNDE.S_STRUCTURE
WHERE ZONE_ID IN (2, 3)

-- ref_secteur : les secteurs
SELECT SECT_ID,
       SECT_LIBLT AS SECT_LIB,
       TRIM(CAST(SECT_CODE AS VARCHAR2(50))) AS SECT_CODE,
       STR_ID, MAT_RELEV
FROM CRM_SNDE.S_SECTEUR

-- ref_comptage : les états de comptage
SELECT ID_COMP, LIBELLE FROM CRM_SNDE.COMTAGE   -- colonnes réelles : ID_COMP, LIBELLE, LIB_AR
```

| Table | Colonnes |
|---|---|
| `ref_centre` | `CENTRE_ID` **PK**, `CENTRE_LIB`, `ZONE_ID` |
| `ref_secteur` | `SECT_ID` **PK**, `SECT_LIB`, `SECT_CODE`, `STR_ID`, `MAT_RELEV` |
| `ref_comptage` | `ID_COMP` **PK**, `LIBELLE` |

Deux pièges :

- `SECT_CODE` est **numérique** dans Oracle. Sans `CAST` puis `TRIM`, elle
  ressort avec des espaces et les comparaisons de chaînes échouent.
- `MAT_RELEV` est le matricule du releveur **affecté au secteur**, utile
  pour savoir qui suivre quand la saisie n'avance pas.

### 3.9 Planification

```sql
-- raw_planif_releve  (entête, une ligne par secteur et année)
PLAN_REL_ID, SECT_ID, STR_ID, SEMAINEDEBT, ANNEE

-- raw_planif_detaille (détail par semaine)
IDDET (PK), NUMSEMAINE, PLAN_REL_ID, TYPE,
DATEDEBUTSEMAINE, DATEDEBUTFACT, MOIS, ANNEE
```

`DATEDEBUTSEMAINE` est la **deadline** dont se sert l'onglet Retards.
`DATEDEBUTFACT` est la date de début de relève.

---

## 4. Tables propres au dashboard

Elles n'existent pas dans Oracle : c'est le dashboard qui les produit.

| Table | Rôle | Colonnes |
|---|---|---|
| `alerts_log` | alertes de franchissement de seuil | `alert_id` PK, `alert_type`, `STR_ID`, `SECT_ID`, `ANNEE`, `MOIS`, `pct_avant`, `pct_apres`, `detected_at`, `acknowledged`, `acknowledged_at` |
| `conso_alerts_log` | alertes « consommation modifiée » | `alert_id` PK, `snapshot_id`, `ABN_ID`, `STR_ID`, `SECT_ID`, `ANNEE`, `MOIS`, `verdict`, `CONSO_CAT_AVANT`, `CONSO_AVANT`, `CONSO_CAT_FACTUREE`, `CONSO_FACTUREE`, `ecart`, `FACT_NUM`, `FACT_DATE`, `MONTANT`, `detected_at`, `acknowledged`, `acknowledged_at` |
| `deadline_alerts_log` | alertes de deadline | `alert_id` PK, `STR_ID`, `SECT_ID`, `ANNEE`, `MOIS`, `deadline`, `deadline_releveur`, `pct_avancement`, `nbabo`, `marge_adjoint`, `urgence`, `detected_at`, `acknowledged`, `acknowledged_at` |
| `snapshot_anomalies_pre_cloture` | état figé des anomalies au basculement `CLOTURE 0→1` | `snapshot_id` PK, `REL_ID`, `ABN_ID`, `CPT_REF`, `STR_ID`, `SECT_ID`, `ANNEE`, `MOIS`, `REL_INDEX_AVANT`, `REL_ANCIEN_INDEX_AV`, `REL_CONSOM_AVANT`, `CONSO_CAT_AVANT`, `ID_COMP_AVANT`, `ETAT_COMPTAGE_AVANT`, `REL_VALIDE_AVANT`, `REL_ESTIMATIF_AVANT`, `cloture_pct`, `cloture_date`, `snapshot_at` |
| `cloture_snapshot_prev` | avancement du tour précédent, pour détecter un franchissement | `IDCLOTURE` PK, `STR_ID`, `SECT_ID`, `ANNEE`, `MOIS`, `POURCTRAITER`, `snapshot_at` |
| `pipeline_runs` | journal des chargements | `run_id`, `started_at`, `finished_at`, `status`, `rows_extracted`, `error_message` |
| `mart_build_state` | signature de chaque mart, pour éviter de le reconstruire | `mart_name` PK, `signature`, `built_at` |

Le modèle des trois `*_alerts_log` est le même et vaut d'être repris :
une clé métier, les valeurs au moment de la détection, `detected_at`,
`acknowledged`, `acknowledged_at`. C'est ce qui permet un bandeau qui ne
disparaît qu'une fois consulté.

---

## 5. Codes et nomenclatures

### 5.1 `ID_COMP` — état du comptage

| Code | Libellé |
|---|---|
| 1 | Accessible |
| 2 | Illisible |
| 3 | Défectueux |
| 4 | Bloqué |
| 5 | Inaccessible |
| 6 | Volé |
| autre | Autre |

Les libellés viennent de `ref_comptage`, mais le dashboard double le
mapping en SQL dans `v_releve` pour ne pas dépendre de la jointure.

### 5.2 `CONSO_CAT` — catégorie de consommation

Calculée sur `REL_CONSOM_CALCUL`, pas stockée dans Oracle :

| Condition | Catégorie |
|---|---|
| `IS NULL` | Inconnue |
| `= 0` | **Nulle** |
| `> 0` et `< 5` | **Faible** |
| `> 300` | **Élevée** |
| sinon | Normale |

Les trois catégories en gras sont celles que le dashboard considère comme
des anomalies de consommation.

### 5.3 `TYPE_FACT` — type de facture

Établi en croisant `TYPE_FACT` avec l'état du compteur relevé sur le
cycle 10/2026 :

| Code | Libellé | Volumétrie observée |
|---|---|---|
| 0 | Relevée | 36 788 — compteurs accessibles |
| 1 | Estimée | 3 363 — inaccessibles + illisibles |
| 2 | Forfaitaire | 1 409 — bloqués + défectueux + volés |
| 3 | Arrêt de compte | abonnés résiliés, montant moyen négatif |
| 4 | Relevée (tarif particulier) | — |
| 5 | Relevée (tarif particulier) | — |
| 7 | Avoir / régularisation | — |

⚠️ Ce mapping reste **à confirmer** avec la direction de la facturation :
il a été déduit des données, pas lu dans une documentation. Le type 0
représente 86 % des factures.

### 5.4 `PRD_ID` — nature d'une ligne de facture

| Code | Nature |
|---|---|
| 20 | consommation d'eau, par tranche (`TARF_TR_ID` = 1, 2, 3) |
| 22 | redevance |
| 25 | exonération |
| 0 | arriéré |

### 5.5 `ZONE_ID` — zones

| Code | Zone |
|---|---|
| 1 | INTERIEUR |
| 2 | NOUAKCHOTT |
| 3 | NOUADHIBOU |

Périmètre du dashboard : `ZONE_IDS = "2,3"`.

### 5.6 Pseudo-structures à exclure

Six `STR_ID` ne sont **pas** des centres d'exploitation : ce sont des
directions et des entités « zone », sans abonné relevable.

| STR_ID | Libellé |
|---|---|
| 1 | DIRECTION SNDE |
| 2 | ZONE NOUAKCHOTT |
| 5 | ZONE NOUADHIBOU |
| 6 | ZONE INTERIEUR |
| 54 | DIR. TECHNICO-COMMERC. |
| 63 | Khadamaty |

`TSTR_ID` **ne permet pas** de les distinguer : les vrais centres et ces
entités partagent `TSTR_ID = 1`. D'où la liste explicite. Le contrôle qui
le confirme : un vrai centre porte des secteurs, ces six-là en ont zéro.

---

## 6. Le code du centre, trois noms pour la même valeur

| Nom | Où |
|---|---|
| `STR_ID` | toutes les tables `raw_*`, `ref_secteur`, et les paramètres d'API |
| `CENTRE_ID` | `ref_centre` **uniquement** |
| `centre_code` / `CENTRE_CODE` | les vues et les marts |

La jointure est donc toujours de cette forme :

```sql
LEFT JOIN ref_centre ce ON ce.CENTRE_ID = x.STR_ID
```

---

## 7. Vues dérivées

### `v_releve`

`raw_releve` + libellés + colonnes calculées, dédupliqué par
`ROW_NUMBER()` sur l'ensemble des compteurs.

Colonnes ajoutées : `MATRICULE` (repli sur `REL_MAT`), `CPT_REF` (repli du
`CPT_SERIE` sur le `CPT_REF` du relevé), `ADRESSE`, `COMP_ADRESSE`,
`COMPTAGE_LIB`, `CENTRE_LIB`, `ZONE_ID`, `ZONE_LIB`, `SECT_LIB`,
`SECT_CODE`, `ETAT_COMPTAGE`, `CONSO_CAT`.

### `v_secteur_cycle`

Un enregistrement par (centre, secteur, cycle), avec l'univers élargi au
cycle de référence N-2 pour ne pas perdre les secteurs pas encore
ouverts.

Colonnes notables : `centre_code`, `centre_libelle`, `ZONE_ID`,
`ZONE_LIB`, `secteur_code`, `secteur_libelle`, `ANNEE`, `MOIS`,
`cycle_key`, `parc_cloture`, `parc_reference`, `parc_abonnement_actif`,
`saisis_declare`, `saisis_reel`, `restant`, `pct_avancement`,
`volume_facture`, `volume_estime`, `pct_estimation`, `CLOTURE`,
`FLAG_CALCUL`, `FLAG_VALID`, `statut`, `avancement_cat`,
`flag_seuil_alerte`, plus les 4 `nb_conso_*` et les 6 `nb_*` d'état de
comptage.

`statut` : `Validé` · `À valider` · `Non ouvert` · `En cours` · `Autre`.

### `v_egf`

L'EGF, construite à partir de **8 sources** : `raw_facture_m` (base),
`raw_facture_det` (pivot des tranches), `raw_releve` (via `REL_ID`),
`raw_abonnement`, `raw_client`, `raw_compteur` (dédupliqué),
`ref_centre`, `ref_secteur`. Filtre `COALESCE(FLAGANNULLE, 0) = 0`.

Elle expose notamment `REFERENCE` (= `ABN_ID`), `ANCIENNE_REFERENCE`,
`NOM_CLIENT`, `ADRESSE`, `NUM_COMPTEUR` (= `CPT_SERIE`), `INDEX_DEBUT`,
`INDEX_FIN`, `NB_JOURS`, `CONSOMMATION` (= `FAT_QTE_CONS`), les tranches
`QTE_TR1..3` / `PU_TR1..3` / `MNT_TR1..3`, `REDEVANCE`, `EXONERATION`,
`TVA`, `MONTANT`, `SOLDE`, `ARRIERES`.

### `v_deadline_secteur`

`raw_cloture_secteur` × `raw_planif_releve` × `raw_planif_detaille`.
Calcule `deadline_releveur = DATEDEBUTSEMAINE − marge`, la marge dépendant
du parc :

| `NBABO` | Marge |
|---|---|
| < 100 | 1 jour |
| 100–300 | 2 jours |
| 301–1000 | 3 jours |
| > 1000 | 4 jours |

`urgence` : `OK` · `PAS_DE_PLANNING` · `NORMAL` · `DEADLINE_PROCHE` ·
`DEPASSE`. Recalculée à chaque requête à partir de `now()`.

---

## 8. Règles transverses à ne jamais oublier

1. **Dédupliquer `CLOTURE_SECTEUR`** par `IDCLOTURE DESC`.
2. **Écarter les factures annulées** : `COALESCE(FLAGANNULLE, 0) = 0`.
3. **Exclure les six pseudo-structures.**
4. **Filtrer les zones** : `ZONE_ID IN (2, 3)`.
5. **Cycle d'une facture** : `FACT_ANNEE * 100 + FACT_PERIODE`.
6. **Relève bimestrielle** : le cycle de comparaison est **N-2**, le
   précédent de même parité. Jamais N-1.
7. **Parc actif** : `ABN_RES = 0`.
8. **Compteur courant** : dédupliquer `raw_compteur` par
   `CPI_DATE_INS DESC, ID DESC`.
9. **`COUNT(DISTINCT CPT_REF)`, pas `COUNT(*)`**, pour compter les
   compteurs relevés. Un compteur relevé deux fois dans le cycle ferait
   dépasser 100 %. C'est le bug qui touche 407 secteurs dans
   `releve_stats` du dashboard : le dénominateur est en `DISTINCT`, les
   catégories en `COUNT(*)`.
10. **`FULL OUTER JOIN` entre deux cycles.** Un secteur ou une référence
    présent en N-2 et absent en N est la pire anomalie qui existe ; une
    jointure classique le ferait disparaître du résultat.

---

## 9. Ce que l'application 2 utilise

| Table | Utilisée | Pour quoi |
|---|---|---|
| `CLOTURE_SECTEUR` | ✅ | volumes et avancement par secteur |
| `S_FACTURE_M` | ✅ | volume facturé par abonné |
| `S_RELEVE` | ✅ | index, état du comptage, suivi quotidien |
| `S_ABONNEMENT` | ✅ | parc et adresses |
| `S_COMPTEUR_INS` | ✅ | numéro de compteur |
| `S_STRUCTURE` · `S_SECTEUR` · `COMTAGE` | ✅ | libellés |
| `S_CLIENT` | ❌ | nom du client — à ajouter si besoin d'appeler l'abonné |
| `S_FACTURE_DET` | ❌ | tranches tarifaires — utile pour expliquer une baisse de montant plutôt que de volume |
| `PLANIF_RELEVE` · `PLANIFDETAILLE` | ❌ | deadlines — utile pour dire si un secteur est en retard, pas seulement en cours |

Les trois dernières lignes sont les extensions naturelles si l'on veut
aller plus loin.

---

*Source : `backend/src/extractor.py` et `backend/src/marts.py` du
dashboard de suivi temps réel. En cas de doute, c'est le code qui fait
foi, pas ce document.*
