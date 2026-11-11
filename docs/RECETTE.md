# Recette bout en bout — scénarios R1 à R13 (§22 du cahier des charges)

Ce document est la **recette exécutable** du projet Jauge : chacun des treize
scénarios du chapitre 22 du cahier des charges (`docs/fr/cahier-des-charges-finops-ia.md`)
est couvert par un test `pytest` qui exerce le **code réel** des modules
fusionnés sur `main`, sur `MemoryStore` et avec les adaptateurs fictifs fournis
par les modules (`FakeModeleClient`, `FakeJugeClient`, `FakePasserelleClient`,
`FakeQualiteEnLigne`).

**Aucun appel réseau, aucun compte AWS, aucune donnée réelle.** Le trafic
utilisé est celui du générateur synthétique `demo/traffic.py` ; les montants sont
recalculés par le moteur de coût du socle (`jauge.engine.calculator`), jamais par
un modèle de langage (ENF-IA-01).

## 1. Lancer la recette

```bash
export PATH="$HOME/.local/bin:$PATH"
uv sync --all-groups

# La recette seule (59 tests : 59 réussis, 0 `xfail` — écarts É1/É2 corrigés, voir §4)
uv run pytest tests/recette -q

# Un scénario précis
uv run pytest tests/recette/test_r4_r5_campagne.py -q

# La CI complète (lint, tailles, tests, gabarits CloudFormation)
bash scripts/ci-local.sh
```

Durée observée de la recette seule : **environ 15 secondes**.

Arborescence de la recette :

| Fichier | Contenu |
|---|---|
| `tests/recette/conftest.py` | Fixtures partagées : store, table de tarifs, API en mode démo, écriture d'un journal NDJSON |
| `tests/recette/outillage.py` | Outillage de campagne : tirage, rejeu, notation, contrôles, consolidation (§13.1 à §13.4) |
| `tests/recette/test_r1_r2_attribution.py` | R1, R2 (+ interopérabilité M12 ↔ M1) |
| `tests/recette/test_r3_leviers.py` | R3 |
| `tests/recette/test_r4_r5_campagne.py` | R4, R5 |
| `tests/recette/test_r6_r7_r8_bascule.py` | R6, R7, R8 |
| `tests/recette/test_r9_plafond.py` | R9 |
| `tests/recette/test_r10_r13_infra_docs.py` | R10, R13 |
| `tests/recette/test_r11_cloisonnement.py` | R11 |
| `tests/recette/test_r12_economie.py` | R12 |

## 2. Couverture des scénarios

| Réf. | Scénario du §22 | Test | Fichier | Ce qui est vérifié |
|---|---|---|---|---|
| **R1** | Mise en service d'un client | `test_r1_mise_en_service_produit_un_premier_cout` | `test_r1_r2_attribution.py` | Création du client puis de l'application via l'API, table de tarifs datée de `data/pricing/`, lecture des journaux du premier jour, agrégation, et **premier coût par application** lu sur `GET /v1/applications/{id}/costs` |
| **R2** | Journée de trafic de démonstration | `test_r2_journee_demo_100_pourcent_attribuee_et_ecart_affiche`, `test_r2_store_amorce_par_la_demo_expose_une_couverture_100`, `test_r2_le_parseur_m1_lit_les_journaux_demo_bruts`, `test_r2_le_parseur_m1_attribue_les_lignes_cur_demo` | `test_r1_r2_attribution.py` | Une journée du générateur `demo/` : **100 % des requêtes attribuées**, aucune dépense non attribuée, **écart avec la facture CUR 2.0 calculé, affiché et sous 3 %** ; et **interopérabilité M12 ↔ M1** : le parseur M1 lit les journaux et les lignes CUR de la démo **bruts** (champ `modelId`, colonnes `resource_tags_user_*`), sans rejet ni dépense non attribuée |
| **R3** | Classement sur un grand modèle | `test_r3_levier_modele_surdimensionne_detecte_avec_economie`, `test_r3_levier_publie_sur_l_api`, `test_r3_detection_reproductible` | `test_r3_leviers.py` | Le détecteur **M3 réel** (`detecter_leviers`) trouve le levier « modèle surdimensionné » sur le trafic du classement, **avec une économie estimée > 0**, un candidat moins cher proposé, des indices cités, et le levier publié et classé par l'API |
| **R4** | Campagne 200 requêtes / 3 candidats | `test_r4_campagne_200_requetes_3_candidats` | `test_r4_r5_campagne.py` | Tirage stratifié de **200 requêtes**, rejeu sur la référence et **3 candidats** au niveau Flex, notation par le juge, contrôles déterministes : chaque candidat expose **taux, intervalle de confiance, latence (p50/p95) et coût par requête**, cohérents avec les verdicts et les tarifs ; résultats publiés sur `GET /v1/campaigns/{id}/results` |
| **R5** | Candidat sous le seuil | `test_r5_candidat_sous_le_seuil_rejette_avec_familles_en_echec` | `test_r4_r5_campagne.py` | Le petit modèle sous le seuil de 97 % de l'assistant : `decider` **refuse** le candidat, motive le refus (condition « taux_equivalence » non remplie) et cite les **familles de requêtes en échec** ; le taux est lu à côté de l'auto-accord du modèle actuel |
| **R6** | Recommandation sans approbation | `test_r6_bascule_impossible_sans_approbation`, `test_r6_bascule_refusee_si_recommandation_non_approuvee`, `test_r6_api_refuse_la_bascule_sans_approbation` | `test_r6_r7_r8_bascule.py` | `ApprobationRequiseError` du contrôleur M6 sans approbation et avec une recommandation non approuvée ; **409 `approval_required`** via l'API, **aucune bascule créée**, refus journalisé |
| **R7** | Bascule approuvée | `test_r7_bascule_approuvee_franchit_les_paliers` | `test_r6_r7_r8_bascule.py` | Règle de passerelle créée à **10 %**, paliers **10 → 50 → 100** franchis, **qualité en ligne mesurée à chaque palier**, bascule close en `APPLIED`, mesures conservées pour l'audit |
| **R8** | Qualité en ligne dégradée | `test_r8_qualite_degradee_retire_la_regle_automatiquement`, `test_r8_api_peut_rollback_une_bascule_en_cours` | `test_r6_r7_r8_bascule.py` | Une mesure sous le seuil **retire la règle** de la passerelle, positionne `annule_automatiquement`, renseigne le motif, **émet une alerte** et bascule la recommandation en `ROLLED_BACK` |
| **R9** | Campagne au-dessus du plafond | `test_r9_campagne_au_dessus_du_plafond_refusee`, `test_r9_campagne_sous_le_plafond_acceptee_avec_devis`, `test_r9_api_refuse_la_campagne_au_dessus_du_plafond` | `test_r9_plafond.py` | `PlafondDepasseError` **avant tout appel de modèle**, devis affiché (200 requêtes × 3 candidats = 1 000 exécutions et 800 jugements, §17.1), et **422 `campaign_cost_cap_exceeded`** via l'API, campagne refusée non enregistrée |
| **R10** | Rôle du MSP sur le compartiment des journaux | `test_r10_role_msp_sans_droit_de_lecture_sur_les_journaux`, `test_r10_role_msp_refuse_explicitement_les_compartiments_sensibles`, `test_r10_role_msp_ne_peut_pas_invoquer_de_modele_ni_lire_le_texte`, `test_r10_approbation_du_role_exige_l_identifiant_externe` | `test_r10_r13_infra_docs.py` | Sur le gabarit `infra/client-account/template.yaml` : **aucun** `s3:GetObject`/`s3:GetObjectVersion`/`s3:ListBucket`/`s3:ListBucketVersions` du rôle du MSP sur le bucket des journaux ni sur celui des résultats ; refus explicite `s3:*` ; aucun droit `bedrock:` ; approbation par identifiant externe |
| **R11** | Utilisateur du client A sur les données du client B | `test_r11_client_a_refuse_sur_les_donnees_du_client_b` (17 cas), `test_r11_refus_journalise_dans_audit`, `test_r11_client_a_accede_a_ses_propres_donnees`, `test_r11_le_store_ne_fuit_pas_les_donnees_du_client_b` | `test_r11_cloisonnement.py` | Parcours de l'API FastAPI en **mode démo** (en-têtes `X-Jauge-*`) sur un store amorcé par la démo : **403 `tenant_isolation_violation` sur chaque point d'API** qui expose des données du client B, refus journalisé dans l'audit, contrôle positif sur les données du client A |
| **R12** | Économie après bascule | `test_r12_economie_apres_bascule_a_volume_constant`, `test_r12_economie_coherente_avec_la_facture_cur`, `test_r12_api_expose_les_economies_du_client` | `test_r12_economie.py` | Coût par requête **avant et après à volume constant** (même trafic, modèle facturé changé), économie constatée égale à l'économie attendue, confirmation **cohérente avec la facture CUR 2.0** par étiquette, et publication des économies par l'API |
| **R13** | Installation dans un compte vierge | `test_r13_les_fichiers_cites_dans_la_documentation_existent`, `test_r13_chaque_commande_documentee_passe_par_un_fichier_existant` (9 cas), `test_r13_le_script_de_deploiement_est_executable_et_coherent`, `test_r13_le_guide_dinstallation_decrit_chaque_parametre_du_gabarit`, `test_r13_le_readme_pointe_vers_le_gabarit_et_le_plan_de_controle` | `test_r10_r13_infra_docs.py` | Les fichiers cités par le README et `docs/deploy-client.md` existent ; **chaque commande `aws`/`cdk`/`uv` documentée** correspond à un fichier réel ; `deploy.sh` est exécutable et passe les paramètres du gabarit ; le guide décrit **tous** les paramètres du gabarit |

## 3. Ce qui reste manuel

Le déploiement réel ne peut pas être exécuté dans une recette hors ligne. Ces
points restent à faire **à la main** sur un compte AWS de démonstration :

| Point | Pourquoi il reste manuel | Comment le faire |
|---|---|---|
| **R1** — arrivée réelle des journaux d'invocation | La recette écrit un journal NDJSON local ; les journaux réels arrivent de Bedrock vers S3 | Activer la journalisation Bedrock vers le bucket du client, puis pointer le lecteur sur `s3://<logs-bucket>/AWSLogs/<compte>/BedrockModelInvocationLogs/` |
| **R2** — facture CUR 2.0 réelle | La recette fabrique un export CUR 2.0 synthétique (`demo/cur.py`) | Brancher l'export CUR 2.0 du client, puis comparer le recalcul et la facture sur 14 jours |
| **R7/R8** — règle AgentCore Gateway réelle et juge en ligne réel | La recette utilise `FakePasserelleClient` et `FakeQualiteEnLigne` | Créer la règle pondérée sur la cible AgentCore du client, brancher la tâche d'évaluation Bedrock comme juge en ligne, puis rejouer la bascule |
| **R8** — alerte réelle (e-mail, SNS) | La recette vérifie l'alerte émise par le contrôleur, pas sa diffusion | Brancher le topic SNS du client sur la distribution d'alerte |
| **R10** — évaluation réelle des droits IAM | La recette lit le gabarit ; elle n'assume pas le rôle | Déployer la pile cliente, puis `aws sts assume-role` avec l'identifiant externe et tenter une lecture du bucket des journaux : l'accès doit échouer |
| **R13** — installation complète dans un compte vierge | La recette vérifie les artefacts et les commandes, pas le déploiement | `./infra/client-account/deploy.sh --msp-account … --logs-bucket …` puis `cdk deploy` depuis `infra/control-plane/` |

## 4. Écarts constatés (à traiter hors de ce module)

Ces écarts ont été constatés **pendant la recette**. Aucun module métier n'a
été modifié : la recette teste la spécification, signale l'écart et, quand
l'écart est de nature à interdire le test, porte un `xfail` documenté.

Les écarts d'interopérabilité **É1** et **É2** ont été **corrigés le
2026-11-11** (PR [#21](https://github.com/BaptisteFaisy/jauge/pull/21)
« Interop démo ↔ attribution : modelId dans les journaux, colonnes CUR 2.0
réelles ») : c'est le générateur de démonstration (M12) qui émet désormais les
vrais noms de champs AWS, le parseur M1 restant inchangé. Les deux tests
`xfail` sont devenus des tests verts ; la recette ne compte plus aucun `xfail`.

| N° | Écart | Impact | Test |
|---|---|---|---|
| **É1** | **Corrigé le 2026-11-11** : `demo/traffic.py` n'émettait pas le champ `modelId` des journaux d'invocation Bedrock réels ; le parseur M1 rejetait 100 % des lignes de la démo (« model_id absent »). La démo émet désormais `modelId` (à côté de `requestId`, `eventTime`, …). | Aucun : le parseur M1 lit les lignes CloudWatch de la démo **brutes**, sans rejet ni adaptation. | `test_r2_le_parseur_m1_lit_les_journaux_demo_bruts` (vert) |
| **É2** | **Corrigé le 2026-11-11** : `demo/cur.py` produisait les colonnes `tag_application`, `tag_team`, `tag_environment`, inconnues du parseur CUR de M1 (`CHAMPS_CUR`). La démo émet désormais les vraies colonnes CUR 2.0 d'étiquettes utilisateur : `resource_tags_user_application`, `resource_tags_user_team`, `resource_tags_user_environment`. | Aucun : les lignes CUR de la démo sont attribuées à 100 % par M1 et le rapprochement quotidien M12 → M1 affiche un écart < 3 %. | `test_r2_le_parseur_m1_attribue_les_lignes_cur_demo` (vert) |
| **É3** | **Corrigé le 2026-11-11** (PR #19, M17) : `demo/seed.py` utilise désormais `ServiceDetectionLeviers` → `detecter_leviers` (M3). (`demo/analyse.py:construire_leviers` reste pour ses propres tests de scénario.) | Aucun : la démo exerce le moteur de règles réel. | `test_r3_levier_modele_surdimensionne_detecte_avec_economie` |
| **É4** | L'API `POST /v1/applications/{id}/campaigns` (port de démonstration) estime le coût avec la **moyenne observée par requête** du store, et non avec le devis du module M4 (`jauge/replay/cost.py`, tarif Flex + coût du juge). | L'estimation affichée par l'API et le devis de référence peuvent différer ; seul le plafond est appliqué dans les deux cas. Le module M4 reste la source de vérité (ENF-COUT-01). | `test_r9_api_refuse_la_campagne_au_dessus_du_plafond` |
| **É5** | **Corrigé le 2026-11-11** (M18, `fix/finitions`) : l'API délègue au port `BasculePort` (`AdaptateurBascules` → `ControlleurBascule`, M6), crée une règle pondérée sur la passerelle et expose `POST /v1/rollouts/{id}/advance`. | Aucun : règle réelle créée, paliers 10 → 50 → 100 et retrait automatique sur rollback. | `test_bascule_creee_par_le_controleur_m6`, `test_paliers_avancent_jusqu_a_100_pourcent` |
| **É6** | Le README (« What is in this repository today » et « Repository layout ») présente encore M1 (attribution), M3 (leviers), M6 (rollout), M8 (API), M9 (rapports), M11 (infrastructure client), M12 (démo) et M13 (portail) comme « Planned », alors qu'ils sont fusionnés sur `main`. | Un juge qui lit le README peut croire que ces modules manquent. Le README est un fichier verrouillé : la correction est signalée ici, elle n'a pas été faite dans ce module. | `test_r13_le_readme_pointe_vers_le_gabarit_et_le_plan_de_controle` (couverture partielle) |
| **É7** | **Corrigé le 2026-11-11** (M18, `fix/finitions`) : `CreationTenant` accepte `schema_attribution` (avec valeurs par défaut) et `POST/GET /v1/tenants` le rendent. | Aucun : le schéma d'attribution est configurable à l'onboarding via l'API (EF-ATT-03). | `test_schema_attribution_accepte_et_rendu`, `test_schema_attribution_par_defaut` |

## 5. Traçabilité

Les tests citent en docstring les exigences visées : EF-ATT-02..08 (R1, R2),
EF-LEV-01 et EF-LEV-06 (R3), EF-REJ-01..04 et EF-EVA-02..03 (R4, R5),
EF-REC-02 et ENF-IA-02 (R6), EF-BAS-01..03 (R7, R8), ENF-COUT-01 (R9),
ENF-SEC-01 et §16 (R10), ENF-SEC-02 (R11), EF-VER-01..02 (R12), ENF-REP-01
(R13). Les chiffres vérifiés sont tous calculés par le code (ENF-IA-01) et
aucune assertion ne transporte de texte de requête (ENF-CONF-01).
