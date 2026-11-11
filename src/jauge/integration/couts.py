"""Adaptateurs des agrégats de coût et du rapprochement (ports `PortAgregats`,
`PortRapprochement`).

Les agrégats sont lus dans le store (ils ont été produits par le module
d'attribution M1 : `jauge.attribution.aggregate`) ; le rapprochement est celui
du même module (`jauge.attribution.reconcile`, écrit par `jauge.engine.
attribution.rapprocher_facture`).

La ventilation par dimension réutilise `agregats_par_dimension` (M1) : aucune
formule de coût n'est réécrite ici (ENF-IA-01).
"""

from __future__ import annotations

from datetime import date

from jauge.agents.ports import PortAgregats, PortRapprochement
from jauge.attribution.aggregate import agregats_par_dimension
from jauge.integration.socle import charger_application, charger_tenant
from jauge.models.cost import AgregatQuotidien, RapprochementJour, SyntheseCout
from jauge.store.protocol import Store

#: Application qui porte la dépense non attribuée (EF-ATT-07).
NON_ATTRIBUE = "non-attribue"


class AdaptateurAgregats(PortAgregats):
    """Port des agrégats de coût branché sur le store."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def synthese(
        self, tenant_id: str, application_id: str | None, debut: date, fin: date
    ) -> SyntheseCout:
        """Synthèse de la période, pour le client ou pour une application."""
        charger_tenant(self._store, tenant_id)
        if application_id is None:
            return self._store.synthese_cout(tenant_id, debut, fin)
        charger_application(self._store, tenant_id, application_id)
        agregats = self._store.lister_agregats(tenant_id, application_id, debut, fin)
        total = round(sum(agregat.cout_recalcule_usd for agregat in agregats), 4)
        return SyntheseCout(
            tenant_id=tenant_id,
            periode_debut=debut,
            periode_fin=fin,
            cout_total_usd=total,
            cout_par_application=agregats_par_dimension(agregats, "application"),
            cout_par_equipe=agregats_par_dimension(agregats, "equipe"),
            cout_par_modele=agregats_par_dimension(agregats, "modele"),
            couverture_attribution_pourcentage=_couverture(agregats, application_id, total),
            ecart_rapprochement_moyen_pourcentage=self._ecart_moyen(tenant_id, debut, fin),
        )

    def agregats(
        self, tenant_id: str, application_id: str | None, debut: date, fin: date
    ) -> list[AgregatQuotidien]:
        """Agrégats quotidiens de la période, filtrés sur le client."""
        charger_tenant(self._store, tenant_id)
        if application_id is not None:
            charger_application(self._store, tenant_id, application_id)
        return self._store.lister_agregats(tenant_id, application_id, debut, fin)

    def _ecart_moyen(self, tenant_id: str, debut: date, fin: date) -> float:
        """Écart moyen de rapprochement du client, calculé par le socle."""
        return self._store.synthese_cout(
            tenant_id, debut, fin
        ).ecart_rapprochement_moyen_pourcentage


def _couverture(agregats: list[AgregatQuotidien], application_id: str, total: float) -> float:
    """Part de la dépense attribuée à une application (EF-ATT-07)."""
    if total <= 0:
        return 0.0
    if application_id == NON_ATTRIBUE:
        return 0.0
    return 100.0


class AdaptateurRapprochement(PortRapprochement):
    """Port du rapprochement facture branché sur le store."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def rapprochements(self, tenant_id: str, debut: date, fin: date) -> list[RapprochementJour]:
        """Journées rapprochées de la période, pour le client demandé."""
        charger_tenant(self._store, tenant_id)
        return [
            jour
            for jour in self._store.lister_rapprochements(tenant_id)
            if debut <= jour.jour <= fin
        ]
