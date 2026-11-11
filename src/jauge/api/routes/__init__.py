"""Routes de l'API du plan de contrôle Jauge (§15)."""

from __future__ import annotations

from jauge.api.routes import (
    applications,
    campaigns,
    chat,
    costs,
    demo,
    levers,
    recommendations,
    reports,
    rollouts,
    savings,
    settings,
    tenants,
)

#: Ordre d'enregistrement dans l'application (l'ordre n'a pas d'effet sur le routage).
ROUTEURS = (
    tenants.router,
    applications.router,
    costs.router,
    levers.router,
    campaigns.router,
    recommendations.router,
    rollouts.router,
    savings.router,
    reports.router,
    chat.router,
    settings.router,
    demo.router,
)

__all__ = ["ROUTEURS"]
