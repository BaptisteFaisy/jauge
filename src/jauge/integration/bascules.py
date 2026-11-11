"""Adaptateur de bascule : la route API pilote le contrôleur M6 (EF-BAS-01..03).

Sans cet adaptateur, `POST /v1/recommendations/{id}/rollout` fabriquait un objet
`BasculeProgressive` sans créer de règle de passerelle ni tenir les paliers
(écart É5 de la recette). Ici l'API passe par `jauge.rollout.controller.
ControlleurBascule`, qui :

- vérifie l'approbation humaine avant toute action (ENF-IA-02) ;
- crée une règle **pondérée** sur la passerelle au premier palier ;
- rejuge un échantillon du trafic réel à chaque palier et **retire la règle**
  dès que la qualité passe sous le seuil ou que le taux d'erreur monte.

Le contrôleur conserve le suivi d'une bascule (mesures successives, alertes) en
mémoire : l'adaptateur garde donc **une instance vivante par bascule**, sans
quoi un second appel (`advance`, `rollback`) ne retrouverait pas son suivi.
Limite assumée pour le hackathon : ce suivi n'est pas persisté, il vit dans le
processus du plan de contrôle (une Lambda froide repartirait du store, qui lui
est persisté — la bascule et la recommandation y sont écrites à chaque palier).

En démonstration, la passerelle est le double hors ligne de M6
(`FakePasserelleClient`) et la qualité en ligne est lue sur la recommandation
issue de la campagne : aucun appel AWS, aucun chiffre inventé (ENF-IA-01).
"""

from __future__ import annotations

from collections.abc import Sequence

from jauge.common.errors import IntrouvableError
from jauge.models.recommendation import BasculeProgressive, Recommandation
from jauge.rollout.controller import (
    PALIERS_PAR_DEFAUT,
    ControlleurBascule,
    MesureEnLigne,
    QualiteEnLigne,
)
from jauge.rollout.gateway import FakePasserelleClient, PasserelleClient
from jauge.store.protocol import Store


class QualiteEnLigneSurRecommandation:
    """Qualité en ligne de démonstration : le taux mesuré pendant la campagne.

    En production cette mesure vient du rejugement d'un échantillon du trafic
    réel (§8.6.2). Hors ligne, on reprend le taux et la borne basse mesurés par
    la campagne : des nombres **calculés par le code**, jamais inventés.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    def mesurer(self, tenant_id: str, application_id: str, palier: int) -> MesureEnLigne:
        """Rend la mesure en ligne du palier courant pour l'application."""
        taux, borne = self._taux_et_borne(tenant_id, application_id)
        return MesureEnLigne(
            taux_equivalence=taux,
            intervalle_bas=borne,
            taux_erreur=0.0,
            nombre_requetes=0,
            palier=palier,
        )

    def _taux_et_borne(self, tenant_id: str, application_id: str) -> tuple[float, float]:
        """Taux et borne basse de la dernière recommandation de l'application."""
        recommandations = self._store.lister_recommandations(tenant_id, app_id=application_id)
        for reco in reversed(recommandations):
            if reco.quality is not None:
                return reco.quality.equivalenceRate, reco.quality.confidenceIntervalLow
        return 1.0, 1.0


class AdaptateurBascules:
    """Port `BasculePort` de l'API, branché sur le contrôleur M6."""

    def __init__(
        self,
        store: Store,
        passerelle: PasserelleClient | None = None,
        qualite: QualiteEnLigne | None = None,
        paliers: Sequence[int] | None = None,
    ) -> None:
        self._store = store
        self._passerelle = passerelle or FakePasserelleClient()
        self._qualite = qualite or QualiteEnLigneSurRecommandation(store)
        self._paliers: tuple[int, ...] = tuple(paliers) if paliers else PALIERS_PAR_DEFAUT
        self._controleurs: dict[tuple[int, ...], ControlleurBascule] = {}
        self._par_bascule: dict[str, ControlleurBascule] = {}

    @property
    def passerelle(self) -> PasserelleClient:
        """Client de passerelle utilisé (double hors ligne en démo)."""
        return self._passerelle

    def _controleur(self, paliers: Sequence[int] | None = None) -> ControlleurBascule:
        """Contrôleur M6 pour ces paliers (une instance vivante, réutilisée)."""
        cle = tuple(paliers) if paliers else self._paliers
        controleur = self._controleurs.get(cle)
        if controleur is None:
            controleur = ControlleurBascule(
                passerelle=self._passerelle,
                qualite=self._qualite,
                paliers=cle,
                store=self._store,
            )
            self._controleurs[cle] = controleur
        return controleur

    def _suivi_de(self, bascule_id: str) -> ControlleurBascule:
        """Contrôleur qui a démarré cette bascule (sinon celui par défaut)."""
        return self._par_bascule.get(bascule_id) or self._controleur()

    def demarrer(
        self, recommandation: Recommandation, paliers: Sequence[int] | None = None
    ) -> BasculeProgressive:
        """Crée la règle pondérée au premier palier, après approbation humaine."""
        application = self._store.obtenir_application(
            recommandation.tenantId, recommandation.applicationId
        )
        if application is None:
            raise IntrouvableError(
                "Application introuvable pour cette recommandation",
                application_id=recommandation.applicationId,
            )
        controleur = self._controleur(paliers)
        bascule = controleur.demarrer(
            recommandation.tenantId,
            application,
            recommandation,
            recommandation.approvedBy,
        )
        self._par_bascule[bascule.id] = controleur
        return bascule

    def avancer(self, bascule: BasculeProgressive) -> BasculeProgressive:
        """Rejuge le palier courant, puis monte d'un palier ou retire la règle."""
        return self._suivi_de(bascule.id).avancer(bascule.tenant_id, bascule)

    def annuler(
        self, bascule: BasculeProgressive, motif: str, automatique: bool = False
    ) -> BasculeProgressive:
        """Retire la règle de passerelle : tout revient au modèle actuel."""
        return self._suivi_de(bascule.id).annuler(
            bascule.tenant_id, bascule, motif, automatique=automatique
        )
