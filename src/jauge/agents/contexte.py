"""Contexte d'appel des outils d'agent et dernier filtre de confidentialité.

Le contexte porte le client, la campagne, le traceur et la session : il est
construit par l'application appelante, jamais par le modèle. Un agent ne peut
donc pas changer de client en changeant ses arguments (ENF-SEC-02).

`sortie()` applique le filtre final : aucun texte de requête ni de réponse
candidate ne sort d'un outil (ENF-CONF-01, §12.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from jauge.agents.guardrails import verifier_aucun_texte_de_requete
from jauge.agents.ports import Services
from jauge.agents.session import SessionCampagne
from jauge.agents.traces import Traceur


@dataclass
class ContexteOutils:
    """Contexte d'un appel d'outil : client, campagne, traces, session."""

    services: Services
    tenant_id: str
    campagne_id: str | None = None
    traceur: Traceur = field(default_factory=Traceur)
    session: SessionCampagne | None = None

    def periode(self, jours: int) -> tuple[date, date]:
        """Fenêtre glissante de `jours` jours finissant aujourd'hui."""
        fin = date.today()
        return fin - timedelta(days=jours), fin


def sortie(ctx: ContexteOutils, charge: dict[str, Any]) -> dict[str, Any]:
    """Filtre final avant le modèle : ni texte de requête ni réponse candidate."""
    verifier_aucun_texte_de_requete(charge)
    return charge
