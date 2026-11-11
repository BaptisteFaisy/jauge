"""Les onze outils des agents Strands (§12.1, §12.3, ENF-CONF-01).

Chaque outil est une fonction typée exposée au modèle : nom, description,
arguments annotés. Trois invariants s'appliquent à **tous** les retours :

1. **agrégats et identifiants seulement** — jamais de texte de requête, de
   réponse candidate ni d'explication de juge (`sortie()` de `contexte.py`) ;
2. **aucun calcul par le modèle** — les nombres renvoyés sortent du socle ou
   des services, jamais d'un LLM (ENF-IA-01) ;
3. **cloisonnement** — `tenant_id` vient du contexte d'appel, jamais du modèle ;
   toute application ou campagne d'un autre client lève `CloisonnementError`
   (ENF-SEC-02).

Les garde-fous du §12.3 (plafond, candidats autorisés, validation humaine) sont
appliqués **dans les outils**, donc indépendamment de ce que le modèle décide.

Répartition par agent (§12.1) :

| Agent | Outils |
|---|---|
| Analyste des coûts | `query_cost_aggregates`, `list_levers`, `reconcile_with_bill` |
| Planificateur de rejeu | `get_task_profile`, `list_candidate_models`, `estimate_campaign_cost`, `start_replay` |
| Lecteur de résultats | `get_replay_results`, `get_failure_clusters` |
| Rédacteur | `get_recommendation_inputs`, `save_recommendation` |
"""

from __future__ import annotations

from typing import Any

from jauge.agents.contexte import ContexteOutils
from jauge.agents.tools_cout import outil_agregats, outil_leviers, outil_rapprochement
from jauge.agents.tools_redaction import outil_enregistrement, outil_entrees
from jauge.agents.tools_rejeu import (
    TAILLE_ECHANTILLON_DEFAUT,
    outil_candidats,
    outil_estimation,
    outil_profil,
    outil_rejeu,
)
from jauge.agents.tools_resultats import outil_clusters, outil_resultats

OUTILS_ANALYSTE = ("query_cost_aggregates", "list_levers", "reconcile_with_bill")
OUTILS_PLANIFICATEUR = (
    "get_task_profile",
    "list_candidate_models",
    "estimate_campaign_cost",
    "start_replay",
)
OUTILS_LECTEUR = ("get_replay_results", "get_failure_clusters")
OUTILS_REDACTEUR = ("get_recommendation_inputs", "save_recommendation")

TOUS_LES_OUTILS = OUTILS_ANALYSTE + OUTILS_PLANIFICATEUR + OUTILS_LECTEUR + OUTILS_REDACTEUR
"""Les onze outils du §12.1, dans l'ordre du cahier des charges."""


def creer_outils(ctx: ContexteOutils) -> dict[str, Any]:
    """Construit les onze outils, indexés par nom."""
    construits = [
        outil_agregats(ctx),
        outil_leviers(ctx),
        outil_rapprochement(ctx),
        outil_profil(ctx),
        outil_candidats(ctx),
        outil_estimation(ctx),
        outil_rejeu(ctx),
        outil_resultats(ctx),
        outil_clusters(ctx),
        outil_entrees(ctx),
        outil_enregistrement(ctx),
    ]
    return {outil.tool_name: outil for outil in construits}


def outils_pour(ctx: ContexteOutils, noms: tuple[str, ...]) -> list[Any]:
    """Sélectionne les outils d'un agent spécialisé (§12.1)."""
    tous = creer_outils(ctx)
    manquants = [nom for nom in noms if nom not in tous]
    if manquants:
        raise KeyError(f"Outils inconnus : {manquants}")
    return [tous[nom] for nom in noms]


__all__ = [
    "OUTILS_ANALYSTE",
    "OUTILS_LECTEUR",
    "OUTILS_PLANIFICATEUR",
    "OUTILS_REDACTEUR",
    "TAILLE_ECHANTILLON_DEFAUT",
    "TOUS_LES_OUTILS",
    "ContexteOutils",
    "creer_outils",
    "outils_pour",
]
