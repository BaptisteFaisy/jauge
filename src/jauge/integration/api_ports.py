"""Adaptateurs des ports de l'API du plan de contrôle (campagnes et rapports).

L'API du plan de contrôle parle à des **ports** (`jauge.api.ports`). Ce module
branche ces ports sur les modules réels :

- `AdaptateurCampagnes` : lancement d'une campagne de rejeu (M4), avec
  estimation préalable et contrôle du plafond (ENF-COUT-01) ;
- `AdaptateurRapports` : rapport mensuel (M9), construit par
  `jauge.reports.builder` et rendu par `jauge.reports.storage`.

Aucune formule n'est dupliquée : le rapport et la campagne viennent des
modules qui les ont déjà écrits (ENF-IA-01).

Exigences visées : EF-POR-01, EF-RAP-03, EF-REJ-01, ENF-COUT-01.
"""

from __future__ import annotations

from datetime import date

from jauge.api.ports import DemandeCampagne
from jauge.common.errors import ValidationError
from jauge.engine.calculator import TableTarifs
from jauge.integration.rejeu import AdaptateurRejeu
from jauge.integration.socle import charger_tenant
from jauge.models.campaign import Campagne
from jauge.models.tenant import Application, Tenant
from jauge.pricing_data.loader import lignes as lignes_de_table
from jauge.reports.builder import construire_rapport
from jauge.reports.schema import RapportMensuel
from jauge.reports.storage import DepotMemoire, DepotRapports, publier, vers_dict
from jauge.reports.synthesis import RedacteurDeterministe, appliquer_synthese
from jauge.store.protocol import Store

#: Taille d'échantillon par défaut d'une campagne (§13.1).
TAILLE_ECHEANTILLON_DEFAUT = 200


class AdaptateurCampagnes:
    """Port API des campagnes : estimation, plafond, puis rejeu (M4)."""

    def __init__(self, store: Store, rejeu: AdaptateurRejeu) -> None:
        self._store = store
        self._rejeu = rejeu

    def lancer(
        self, tenant: Tenant, application: Application, demande: DemandeCampagne
    ) -> Campagne:
        """Crée et exécute une campagne de rejeu pour le client demandé."""
        charger_tenant(self._store, tenant.id)
        candidats = demande.modeles_candidats or [
            modele for modele in tenant.modeles_autorises if modele != application.modele_actuel
        ]
        if not candidats:
            raise ValidationError(
                "Aucun modèle candidat fourni ni autorisé pour ce client",
                tenant_id=tenant.id,
            )
        interdits = [
            modele
            for modele in candidats
            if tenant.modeles_autorises and modele not in tenant.modeles_autorises
        ]
        if interdits:
            raise ValidationError("Modèle candidat non autorisé pour ce client", modeles=interdits)
        taille = demande.taille_echantillon or TAILLE_ECHEANTILLON_DEFAUT
        return self._rejeu.lancer_campagne(
            tenant.id,
            application.id,
            candidats,
            taille,
            demande.graine_aleatoire,
            modele_juge_id=demande.modele_juge_id,
        )


class AdaptateurRapports:
    """Port API des rapports : construction et rendu du rapport mensuel (M9)."""

    def __init__(
        self,
        store: Store,
        table_tarifs: TableTarifs | None = None,
        depot: DepotRapports | None = None,
    ) -> None:
        self._store = store
        self._tarifs = table_tarifs
        self._depot = depot or DepotMemoire()
        self._rapports: dict[str, dict[str, object]] = {}

    def generer(self, tenant_id: str, debut: date, fin: date) -> dict[str, object]:
        """Génère le rapport du mois de la période demandée."""
        charger_tenant(self._store, tenant_id)
        rapport = self._construire(tenant_id, fin)
        enrichi = appliquer_synthese(rapport, RedacteurDeterministe())
        contenu = vers_dict(enrichi)
        self._rapports[str(contenu.get("id", ""))] = contenu
        publier(enrichi, self._depot)
        return contenu

    def obtenir(self, rapport_id: str) -> dict[str, object] | None:
        """Rend un rapport déjà généré."""
        return self._rapports.get(rapport_id)

    def _construire(self, tenant_id: str, fin: date) -> RapportMensuel:
        """Construit le rapport mensuel (le module M9 fait tous les calculs)."""
        tarifs = lignes_de_table(self._tarifs) if self._tarifs else None
        return construire_rapport(self._store, tenant_id, fin.year, fin.month, tarifs=tarifs)
