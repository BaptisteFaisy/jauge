"""Routes de l'API du plan de contrôle Jauge (§15)."""

from __future__ import annotations

from jauge.api.routes import (
    applications,
    backtest,
    benchmark,
    campaigns,
    chat,
    costs,
    demo,
    health,
    killswitch,
    levers,
    ombre,
    pricing,
    protocoles,
    recommendations,
    reports,
    rollouts,
    savings,
    settings,
    tenants,
    verite,
)

#: Ordre d'enregistrement dans l'application (l'ordre n'a pas d'effet sur le routage).
ROUTEURS = (
    health.router,
    backtest.router,
    benchmark.router,
    killswitch.router,
    tenants.router,
    applications.router,
    costs.router,
    levers.router,
    pricing.router,
    protocoles.router,
    verite.router,
    ombre.router,
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
