"""Profil de tâche d'une application (port `PortProfilTache`) et contexte de leviers.

Le profil de tâche est **dérivé**, jamais recopié :

- les seuils, le modèle courant et la relecture humaine viennent de la fiche de
  l'application (`jauge.models.tenant.Application`) ;
- les volumes et les jetons moyens viennent des agrégats (M1), via les helpers
  de `jauge.levers.base` ;
- les contrôles déterministes viennent du module d'évaluation (M5, EF-EVA-02) :
  ils dépendent du type de tâche ;
- le type de tâche vient du trafic observé, sinon de la description de
  l'application.

Aucun texte de requête n'entre dans ce profil (ENF-CONF-01).

Exigences visées : EF-EVA-02, EF-LEV-01..06, §13.1, ENF-SEC-02.
"""

from __future__ import annotations

from jauge.agents.ports import PortProfilTache, ProfilTache
from jauge.common.temps import aujourdhui as date_du_jour
from jauge.engine.calculator import TableTarifs
from jauge.evaluation.checks import controles_pour
from jauge.integration.plan_de_donnees import PlanDeDonnees
from jauge.integration.socle import (
    charger_application,
    charger_tenant,
    type_tache_d_description,
    type_tache_de,
)
from jauge.levers.base import (
    ContexteLevier,
    ParametresLevier,
    ProfilRequetes,
    jetons_par_requete,
    volume_mensuel,
)
from jauge.models.tenant import Application
from jauge.replay.sampling import STRATES_LONGUEUR
from jauge.store.protocol import Store


def controles_de(type_tache: str) -> tuple[str, ...]:
    """Noms des contrôles déterministes du type de tâche (EF-EVA-02)."""
    return tuple(
        controle.__name__.removeprefix("controle_") for controle in controles_pour(type_tache)
    )


def construire_contexte_levier(
    store: Store,
    tenant_id: str,
    application_id: str,
    profil: ProfilRequetes | None,
    parametres: ParametresLevier,
    table_tarifs: TableTarifs,
) -> ContexteLevier:
    """Assemble le contexte d'analyse d'une application (module M3)."""
    tenant = charger_tenant(store, tenant_id)
    application = charger_application(store, tenant_id, application_id)
    return ContexteLevier(
        tenant_id=tenant.id,
        application_id=application.id,
        modele_actuel=application.modele_actuel,
        profil=profil or ProfilRequetes(),
        parametres=parametres,
        agregats=tuple(store.lister_agregats(tenant.id, application.id)),
        modeles_autorises=tuple(tenant.modeles_autorises),
        table_tarifs=table_tarifs,
    )


class AdaptateurProfilTache(PortProfilTache):
    """Port du profil de tâche, branché sur les agrégats et le plan de données."""

    def __init__(
        self,
        store: Store,
        table_tarifs: TableTarifs,
        plan: PlanDeDonnees | None = None,
    ) -> None:
        self._store = store
        self._tarifs = table_tarifs
        self._plan = plan

    def profil_tache(self, tenant_id: str, application_id: str) -> ProfilTache:
        """Profil dérivé d'une application : type, contrôles, seuils et volumes."""
        application = charger_application(self._store, tenant_id, application_id)
        profil_requetes = self._plan.profil(tenant_id, application_id) if self._plan else None
        contexte = construire_contexte_levier(
            self._store,
            tenant_id,
            application_id,
            profil_requetes,
            ParametresLevier(
                date_reference=date_du_jour(),
                region=charger_tenant(self._store, tenant_id).region_principale,
            ),
            self._tarifs,
        )
        entree, sortie = jetons_par_requete(contexte)
        type_tache = _type_tache(application, profil_requetes)
        return ProfilTache(
            application_id=application.id,
            type_tache=type_tache,
            controles_deterministes=controles_de(type_tache),
            seuil_qualite=application.seuil_qualite,
            seuil_intervalle_bas=application.seuil_intervalle_bas,
            relecture_humaine=application.relecture_humaine_autorisee,
            modele_actuel=application.modele_actuel,
            volume_mensuel_requetes=volume_mensuel(contexte),
            jetons_entree_moyens=int(entree),
            jetons_sortie_moyens=int(sortie),
            stratums=STRATES_LONGUEUR,
        )


def _type_tache(application: Application, profil: ProfilRequetes | None) -> str:
    """Type de tâche : d'abord le trafic observé, sinon la description."""
    if profil is not None and profil.requetes_par_type_tache:
        dominant = max(
            profil.requetes_par_type_tache.items(), key=lambda couple: (couple[1], couple[0])
        )[0]
        return type_tache_de(dominant)
    return type_tache_d_description(application.description_tache)
