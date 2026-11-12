# Backlog d'idées — 999 pistes pour continuer Jauge

Réservoir d'idées généré le **2026-11-11** par huit sous-agents en parallèle, un
par axe, à partir de l'état réel de `main` (1 109 tests, 18 modules livrés,
portail publié). **999 idées** au total — un agent a sauté un numéro dans sa
numérotation, d'où 999 et non 1 000.

Ce document n'est **pas** un plan : la quasi-totalité de ces idées ne mérite pas
d'être faite. Il sert à deux choses :

1. ne rien perdre de ce qui a été exploré ;
2. rendre le **top 10** ci-dessous discutable — chaque piste retenue pointe vers
   les idées brutes qui l'ont nourrie.

| Axe | Idées | Fichier |
|---|---|---|
| Produit & expérience utilisateur | 125 | [`01-produit-ux.md`](01-produit-ux.md) |
| Science de la mesure & du rejeu | 125 | [`02-mesure-rejeu.md`](02-mesure-rejeu.md) |
| Ingénierie, fiabilité & opérations | 125 | [`03-ingenierie-fiabilite.md`](03-ingenierie-fiabilite.md) |
| Sécurité, confidentialité & conformité | 125 | [`04-securite-conformite.md`](04-securite-conformite.md) |
| Affaires, MSP & go-to-market | 125 | [`05-affaires-msp.md`](05-affaires-msp.md) |
| Agents & LLMOps | 125 | [`06-agents-llmops.md`](06-agents-llmops.md) |
| Intégrations, données & API | 124 | [`07-integrations-donnees.md`](07-integrations-donnees.md) |
| Recherche & paris de fond | 125 | [`08-recherche-paris.md`](08-recherche-paris.md) |

Critères de classement du top 10 : **(a)** renforce la thèse du produit — la
preuve par le rejeu, pas un tableau de bord de plus ; **(b)** faisable maintenant,
idéalement hors ligne, sans compte AWS ; **(c)** réduit le risque principal
(« peut-on faire confiance au juge ? ») ; **(d)** démontrable dans la vidéo et
devant un juge du hackathon.

---

## Top 10

**Bilan (2026-11-12).** Cinq pistes sur dix sont fusionnées dans `main` :
n° 4 (PR #48), n° 5 (PR #47), n° 6 (PR #51), n° 8 (PR #46), n° 9 (PR #45).
La branche `feat/integration-top10` les câble : exports publics, routeurs
`backtest`, `killswitch` et `benchmark`, garde kill-switch sur les routes qui
engagent un traitement, point `GET /v1/benchmarks/parc` et huitième règle de
levier. La suite compte **1 395 tests** verts, contre 1 109 à la génération de
ce backlog. Les cinq pistes restantes (n° 1, 2, 3, 7, 10) ne sont **pas
livrées** : du code partiel sans tests dort dans des worktrees ; la piste n° 7
n'a aucun fichier.

### 1. Protocole d'évaluation pré-enregistré + marge de non-infériorité

**Statut (2026-11-12).** Non livré — worktree `~/tmp/jauge-t1-protocole` :
`src/jauge/evaluation/protocole.py` et `src/jauge/replay/scellement.py` écrits,
non committés, sans tests.

**Quoi.** Figer, *avant* le moindre rejeu, un protocole immuable : métriques,
prompt du juge, seuils, plan d'échantillonnage, marge de non-infériorité
acceptée. Empreinte de hachage du protocole stockée avec la campagne ; l'échantillon
est tiré une fois et jamais re-sélectionné.

**Pourquoi maintenant.** C'est ce qui sépare « on a mesuré » de « on s'est
engagé sur une méthode, puis on a mesuré ». Sans pré-enregistrement, un
évaluateur peut toujours suspecter un choix de seuil après coup — et c'est la
première objection d'un juge technique.

**Effort.** Faible : un artefact JSON + un hachage + un champ `protocole` sur
`Campagne`. **Où.** `src/jauge/replay/`, `src/jauge/evaluation/`,
`src/jauge/models/campaign.py`.
**Idées brutes.** axe 2 n° 3, 4, 21, 69, 123, 125.

### 2. Suite de calibration du juge (biais mesurés, pas supposés)

**Statut (2026-11-12).** Non livré — worktree `~/tmp/jauge-t2-calibration` :
`calibration.py`, `aptitude_juge.py`, `jeux_calibration.py` et
`jeux_calibration_corpus.py` écrits, non committés, sans tests.

**Quoi.** Un jeu de calibration qui mesure le juge lui-même : aveuglement sur le
modèle producteur, ordre des réponses randomisé, tests de biais de position /
verbosité / formatage, **items-pièges** dorés glissés dans le lot, et calcul des
taux de faux-accept et faux-rejet.

**Pourquoi maintenant.** Le risque n° 1 du cahier des charges (§21) est « le
modèle juge se trompe ». Aujourd'hui on le mitigé (30 paires relues par un
humain, accord affiché). Le mesurer sur un jeu de calibration transforme une
hypothèse en chiffre publié dans la recommandation.

**Effort.** Moyen. **Où.** `src/jauge/evaluation/judge.py` + nouveau
`calibration.py`, résultat exposé dans l'Annexe B.
**Idées brutes.** axe 2 n° 10, 11, 12, 13, 79, 80, 81.

### 3. Portes déterministes à vérité terrain

**Statut (2026-11-12).** Non livré — worktree `~/tmp/jauge-t3-verite-terrain` :
`src/jauge/evaluation/bac_a_sable.py` écrit, non committé, sans tests.

**Quoi.** Là où une réponse objective existe, ne pas demander au juge : exécuter
le code généré dans un bac à sable et lancer les tests, valider le JSON contre le
schéma, comparer une réponse numérique à un solveur.

**Pourquoi maintenant.** Nos applications de démonstration (classement de
tickets, extraction) ont exactement ce profil : la vérité est vérifiable. Chaque
porte déterministe retire une part de la dépendance au juge — et c'est l'argument
le plus solide face à un acheteur sceptique.

**Effort.** Faible à moyen (le registre `CONTROLES_PAR_TACHE` existe déjà).
**Où.** `src/jauge/evaluation/checks.py`.
**Idées brutes.** axe 2 n° 28, 48, 49, 50, 51.

### 4. Qualité pondérée par le coût + régression détectée par strate

**Statut (2026-11-12).** Livré — PR #48 : `src/jauge/evaluation/ponderation.py`,
`src/jauge/evaluation/strates.py`, `src/jauge/engine/stats.py` ; tests
`tests/evaluation/test_ponderation.py`, `tests/evaluation/test_strates.py`,
`tests/engine/test_stats_ponderation.py`.

**Quoi.** Deux changements de lecture : le titre devient **qualité par dollar**
(et non qualité brute), et la décision se prend aussi **par strate** (type de
requête × application × longueur), avec alerte si une strate franchit sa propre
borne de confiance — même quand la moyenne tient.

**Pourquoi maintenant.** Une moyenne plate peut masquer une régression
localisée sur les requêtes rares mais critiques (le §13.6 le reconnaît déjà
comme limite). Pondérer par le coût aligne la preuve sur l'objectif FinOps.

**Effort.** Faible. **Où.** `src/jauge/evaluation/scoring.py`, `decision.py`,
`src/jauge/engine/stats.py`.
**Idées brutes.** axe 2 n° 17, 18, 19, 22, 23, 42, 105.

### 5. Backtest contrefactuel : « ce que ça aurait coûté le mois dernier »

**Statut (2026-11-12).** Livré — PR #47 : `src/jauge/replay/backtest.py`, point
d'API `GET /v1/applications/{app_id}/backtest` ; tests
`tests/replay/test_backtest.py`, `tests/api/test_route_backtest.py`.

**Quoi.** Rejouer un candidat sur les journaux du mois précédent et rendre
l'économie **contrefactuelle** en dollars, avec un intervalle P10/P50/P90 — sans
rien changer à la production.

**Pourquoi maintenant.** C'est l'artefact le plus convaincant pour un directeur
financier, et il est **calculable hors ligne dès aujourd'hui** avec les données
que la démo produit déjà. C'est aussi le meilleur plan de coupe disponible pour
la vidéo de trois minutes.

**Effort.** Moyen. **Où.** nouveau `src/jauge/replay/backtest.py`, une carte dans
le portail, un point d'API `GET /v1/applications/{id}/backtest`.
**Idées brutes.** axe 1 n° 59 ; axe 7 n° 21, 24, 87, 90.

### 6. « Proof pack » signé, reproductible par un tiers

**Statut (2026-11-12).** Livré — PR #51 : `src/jauge/reports/proof_pack.py` et
ses modules (`_coherence`, `_depot`, `_entrees`, `_rendu`, `_sections`,
`_verif_base`, `_verification`), `src/jauge/reports/scellement.py` ; rendus JSON
et Markdown, chaîne de hachage, dépôt mémoire/S3, rejeu de vérification ; 5
fichiers de tests.

**Quoi.** Un dossier de preuve auto-portant par campagne : échantillon figé,
réponses de référence et candidates, scores du juge avec explications, intervalle
de Wilson, version du protocole et du harnais, table des tarifs datée, économie
projetée — le tout scellé par une **chaîne de hachage** et stocké en écriture
seule (S3 Object Lock).

**Pourquoi maintenant.** Le modèle économique est une **part des économies
vérifiées** : sans artefact auditable, le montant facturé est négociable à
l'infini. C'est aussi ce qu'un auditeur (ou un juge de hackathon) peut revérifier
seul.

**Effort.** Moyen. **Où.** `src/jauge/reports/`, `src/jauge/evaluation/`,
`infra/client-account/` (compartiment scellé).
**Idées brutes.** axe 1 n° 22 ; axe 2 n° 39, 122, 123 ; axe 4 n° 40, 41, 48, 49.

### 7. Trafic fantôme puis canary avant la bascule

**Statut (2026-11-12).** Non livré — worktree `~/tmp/jauge-t7-shadow-canary` :
rien, aucun fichier.

**Quoi.** Deux étapes avant le trafic réel : le **shadow** répond en parallèle
sur un échantillon sans rien renvoyer à l'utilisateur, puis le **canary** bascule
un petit pourcentage avec déclencheur de retour arrière pré-engagé.

**Pourquoi maintenant.** Aujourd'hui la preuve est de laboratoire (rejeu) et la
bascule est directe (10 → 50 → 100). Le shadow comble l'écart entre les deux et
répond à l'objection « votre rejeu ne reproduit pas la production » (§21).

**Effort.** Moyen à important (nécessite le chemin passant). **Où.**
`src/jauge/rollout/`, règle de passerelle AgentCore.
**Idées brutes.** axe 2 n° 84, 85, 87, 88 ; axe 6 n° 33, 84.

### 8. Nouvelle famille de leviers : la diète de jetons prouvée par rejeu

**Statut (2026-11-12).** Livré — PR #46 : `src/jauge/levers/token_diet.py`,
`src/jauge/replay/variantes.py`, `src/jauge/replay/variantes_preuve.py` ; tests
dédiés.

**Quoi.** Optimiser le **prompt** au lieu du modèle : compresser le prompt
système, retirer les exemples inutiles, raccourcir les consignes de sortie — et
**prouver par rejeu** que la qualité tient à jetons réduits.

**Pourquoi maintenant.** Souvent un gain supérieur au changement de modèle, sans
toucher à l'architecture. Et surtout : personne ne le propose *avec preuve* —
c'est la même machinerie (rejeu, juge, règle de décision) appliquée à un autre
levier, donc un coût de développement faible pour un différenciateur fort.

**Effort.** Moyen. **Où.** `src/jauge/levers/` (nouvelle règle),
`src/jauge/replay/` (variantes de prompt), Annexe A du cahier des charges.
**Idées brutes.** axe 8 n° 81, 82, 83, 86, 87, 90 ; axe 6 n° 6, 19.

### 9. Kill-switch client + garde-fous de confidentialité des agrégats

**Statut (2026-11-12).** Livré — PR #45 : `src/jauge/api/killswitch.py`,
`src/jauge/api/routes/killswitch.py`, `src/jauge/reports/anonymisation.py` ; API
`POST`/`GET /v1/tenants/{id}/kill-switch` et `GET`/`PUT
/v1/tenants/{id}/confidentialite` ; tests `tests/api/test_killswitch.py`,
`tests/reports/test_anonymisation.py`.

**Quoi.** Un bouton qui arrête immédiatement tout rejeu et tout jugement pour un
client (et gèle l'accès aux données), plus un seuil de **k-anonymité** sur tout
agrégat qui quitte le compte du client, et du bruit différentiellement privé sur
les comparaisons inter-clients.

**Pourquoi maintenant.** C'est l'interrupteur de confiance qui fait signer un
client dont les prompts contiennent des données sensibles — et la condition
préalable à tout benchmark inter-clients (un argument commercial majeur).

**Effort.** Faible à moyen. **Où.** `src/jauge/api/`, `src/jauge/reports/`,
`infra/client-account/`.
**Idées brutes.** axe 4 n° 65, 66, 67, 104, 124 ; axe 8 n° 30, 39.

### 10. Catalogue de tarifs historisé + alerte de baisse de prix qui rouvre les preuves

**Statut (2026-11-12).** Non livré — worktree `~/tmp/jauge-t10-catalogue-prix` :
`pricing_data/catalogue.py` et `surveillance.py` écrits, non committés, sans
tests.

**Quoi.** Exposer la table des tarifs datée comme une API (prix courant et
historique par modèle, région, niveau), surveiller les changements de prix, et
**relancer automatiquement** les campagnes concernées quand un modèle devient
moins cher — ou quand un engagement réservé change le prix effectif.

**Pourquoi maintenant.** La table datée existe déjà (`data/pricing/`) : c'est un
actif dormant. Une baisse de prix chez AWS est un événement qui crée mécaniquement
de nouvelles économies à prouver — donc du chiffre d'affaires récurrent pour le
MSP, sans prospection.

**Effort.** Faible. **Où.** `src/jauge/pricing_data/`, nouvelle route API,
`src/jauge/levers/`.
**Idées brutes.** axe 7 n° 118, 119, 120, 121 ; axe 1 n° 101, 102, 103.

---

## Le pari commercial, si le produit doit vivre

Hors top 10 technique, mais décisif pour la suite : **AWS Marketplace + AWS Partner
Network (Competency Generative AI) + offres privées CPPO** (axe 5 n° 38 à 45),
avec une facturation à l'usage par bascule réussie plutôt qu'un abonnement plat.
Et, côté marché, l'**ingestion multi-cloud** (Azure OpenAI, Vertex AI, OpenAI
direct) qui transforme Jauge d'outil Bedrock en couche FinOps GenAI neutre
(axe 7 n° 60 à 63).

## Ce qui est volontairement écarté

- Les idées qui **recopient** un tableau de bord de coûts existant (Cost
  Explorer, Cost Optimization Hub) : c'est précisément ce que Jauge n'est pas.
- Les marchés internes de capacité, enchères et jetons négociables (axe 8
  n° 61 à 70) : séduisants sur le papier, aucun acheteur identifiable aujourd'hui.
- Le chiffrement homomorphe du rejeu (axe 8 n° 28) : hors de portée d'une équipe
  de cette taille à cet horizon — à garder comme veille.
