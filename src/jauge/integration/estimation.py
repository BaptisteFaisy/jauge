"""Adaptateur de l'estimation de coût d'une campagne (port `PortEstimationCout`).

L'estimation vient du module de rejeu (M4) : `jauge.replay.cost` applique la
formule du §17.1 (2 exécutions de la référence + 1 par candidat, au tarif Flex,
plus les jugements) et la compare au plafond du client (ENF-COUT-01).

Le module d'estimation **ne lève pas** : il rend le devis avec son plafond, et
ce sont les appelants (l'outil `start_replay` des agents, la route de campagne
de l'API) qui refusent de dépasser le plafond. Une campagne hors plafond ne
dépense donc rien avant d'être refusée.

Exigences visées : ENF-COUT-01, §17.1, EF-ADM-01, ENF-IA-01.
"""

from __future__ import annotations

from datetime import date

from jauge.agents.ports import EstimationCampagne, PortEstimationCout
from jauge.common.errors import ValidationError
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.evaluation.judge import RegistreCapacite, choisir_juge
from jauge.integration.profil import AdaptateurProfilTache
from jauge.integration.socle import charger_application, charger_tenant, famille_de, rang_capacite
from jauge.models.pricing import MetriquesRequete
from jauge.replay.cost import NIVEAU_REJEU, HypothesesCout, estimer_cout_campagne
from jauge.store.protocol import Store

#: Jetons moyens d'un jugement (hypothèse de facturation, cf. M4).
JETONS_ENTREE_JUGE = 1200
JETONS_SORTIE_JUGE = 400


class AdaptateurEstimation(PortEstimationCout):
    """Port d'estimation de coût, branché sur le module de rejeu (M4)."""

    def __init__(
        self,
        store: Store,
        table_tarifs: TableTarifs,
        profils: AdaptateurProfilTache | None = None,
    ) -> None:
        self._store = store
        self._tarifs = table_tarifs
        self._profils = profils or AdaptateurProfilTache(store, table_tarifs)

    def estimer(
        self,
        tenant_id: str,
        application_id: str,
        modeles: list[str],
        taille_echantillon: int,
    ) -> EstimationCampagne:
        """Devis d'une campagne : coût total, plafond et détail par modèle."""
        tenant = charger_tenant(self._store, tenant_id)
        application = charger_application(self._store, tenant_id, application_id)
        if not modeles:
            raise ValidationError("Aucun modèle candidat fourni", application_id=application_id)
        if taille_echantillon <= 0:
            raise ValidationError("Taille d'échantillon invalide", taille=taille_echantillon)
        profil = self._profils.profil_tache(tenant_id, application_id)
        hypotheses = HypothesesCout(
            jetons_entree_moyens=profil.jetons_entree_moyens,
            jetons_sortie_moyens=profil.jetons_sortie_moyens,
            jetons_entree_juge=JETONS_ENTREE_JUGE,
            jetons_sortie_juge=JETONS_SORTIE_JUGE,
        )
        juge = self._choisir_juge(tenant.modeles_autorises, application.modele_actuel, modeles)
        devis = estimer_cout_campagne(
            tarifs=self._tarifs,
            region=tenant.region_principale,
            a_la_date=date.today(),
            modele_actuel=application.modele_actuel,
            candidats=modeles,
            taille_echantillon=taille_echantillon,
            hypotheses=hypotheses,
            modele_juge_id=juge,
            plafond_usd=tenant.plafond_campagne_usd,
        )
        return EstimationCampagne(
            montant_usd=devis.cout_total_usd,
            devise="USD",
            taille_echantillon=taille_echantillon,
            plafond_usd=tenant.plafond_campagne_usd,
            par_modele_usd=self._par_modele(
                tenant.region_principale,
                application.modele_actuel,
                modeles,
                hypotheses,
                taille_echantillon,
            ),
        )

    def _choisir_juge(self, autorises: list[str], modele_actuel: str, candidats: list[str]) -> str:
        """Modèle juge conforme au §13.5 (distinct des candidats, au moins capable)."""
        registre = RegistreCapacite(
            {modele: rang_capacite(famille_de(modele)) for modele in {*autorises, modele_actuel}}
        )
        return choisir_juge(modele_actuel, candidats, registre)

    def _par_modele(
        self,
        region: str,
        modele_actuel: str,
        candidats: list[str],
        hypotheses: HypothesesCout,
        taille: int,
    ) -> dict[str, float]:
        """Coût de rejeu par modèle, au tarif Flex (même formule que le socle)."""
        metriques = MetriquesRequete(
            jetons_entree=hypotheses.jetons_entree_moyens,
            jetons_sortie=hypotheses.jetons_sortie_moyens,
        )
        detail: dict[str, float] = {}
        for modele_id in [modele_actuel, *candidats]:
            tarif = self._tarifs.trouver(modele_id, region, NIVEAU_REJEU, date.today())
            cout = calculer_cout_requete(tarif, metriques).cout_total
            detail[modele_id] = round(cout * taille, 6)
        return detail
