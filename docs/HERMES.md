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
3. résout chaque conflit : jambe **Fireworks** d'abord, repli **déterministe**
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
| `.github/hermes/resolve.py` | décision pure : fichiers dérivés/binaires, hunks, garde-fou de sortie, jambe Fireworks |
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

## 3. Jambe LLM : Fireworks

- Endpoint : `https://api.fireworks.ai/inference/v1/chat/completions`
- Modèle : `accounts/fireworks/models/deepseek-v4p1-flash-1017`
- Clé : secret `FIREWORKS_API_KEY` (jamais affichée, jamais journalisée)

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

- `resolve.py` : contrôle **« jambe directe = Fireworks »** (URL, modèle, nom du
  secret), **« clé Fireworks ⇒ disponible »** (et clé vide/absente ⇒
  indisponible), liste des dérivés, découpage des hunks (styles merge et diff3),
  refus des sorties d'IA (vides, tronquées, à marqueurs, JSON ou Python
  invalides), repli heuristique, et jambe Fireworks pilotée par un transport
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

| Nom | Rôle | Obligatoire |
|---|---|---|
| `FIREWORKS_API_KEY` | jambe LLM directe Fireworks | non — absent ⇒ 100 % déterministe |
| `HERMES_PUSH_TOKEN` | PAT `contents: write` pour le push git (déclenche les vrais événements) | non — absent ⇒ ré-armement par dispatch |

Le `GITHUB_TOKEN` du workflow suffit pour l'API (labels, commentaires,
check-runs, dispatches) ; il ne suffit **pas** pour que le push déclenche la CI
(anti-récursion GitHub), d'où le ré-armement explicite de `ci.yml` et du gate.

Variables de dépôt (non secrètes) : aucune n'est nécessaire. `HERMES_LLM_MODEL`
n'est **pas** utilisée : le modèle Fireworks est fixé dans `resolve.py`.

## 8. Limites connues

1. **Budget de 5 minutes par job** (règle user 2026-10-24, garde `ci-budget`) :
   le job de résolution est plafonné à 5 min, dont 200 s de validation. Mesure
   sur le runner (cache uv chaud) : `scripts/ci-local.sh` = **4 s** (lint 1 s,
   limites < 1 s, 22 tests 1 s, pas de gabarit infra). Un `ci-local.sh` plus
   lent que 200 s ferait échouer le run **sans push** (escalade) : le budget ne
   s'allonge pas.
2. **Pas de résolution par hunk** : les fichiers dont un côté dépasse 12 000
   caractères ne sont pas découpés (`duello` le faisait) ; ils partent en
   escalade. Suffisant pour Jauge (aucun fichier de cette taille hors `uv.lock`,
   qui est dérivé).
3. **Pas de miroir d'objets par runner** ([HERMES-CACHE] de `duello`) : chaque
   run clone le dépôt. Le dépôt Jauge est petit (quelques Mo) ; à revoir si le
   clone dépasse ~30 s.
4. **Pas de coordination multi-agents** ([HERMES-COORD] de `duello`) : inutile
   avec un seul gate sérialisé.
5. **Le résolveur ne merge jamais** : il pousse, puis le gate ré-évalue. Le
   merge reste la décision du gate/Mergify.
6. **`workflow_run` du chien de garde** cible le workflow nommé `CI` : renommer
   `ci.yml` casserait le réveil (le `workflow_dispatch` manuel reste possible).
7. Un échec du résolveur n'est **pas** un état terminal : la PR reste ouverte et
   le gate re-dispatch avec backoff (`min(5·2ⁿ, 30)` min), escalade informative
   au-delà de 3 tentatives.

## 9. Ordre de fusion prévu

1. Cette PR (`feat/hermes-resolver`) est ouverte en **draft**, volontairement
   **non fusionnée** : le runbook demande de merger la CI d'abord.
2. La CI du dépôt (`ci.yml`) doit être verte et ses checks connus.
3. Poser les secrets `FIREWORKS_API_KEY` et `HERMES_PUSH_TOKEN` (action user).
4. Passer la PR en « ready » puis la merger.
5. Premier essai sur un vrai conflit, puis sortie du dry-run du gate
   (`HERMES_LITE_DRY_RUN=0`).

## 10. Écarts au runbook

| Runbook | Ici | Pourquoi |
|---|---|---|
| `resolve.py --selftest` + `resolver.py --selftest` | + `rebase.py`, `dispatch.py`, `watchdog.py` | chaque module porte son propre selftest ; `resolver.py` les agrège |
| Fichiers < 500 lignes / fonctions < 50 lignes | respecté **sans exclusion** : `resolve.py` 313 l, `resolver.py` 356 l, `rebase.py` 272 l, `dispatch.py` 273 l, `gh.py` 246 l, `resolve_selftest.py` 234 l, `watchdog.py` 196 l, `gate.py` 378 l | `.github/` est exclu du garde `check_limits.py` : la contrainte est tenue à la main |
| Var `HERMES_LLM_MODEL=combo/cheap` | non posée | la jambe est Fireworks, modèle fixé dans le code |
| `hermes-worker.yml` (ré-armement générique) | non porté | un seul point d'entrée suffit ; le ré-armement est fait par le résolveur |
| Résolution par hunk | non portée | voir limites §8.2 |
| Watchdog en cron `*/5` | déclenchement événementiel (`workflow_run`) + manuel | le planificateur GitHub est peu fiable (leçon [WD-EVENT]) et un cron réveille le gate sans événement à traiter |
