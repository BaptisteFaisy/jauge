# Hermes — résolveur de conflits (Jauge)

Portage du sous-agent de conflits de `BaptisteFaisy/duello`
(`hermes-conflict-resolver.yml`, ~1 630 lignes) vers des modules Python séparés,
testables et bornés, conformément au runbook `labkit-hermes-prompt.md` §6
(phase 3) et aux leçons §8.

## 1. Ce que ça fait

Une PR en retard sur `main` est rafraîchie par le gate (`hermes-lite.yml`,
rebase serveur). Une PR en **vrai conflit** ne peut pas l'être : le gate pose le
label `hermes/conflit`, publie un check rouge et **lance le résolveur**. Le
résolveur :

1. clone le dépôt dans un dossier **jetable** (jamais le workspace du runner) ;
2. rebase la branche de la PR sur `origin/main` ;
3. résout chaque conflit : jambe **InferHub** d'abord, repli **déterministe**
   ensuite ;
4. **valide avec la toolchain du dépôt** : `bash scripts/ci-local.sh`
   (ruff + limites de taille + pytest + cfn-lint) ;
5. pousse en `--force-with-lease` sur **la branche de la PR uniquement** ;
6. **rapporte** : commentaire marqué (mis à jour, un seul par PR) + check-run
   `Hermes résolveur (conflits)`.

Si la validation est rouge, **aucun push** : la branche distante reste intacte
et un humain est prévenu. Jamais d'échec silencieux.

## 2. Fichiers

| Fichier | Rôle |
|---|---|
| `.github/hermes/resolve.py` | décision pure : fichiers dérivés/binaires, hunks, garde-fou de sortie, jambe InferHub |
| `.github/hermes/resolve_selftest.py` | selftests du noyau (extraits pour garder `resolve.py` court) |
| `.github/hermes/rebase.py` | primitives git : rebase, résolution fichier par fichier, passes IA/déterministe |
| `.github/hermes/resolver.py` | orchestration : clone, validation, push, rapport |
| `.github/hermes/dispatch.py` | anti-doublon, backoff, escalade, dispatch du résolveur |
| `.github/hermes/watchdog.py` | réveil du gate quand une PR devient en conflit sans événement |
| `.github/hermes/gh.py` | accès API partagé (labels, commentaires, check-runs, dispatches) |
| `.github/hermes/gate.py` | gate de fraîcheur (socle) + `check_runs_green()` + appel du résolveur |
| `.github/workflows/hermes-resolver.yml` | job de résolution (`workflow_dispatch`) |
| `.github/workflows/hermes-watchdog.yml` | réveil événementiel du gate (pas de cron) |
| `.github/workflows/hermes-selftests.yml` | joue les cinq `--selftest` sur les PR qui touchent `.github/hermes/**` |

Aucune logique métier dans le YAML : les workflows appellent un script Python
(stdlib seule, aucun `pip`). C'est la leçon des heredocs Python multi-lignes
dans un bloc `run: |` (indentation cassée sur un runner le 2026-10-24).

## 3. Jambe LLM : InferHub

- Endpoint : `https://api.inferhub.dev/v1/chat/completions`
- Modèle : `combo/cheap`
- Clé : secret `INFERHUB_API_KEY` (jamais affichée, jamais journalisée)

**Sans clé : 100 % déterministe + escalade humaine.** Aucun chemin ne « réussit »
en silence : soit la résolution aboutit, soit la PR reçoit un rapport d'échec et
un humain est mentionné. Le résolveur n'utilise **pas** Agnes (écart assumé au
`duello` d'origine, cf. runbook §6.3).

## 4. Fichiers dérivés / binaires (jamais soumis à l'IA)

Un fichier dérivé est produit par un outil du dépôt : le réécrire à la main est
une faute, il faut le **régénérer**. En cas de conflit, la version de la base
est conservée et le fichier est signalé « à régénérer » dans le rapport.

| Motif | Justification |
|---|---|
| `uv.lock` | verrou de résolution uv : régénéré par `uv lock`/`uv sync`, jamais édité à la main |
| `*.lock` | tout autre verrou (outils futurs) : même raison |
| `.venv/` | environnement virtuel local, jamais versionné |
| `cdk.out/` | sortie de synthèse CDK (`cdk synth`), régénérée |
| `__pycache__/`, `*.pyc` | bytecode Python, régénéré par l'interpréteur |

Sont également écartés : les **binaires** (images, polices, archives — une IA ne
peut pas en produire un contenu valide) et tout fichier dont un côté dépasse
**12 000 caractères** (mode « fichier entier » impossible sans troncature ; une
troncature produit du code invalide à coup sûr).

## 5. Repli déterministe

| Cas | Résolution |
|---|---|
| fichier dérivé ou binaire | côté base (`--ours` pendant un rebase) + signalé « à régénérer » |
| `*.md`, `*.txt`, `*.csv` | union triée des lignes des deux côtés (aucune perte) |
| `.github/`, `pyproject.toml`, `src/`, `tests/`, `scripts/`, `infra/` | **jamais** de prise de côté au hasard → escalade humaine |

Aucune prise de côté forcée (`-X ours` / `-X theirs` interdits) : une régression
silencieuse coûte plus cher qu'une escalade.

## 6. Selftests (sans réseau, sans secret)

```bash
python3 .github/hermes/resolve.py   --selftest
python3 .github/hermes/resolver.py  --selftest
python3 .github/hermes/rebase.py    --selftest
python3 .github/hermes/dispatch.py  --selftest
python3 .github/hermes/watchdog.py  --selftest
```

Couverture :

- `resolve.py` : contrôle **« jambe directe = InferHub »** (URL, modèle, nom du
  secret), **« clé InferHub ⇒ disponible »** (et clé vide/absente ⇒
  indisponible), liste des dérivés, découpage des hunks (styles merge et diff3),
  refus des sorties d'IA (vides, tronquées, à marqueurs, JSON ou Python
  invalides), repli heuristique, et jambe InferHub pilotée par un transport
  factice (forme du corps vérifiée, aucun réseau).
- `rebase.py` : **vrai rebase git** dans un dépôt jetable — une source en
  conflit escalade, un dérivé est résolu côté base, une doc est fusionnée en
  union.
- `resolver.py` : validation (vert/rouge/timeout/vide), marqueurs du rapport,
  toolchain (refuse `npm`/`tsc`/`gradle`), puis délègue aux deux selftests
  ci-dessus.
- `dispatch.py` : backoff `min(5·2ⁿ, 30)`, lecture de l'horodatage du dernier
  rapport, anti-doublon, dispatch intercepté (entrées vérifiées), dry-run.
- `watchdog.py` : rate-limit, horodatage corrompu, anti-doublon, échec de
  dispatch signalé.

Ces cinq contrôles tournent aussi en CI : `.github/workflows/hermes-selftests.yml`
les joue sur toute PR qui touche `.github/hermes/**`.

## 7. Secrets et variables

Secrets (noms seuls — aucune valeur n'est jamais affichée) :

| Nom | Rôle | Obligatoire | État |
|---|---|---|---|
| `INFERHUB_API_KEY` | jambe LLM directe InferHub (`combo/cheap`) | non — absent ⇒ 100 % déterministe | **posé le 2026-11-11** |
| `HERMES_PUSH_TOKEN` | PAT `contents: write` pour le push git (déclenche les vrais événements) | non — absent ⇒ ré-armement par dispatch | **absent** : le ré-armement fonctionne, mais les runs déclenchés par le push du `GITHUB_TOKEN` arrivent en `action_required` et doivent être approuvés (voir §11) |

Le `GITHUB_TOKEN` du workflow suffit pour l'API (labels, commentaires,
check-runs, dispatches) ; il ne suffit **pas** pour que le push déclenche la CI
(anti-récursion GitHub), d'où le ré-armement explicite de `ci.yml` et du gate.

Variables de dépôt (non secrètes) : aucune n'est nécessaire. `HERMES_LLM_MODEL`
n'est **pas** utilisée : le modèle InferHub est fixé dans `resolve.py`.

## 8. Limites connues

1. **Budget CI et exemption du résolveur** (règle user 2026-10-24, garde
   `ci-budget`) : le verdict d'une PR reste ≤ 5 min par job. Le job de
   résolution, lui, porte `timeout-minutes: 10` avec le marqueur explicite
   `# CI-BUDGET-EXEMPT` — il n'est **jamais** un check de PR (lancement en
   `workflow_dispatch` seulement) et il rejoue toute la toolchain du dépôt sur
   l'arbre rebasé avant de pousser. Mesures du 2026-11-11 : `scripts/ci-local.sh`
   = **222 s** en local (cache uv chaud) et **> 270 s sur le runner** — le run
   `43068197660` a échoué sur « Validation : timeout après 270s » alors que le
   rebase et la résolution avaient réussi. D'où `timeout-minutes: 12` et
   `VALIDATION_TIMEOUT: 480`. Un timeout de validation **bloque le push** (le
   résolveur préfère escalader plutôt que pousser un arbre non validé) : le
   budget ne s'allonge donc jamais au détriment de la sûreté.
   Le garde-fou `check_timeouts.py` accepte une exemption **seulement** si la
   ligne porte `CI-BUDGET-EXEMPT` avec sa raison ; sinon tout dépassement reste
   une erreur, et l'exemption est signalée en avertissement à chaque run.
2. **Pas de résolution par hunk** : les fichiers dont un côté dépasse 12 000
   caractères ne sont pas découpés (`duello` le faisait) ; ils partent en
   escalade. Suffisant pour Jauge (aucun fichier de cette taille hors `uv.lock`,
   qui est dérivé).
3. **Pas de miroir d'objets par runner** ([HERMES-CACHE] de `duello`) : chaque
   run clone le dépôt. Le dépôt Jauge est petit (quelques Mo) ; à revoir si le
   clone dépasse ~30 s.
4. **Pas de coordination multi-agents** ([HERMES-COORD] de `duello`) : inutile
   avec un seul gate sérialisé.
5. **Le résolveur ne merge jamais** : il pousse, ré-arme la CI, puis Mergify
   (le pilote) ré-évalue et fusionne. Le merge n'est jamais la décision du
   résolveur ni du gate.
6. **`workflow_run` du chien de garde** cible le workflow nommé `CI` : renommer
   `ci.yml` casserait le réveil (le `workflow_dispatch` manuel reste possible).
7. Un échec du résolveur n'est **pas** un état terminal : la PR reste ouverte et
   le gate re-dispatch avec backoff (`min(5·2ⁿ, 30)` min), escalade informative
   au-delà de 3 tentatives.

## 9. Ordre de fusion — réalisé le 2026-11-11

1. CI du dépôt (`ci.yml`) mergée d'abord, checks connus : `lint`, `limits`,
   `test`, `iac`, `ci-budget`.
2. Résolveur livré en PR **draft** (#10), passé « ready » puis mergé une fois la
   CI verte.
3. Secret `INFERHUB_API_KEY` posé ; `HERMES_PUSH_TOKEN` volontairement absent
   (le ré-armement par dispatch déclenche la CI et le gate).
4. **Mergify est le pilote de merge** (vérifié le 2026-11-11 : la PR #25 a été
   fusionnée par la file Mergify). Le gate repasse en dry-run
   (`HERMES_LITE_DRY_RUN=1`) : un seul pilote à la fois.
5. Le gate garde deux rôles en dry-run : poser les labels d'état, et
   **dispatcher le résolveur sur un vrai conflit** — Mergify signale le conflit
   mais ne le résout pas. Variable `HERMES_DISPATCH_RESOLVEUR=1` (défaut) :
   le dispatch survit au dry-run ; à `0`, le résolveur n'est lancé que par
   `workflow_dispatch` manuel.
6. Essai sur un conflit réel : voir §11.

## 10. Écarts au runbook

| Runbook | Ici | Pourquoi |
|---|---|---|
| `resolve.py --selftest` + `resolver.py --selftest` | + `rebase.py`, `dispatch.py`, `watchdog.py` | chaque module porte son propre selftest ; `resolver.py` les agrège |
| Fichiers < 500 lignes / fonctions < 50 lignes | respecté **sans exclusion** : `resolve.py` 313 l, `resolver.py` 356 l, `rebase.py` 272 l, `dispatch.py` 273 l, `gh.py` 246 l, `resolve_selftest.py` 234 l, `watchdog.py` 196 l, `gate.py` 378 l | `.github/` est exclu du garde `check_limits.py` : la contrainte est tenue à la main |
| Var `HERMES_LLM_MODEL=combo/cheap` | non posée | la jambe est InferHub, modèle fixé dans le code |
| `hermes-worker.yml` (ré-armement générique) | non porté | un seul point d'entrée suffit ; le ré-armement est fait par le résolveur |
| Résolution par hunk | non portée | voir limites §8.2 |
| Watchdog en cron `*/5` | déclenchement événementiel (`workflow_run`) + manuel | le planificateur GitHub est peu fiable (leçon [WD-EVENT]) et un cron réveille le gate sans événement à traiter |

## 11. Essai sur un conflit réel — 2026-11-11, réussi

Scénario volontairement provocé pour valider la chaîne complète : deux branches
modifient **la même docstring** de `src/jauge/common/ids.py` (fonction
`deterministic`), chacune y ajoutant un paragraphe différent.

| Étape | Référence | Résultat |
|---|---|---|
| PR A fusionnée | #27 | paragraphe « reproductibilité inter-machine » sur `main` |
| PR B en conflit réel | #28 | GitHub : `mergeable=CONFLICTING`, `mergeStateStatus=DIRTY` |
| Détection par le gate | run `43068032014` | `[dispatch] #28 : résolveur lancé sur test/conflit-b` |
| Résolution + validation + push | run `43076859827` (18:20:52 → 18:28:52) | `[resolver] conflit : src/jauge/common/ids.py` puis `rebase + résolution + push OK` |
| Preuve du push | commit `650b6365` | commiteur **`hermes-resolver <hermes-resolver@jauge.local>`** |
| CI ré-armée | run `43079751879` | `lint`, `limits`, `test`, `test-2`, `test-3`, `iac` → tous verts |
| Fusion | #28, 19:04:42 UTC | fusionnée par la file **Mergify** |

**Résultat sur `main`** : la docstring contient les **deux** paragraphes — celui
de la PR #27 *et* celui de la PR #28. Ce n'est donc ni un « garde le mien » ni un
« garde le leur » : c'est une fusion sémantique. Le fichier étant sous `src/`
(chemin critique), le repli déterministe était **interdit** (`resoudre_heuristique`
rend `None`) : c'est bien la jambe **InferHub** (`combo/cheap`) qui a produit le
contenu, validé ensuite par le garde-fou de sortie puis par toute la toolchain.

### Les trois bugs trouvés par cet essai (tous corrigés)

Un essai de bout en bout vaut mieux qu'un selftest : les trois défauts suivants
étaient invisibles hors conditions réelles, et chacun bloquait **toute**
résolution automatique.

| # | Bug | Effet | Correctif |
|---|---|---|---|
| 1 | `GET /repos/{o}/{r}/pulls` (la **liste**) ne renvoie ni `mergeable` ni `mergeable_state` ; le gate ne lisait que la liste | un conflit réel passait pour un simple retard → le résolveur n'était **jamais** dispatché | PR #29 : `etat_fusion()` rappelle le détail de la PR (3 tentatives, 4 s) |
| 2 | `hermes-lite.yml` déclarait `actions: read` | `POST …/workflows/{id}/dispatches` → **HTTP 403**, dispatch impossible | PR #32 : `actions: write` |
| 3 | `hermes-resolver.yml` sans `node` dans le `PATH` ni `TMPDIR` hors de `/tmp`, et `VALIDATION_TIMEOUT` à 200 puis 270 s | validation rouge : `BrokenPipeError` dans `jsii` (aws-cdk-lib lance un processus Node) et timeout avant la fin de `ci-local.sh` | PR #35 (480 s) et #36 (`node` + `TMPDIR` + rapport tronqué par ligne) |

Le troisième bug illustre le garde-fou : validation rouge ⇒ **aucun push**,
escalade `hermes/intervention-humaine` et rapport sur la PR. Le résolveur n'a
jamais poussé un arbre non validé.

### Ce qui reste manuel aujourd'hui

Le push du résolveur est fait avec le `GITHUB_TOKEN` (pas de
`HERMES_PUSH_TOKEN`). Conséquence mesurée : les runs déclenchés par ce push
arrivent en état **`action_required`** et doivent être approuvés
(`POST /repos/{o}/{r}/actions/runs/{id}/approve` — 4 runs approuvés à la main
sur cet essai) avant que Mergify voie `ci-budget` et `stale-base-guard` verts.

Avec un PAT `contents: write` posé en secret `HERMES_PUSH_TOKEN`, le push
déclenche des runs normaux, sans approbation : la chaîne devient entièrement
autonome. C'est le seul geste restant pour l'autonomie complète.
