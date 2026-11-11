"""Modèle factice Strands : rejoue un scénario, sans réseau ni fournisseur.

Sert aux tests des agents (`tests/agents/`) et à la démonstration hors ligne :
on décrit à l'avance les réponses du modèle — un appel d'outil, puis un texte —
et l'agent Strands exécute réellement sa boucle : il appelle l'outil, reçoit le
résultat, puis répond.

Le scénario est une liste de « tours » ; chaque tour est une liste de blocs :

- `{"outil": ("nom_outil", {"argument": "valeur"})}` — le modèle demande un outil ;
- `{"texte": "…"}` — le modèle répond.

Aucun appel réseau n'est fait : c'est ce qui permet de tester le choix d'outil,
la validation de schéma et les garde-fous en CI.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable
from typing import Any

from jauge.agents.sdk import Model


class ModeleFactice(Model):
    """Modèle Strands scripté, déterministe et hors ligne."""

    def __init__(
        self,
        tours: list[list[dict[str, Any]]] | None = None,
        texte_final: str = "Terminé.",
        nom: str = "modele-factice",
    ) -> None:
        self._tours = list(tours or [])
        self._texte_final = texte_final
        self._nom = nom
        self.appels = 0
        self.outils_demandes: list[str] = []

    # --- Contrat `Model` ---------------------------------------------------
    def update_config(self, **model_config: Any) -> None:
        """Aucune configuration à mettre à jour dans un modèle factice."""

    def get_config(self) -> dict[str, Any]:
        """Configuration exposée (vide : rien à configurer)."""
        return {"model_id": self._nom}

    async def structured_output(
        self, output_model: type[Any], prompt: Any, system_prompt: str | None = None, **kwargs: Any
    ) -> AsyncIterable[dict[str, Any]]:
        """Sortie structurée : le scénario fournit le dictionnaire attendu."""
        yield {"output": output_model.model_validate(kwargs.get("donnees", {}))}

    async def stream(
        self,
        messages: Any,
        tool_specs: Any = None,
        system_prompt: Any = None,
        **kwargs: Any,
    ) -> AsyncIterable[dict[str, Any]]:
        """Rejoue le tour courant du scénario, dans le format d'événements Strands."""
        self.appels += 1
        tour = self._tours.pop(0) if self._tours else [{"texte": self._texte_final}]
        for index, bloc in enumerate(tour):
            if "outil" in bloc:
                nom, arguments = bloc["outil"]
                self.outils_demandes.append(nom)
                yield {"messageStart": {"role": "assistant"}}
                yield {
                    "contentBlockStart": {
                        "start": {
                            "toolUse": {"toolUseId": f"tu-{self.appels}-{index}", "name": nom}
                        }
                    }
                }
                yield {
                    "contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}
                }
                yield {"contentBlockStop": {}}
            else:
                yield {"messageStart": {"role": "assistant"}}
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": str(bloc.get("texte", ""))}}}
                yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                "metrics": {"latencyMs": 0},
            }
        }

    # --- Aides de test -----------------------------------------------------
    def reste_des_tours(self) -> int:
        """Nombre de tours encore scriptés (0 = le modèle improvisera)."""
        return len(self._tours)
