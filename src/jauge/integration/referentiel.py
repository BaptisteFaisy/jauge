"""Adaptateur du référentiel : clients et applications (port `PortReferentiel`).

Le port est branché sur `jauge.store.protocol.Store` : les fiches de client et
d'application viennent du plan de contrôle, sans duplication (zéro duplication).

Cloisonnement (ENF-SEC-02) : une application d'un autre client lève
`CloisonnementError`, une application inexistante lève `IntrouvableError`.
"""

from __future__ import annotations

from jauge.agents.ports import PortReferentiel
from jauge.integration.socle import charger_application, charger_tenant
from jauge.models.tenant import Application, Tenant
from jauge.store.protocol import Store


class AdaptateurReferentiel(PortReferentiel):
    """Port du référentiel branché sur le store du plan de contrôle."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def tenant(self, tenant_id: str) -> Tenant:
        """Rend la fiche du client demandé."""
        return charger_tenant(self._store, tenant_id)

    def application(self, tenant_id: str, application_id: str) -> Application:
        """Rend la fiche de l'application demandée, après contrôle de cloisonnement."""
        return charger_application(self._store, tenant_id, application_id)
