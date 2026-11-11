# Brief sous-agent — comment livrer un module Jauge

À lire **en premier**, avant d'écrire la moindre ligne. Ce brief est identique
pour tous les modules ; la mission précise (chemins, exigences, chapitres du
cahier des charges) figure dans l'ordre de mission.

## 0. Le projet en trois phrases

**Jauge** est un service FinOps vendu par un MSP : il **attribue** chaque dollar
d'inférence Amazon Bedrock à une application et une équipe, **prouve** qu'un
modèle moins cher convient en rejouant des requêtes réelles et en les faisant
noter par un modèle juge, puis **applique** la bascule par paliers via une règle
de passerelle AgentCore et vérifie l'économie sur la facture.

Dépôt : `BaptisteFaisy/jauge` (privé), branche de base `main`, clone de
référence `/home/baptiste/Bureau/jauge`.

Documents de référence, **dans le dépôt** :

- `docs/fr/cahier-des-charges-finops-ia.md` — la spec normative (chapitres et
  références `EF-*`, `ENF-*`, `R*`, annexes A à E).
- `docs/MODULES.md` — la carte du chantier : règles non négociables, socle déjà
  livré, tableau des 16 modules avec leurs chemins et leur définition de « fini ».

## 1. Méthode imposée

```bash
# 1. worktree dédié (jamais le clone de référence, jamais le worktree d'un autre)
git -C /home/baptiste/Bureau/jauge fetch origin
git -C /home/baptiste/Bureau/jauge worktree add /home/baptiste/tmp/jauge-<module> \
    -b feat/<module> origin/main
cd /home/baptiste/tmp/jauge-<module>

# 2. environnement
export PATH="$HOME/.local/bin:$PATH"     # uv y est installé
uv sync --all-groups

# 3. développer, tester en continu
uv run pytest -q tests/<module>
bash scripts/ci-local.sh                 # lint + limits + test + iac : DOIT être vert

# 4. livrer
git add -A
git commit -m "<module> : <ce que ça apporte> (<références EF-*>)"
git push -u origin feat/<module>
gh pr create --base main --title "<module> : ..." --body "<résumé + écarts + besoins>"

# 5. vérifier la CI et corriger
gh pr checks <numéro> --watch --interval 20    # ou : gh run list --branch feat/<module>
```

**Bornes** : si la CI échoue trois fois de suite sur la même erreur déterministe,
arrête, laisse la PR telle quelle et réponds `KO` avec l'erreur exacte recopiée.
Ne jamais allonger un `timeout-minutes`. Ne jamais pousser sur `main`, ne jamais
merger, ne jamais rebaser ni supprimer la branche d'un autre.

## 2. Règles dures (rappel — tout écart est un KO)

1. **Tailles** : fichier < 500 lignes, fonction < 50 lignes.
   `python3 scripts/check_limits.py` doit rester vert (job CI `limits`).
2. **Zéro duplication** : importe le socle (`jauge.models`, `jauge.engine`,
   `jauge.common`, `jauge.store`) au lieu de réécrire un calcul ou une entité.
3. **Fichiers verrouillés** (ne pas modifier, même « pour rendre service ») :
   `pyproject.toml`, `uv.lock`, `src/jauge/common/**`, `src/jauge/models/**`,
   `src/jauge/engine/**`, `src/jauge/store/protocol.py`, `tests/conftest.py`,
   `.github/**`, `.mergify.yml`, `scripts/**`, `docs/MODULES.md`, `README.md`.
   Un besoin d'évolution (champ manquant, dépendance absente) → **ne rien
   toucher**, le signaler dans le corps de la PR et dans la réponse finale.
4. **Aucun appel réseau dans les tests** : ni AWS, ni HTTP, ni LLM. Utilise des
   adaptateurs fictifs (classes `Fake*`) et `MemoryStore`. Les adaptateurs réels
   (boto3) sont écrits mais testés seulement via leur interface.
5. **Aucun chiffre calculé par un LLM** (ENF-IA-01) : les nombres viennent du
   code ; un agent ne reçoit que des valeurs déjà calculées et ne rédige que du
   texte.
6. **Confidentialité** (ENF-CONF-01) : le texte des requêtes et des réponses ne
   sort jamais du plan de données. Aucun outil d'agent, aucune réponse d'API du
   plan de contrôle ne renvoie `texte_requete` ni `reponse_candidate`.
7. **Cloisonnement** (ENF-SEC-02) : toute lecture prend `tenant_id` et filtre
   dessus ; chaque module qui expose des données livre ses **tests négatifs**
   (client B sur les données du client A → refus).
8. **Langue** : commentaires, docstrings, messages d'erreur et commits en
   **français** ; tout ce qui est destiné aux juges du hackathon (`README.md`,
   `docs/HLD.md`, `docs/ARCHITECTURE.md`, portail, `docs/deploy-client.md`) en
   **anglais**.
9. **Pas de délégation** : tu fais le travail toi-même, sans sous-agent.
10. **Secrets** : ne jamais afficher ni copier une valeur de secret (noms seuls).

## 3. Ce que le socle fournit déjà

| Chemin | Contenu à réutiliser |
|---|---|
| `src/jauge/models/tenant.py` | `Tenant`, `Application`, `FormuleOffre`, `SchemaAttribution` |
| `src/jauge/models/pricing.py` | `ServiceTier`, `TarifModele`, `MetriquesRequete`, `CoutRequete` |
| `src/jauge/models/cost.py` | `AgregatQuotidien`, `RapprochementJour`, `SyntheseCout` |
| `src/jauge/models/lever.py` | `Levier`, `TypeLevier`, `StatutLevier` |
| `src/jauge/models/campaign.py` | `Campagne`, `ResultatCandidat`, `EchantillonRequete`, `ParametresRejeu`, `PaireRejoueeDataPlane`, `StatutCampagne` |
| `src/jauge/models/recommendation.py` | `Recommandation` (+ blocs qualité/coût/latence de l'Annexe B), `BasculeProgressive`, `VerificationEconomie`, `StatutRecommandation` |
| `src/jauge/common/errors.py` | `JaugeError` et ses sous-classes (`CloisonnementError`, `PlafondDepasseError`, `TarifInconnuError`, `ApprobationRequiseError`, `EtatIncompatibleError`, `ServiceIndisponibleError`, `ValidationError`, `IntrouvableError`) |
| `src/jauge/common/ids.py` | `aleatoire`, `deterministic`, `graine_de`, `jour_compact`, `horodatage_utc` |
| `src/jauge/engine/calculator.py` | `TableTarifs` (table datée + repli Standard), `calculer_cout_requete` (Annexe D) |
| `src/jauge/engine/attribution.py` | `agreger_requetes`, `rapprocher_facture` |
| `src/jauge/engine/stats.py` | `intervalle_wilson`, `percentile`, `taille_echantillon_minimale`, `economie_mensuelle`, `baisse_pourcentage` |
| `src/jauge/store/` | `Store` (protocole) + `MemoryStore` (cloisonné par client) |
| `tests/conftest.py` | fixtures `tenant`, `tenant_b`, `app_classement`, `app_assistant`, `tarifs` ; constantes `GRAND_MODELE`, `MOYEN_MODELE`, `PETIT_MODELE`, `REGION` |

Si un module a besoin d'une entité qui n'existe pas dans `models/`, il la
définit **dans son propre paquet** (dataclass ou pydantic local) et propose son
intégration au socle dans le corps de la PR.

## 4. Définition de « fini »

- [ ] Code dans les chemins attribués, tailles conformes, ruff vert.
- [ ] Tests dans `tests/<module>/`, sans réseau, couvrant le cas nominal, les
      cas limites et (si exposition de données) le refus inter-clients.
- [ ] Les exigences `EF-*`/`ENF-*` visées sont citées en docstring de module.
- [ ] `bash scripts/ci-local.sh` vert en local **et** CI GitHub verte sur la PR.
- [ ] PR ouverte sur `main`, corps de PR en français : ce qui est fait, ce qui
      manque, les hypothèses prises, les besoins vis-à-vis du socle.
- [ ] Réponse finale : numéro de PR, résumé, écarts, besoins.
