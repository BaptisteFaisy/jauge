"""API du plan de contrôle Jauge (§15, EF-POR-01, ENF-SEC-02).

Le paquet expose la factory `creer_app` (application FastAPI), les briques
d'authentification et de rôles (`auth`), le cloisonnement par client
(`tenancy`), le journal d'audit (`audit`), les paramètres d'administration
(`reglages`) et les ports vers les modules métier pas encore livrés (`ports`).
"""

from __future__ import annotations

from jauge.api.app import creer_app
from jauge.api.killswitch import (
    ActionKillSwitch,
    EtatKillSwitch,
    EtatKillSwitchClient,
    KillSwitchStore,
    MemoryKillSwitchStore,
    TraitementSuspenduError,
)

__all__ = [
    "ActionKillSwitch",
    "EtatKillSwitch",
    "EtatKillSwitchClient",
    "KillSwitchStore",
    "MemoryKillSwitchStore",
    "TraitementSuspenduError",
    "creer_app",
]
