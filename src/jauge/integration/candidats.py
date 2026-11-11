"""Adaptateur des modèles candidats (port `PortModelesCandidats`).

La liste des candidats est la **liste d'autorisation du client**
(`Tenant.modeles_autorises`, EF-ADM-01) : le module ne propose jamais un modèle
que le client n'a pas autorisé. La table des tarifs (M2) sert à savoir si un
modèle candidat est **tarifé** : sans tarif, le coût de la campagne ne peut
pas être estimé et le candidat est signalé comme tel.

La famille et la fenêtre de contexte sont déduites de l'identifiant du modèle :
le dépôt ne porte pas encore de catalogue de référence des modèles du marché
(besoin de socle signalé dans la PR d'intégration).

Exigences visées : EF-ADM-01, EF-REJ-01, ENF-SEC-02.
"""

from __future__ import annotations

from datetime import date

from jauge.agents.ports import ModeleCandidat, PortModelesCandidats
from jauge.common.errors import TarifInconnuError
from jauge.engine.calculator import TableTarifs
from jauge.integration.socle import (
    charger_application,
    charger_tenant,
    famille_de,
    fenetre_contexte_de,
)
from jauge.models.pricing import ServiceTier
from jauge.store.protocol import Store


class AdaptateurCandidats(PortModelesCandidats):
    """Port des modèles candidats, branché sur l'autorisation du client."""

    def __init__(self, store: Store, table_tarifs: TableTarifs | None = None) -> None:
        self._store = store
        self._tarifs = table_tarifs

    def modeles_candidats(self, tenant_id: str, application_id: str) -> list[ModeleCandidat]:
        """Modèles autorisés pour l'application, hors modèle déjà en place."""
        tenant = charger_tenant(self._store, tenant_id)
        application = charger_application(self._store, tenant_id, application_id)
        candidats: list[ModeleCandidat] = []
        for modele_id in tenant.modeles_autorises:
            if modele_id == application.modele_actuel:
                continue
            candidats.append(
                ModeleCandidat(
                    modele_id=modele_id,
                    famille=famille_de(modele_id),
                    fenetre_contexte=fenetre_contexte_de(modele_id),
                    niveau_service=ServiceTier.STANDARD.value,
                    tarif_connu=self._tarif_connu(modele_id, tenant.region_principale),
                    autorise=True,
                )
            )
        return candidats

    def _tarif_connu(self, modele_id: str, region: str) -> bool:
        """Vrai si la table des tarifs couvre ce modèle (EF-ATT-04)."""
        if self._tarifs is None:
            return False
        try:
            self._tarifs.trouver(modele_id, region, ServiceTier.STANDARD, date.today())
        except TarifInconnuError:
            return False
        return True
