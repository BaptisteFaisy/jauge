"""Adaptateur du rejeu (port `PortRejeu`) : M4 (tirage + exécution) → M5 (évaluation).

Le port `lancer` exécute une campagne complète :

1. estimation du coût et contrôle du plafond (ENF-COUT-01) ;
2. tirage stratifié reproductible des requêtes (`jauge.replay.sampling`) depuis
   le **plan de données** (métadonnées seulement, jamais de texte, ENF-CONF-01) ;
3. exécution au tarif Flex (`jauge.replay.executor.ExecuteurRejeu`) avec un
   `ModeleClient` injecté : `FakeModeleClient` en démo, `BedrockModeleClient`
   hors démo ;
4. notation par le juge et contrôles déterministes (`jauge.evaluation`) ;
5. enregistrement de la `Campagne` dans le store, et de la trace (paires,
   clusters) dans le plan de données.

Exigences visées : EF-REJ-01..04, EF-EVA-01..04, ENF-COUT-01, ENF-CONF-01,
ENF-IA-01, ENF-SEC-02.
"""

from __future__ import annotations

from datetime import date

from jauge.agents.ports import PortRejeu
from jauge.common.errors import PlafondDepasseError
from jauge.common.ids import aleatoire
from jauge.engine.calculator import TableTarifs
from jauge.evaluation.judge import JugeClient
from jauge.integration.estimation import AdaptateurEstimation
from jauge.integration.evaluation import (
    charger_campagne,
    choisir_juge_campagne,
    cout_reel_rejeu,
    evaluer_rejeu,
)
from jauge.integration.plan_de_donnees import CampagneDataPlane, PlanDeDonnees
from jauge.integration.socle import charger_application, charger_tenant, type_tache_de
from jauge.models.campaign import Campagne, ParametresRejeu, ResultatCandidat, StatutCampagne
from jauge.models.tenant import Application
from jauge.replay.client import BedrockModeleClient, FakeModeleClient, ModeleClient
from jauge.replay.executor import ExecuteurRejeu, RejeuComplet, TextesEnMemoire
from jauge.replay.sampling import RequeteSource, tirer_echantillon
from jauge.store.protocol import Store


def creer_client_modele(*, mode_demo: bool, region: str) -> ModeleClient:
    """Sélectionne le client de modèle réel ou factice selon la configuration."""
    if mode_demo:
        return FakeModeleClient()
    return BedrockModeleClient(region=region)


class AdaptateurRejeu(PortRejeu):
    """Port de lancement et de suivi d'une campagne de rejeu."""

    def __init__(
        self,
        store: Store,
        table_tarifs: TableTarifs,
        plan: PlanDeDonnees,
        *,
        modele_client: ModeleClient | None = None,
        juge_client: JugeClient | None = None,
        estimation: AdaptateurEstimation | None = None,
        mode_demo: bool = True,
    ) -> None:
        self._store = store
        self._tarifs = table_tarifs
        self._plan = plan
        self._modele_client = modele_client
        self._juge_client = juge_client
        self._estimation = estimation or AdaptateurEstimation(store, table_tarifs)
        self._mode_demo = mode_demo

    def lancer(
        self,
        tenant_id: str,
        application_id: str,
        modeles: list[str],
        taille_echantillon: int,
        graine: int,
    ) -> str:
        """Exécute une campagne complète et rend son identifiant."""
        return self.lancer_campagne(
            tenant_id, application_id, modeles, taille_echantillon, graine
        ).id

    def statut(self, tenant_id: str, campagne_id: str) -> StatutCampagne:
        """Statut courant d'une campagne, après contrôle de cloisonnement."""
        return charger_campagne(self._store, tenant_id, campagne_id).statut

    def lancer_campagne(
        self,
        tenant_id: str,
        application_id: str,
        modeles: list[str],
        taille_echantillon: int,
        graine: int,
        *,
        modele_juge_id: str | None = None,
    ) -> Campagne:
        """Exécute la campagne complète et enregistre les résultats."""
        tenant = charger_tenant(self._store, tenant_id)
        application = charger_application(self._store, tenant_id, application_id)
        devis = self._estimation.estimer(tenant_id, application_id, modeles, taille_echantillon)
        self._verifier_plafond(devis.montant_usd, devis.plafond_usd)
        juge = modele_juge_id or choisir_juge_campagne(
            tenant.modeles_autorises, application.modele_actuel, modeles
        )
        campagne_id = aleatoire("campaign")
        rejeu = self._executer(
            campagne_id,
            tenant_id,
            application,
            modeles,
            taille_echantillon,
            graine,
            tenant.region_principale,
        )
        resultats, clusters, paires = evaluer_rejeu(
            application, rejeu, self._types(application, rejeu), self._juge_client, juge
        )
        campagne = self._assembler(
            campagne_id,
            tenant_id,
            application,
            modeles,
            graine,
            juge,
            devis.montant_usd,
            rejeu,
            resultats,
        )
        self._enregistrer(campagne, resultats, clusters, paires)
        return campagne

    def _verifier_plafond(self, cout_estime: float, plafond: float) -> None:
        """Refuse une campagne au-dessus du plafond du client (ENF-COUT-01)."""
        if cout_estime > plafond:
            raise PlafondDepasseError(
                "Coût estimé de la campagne au-dessus du plafond du client",
                cout_estime_usd=cout_estime,
                plafond_usd=plafond,
            )

    def _enregistrer(
        self,
        campagne: Campagne,
        resultats: list[ResultatCandidat],
        clusters: list[object],
        paires: list[object],
    ) -> None:
        """Écrit la campagne dans le store et sa trace dans le plan de données."""
        self._store.enregistrer_campagne(campagne)
        self._plan.enregistrer_campagne(
            CampagneDataPlane(
                campagne_id=campagne.id,
                resultats=list(resultats),
                clusters=clusters,
                paires=paires,
            )
        )

    def _assembler(
        self,
        campagne_id: str,
        tenant_id: str,
        application: Application,
        modeles: list[str],
        graine: int,
        juge: str,
        cout_estime: float,
        rejeu: RejeuComplet,
        resultats: list[ResultatCandidat],
    ) -> Campagne:
        """Assemble la campagne terminée à partir des mesures du rejeu."""
        return Campagne(
            id=campagne_id,
            tenant_id=tenant_id,
            application_id=application.id,
            modeles_candidats=list(modeles),
            taille_echantillon=rejeu.resume.taille_echantillon,
            graine_aleatoire=graine,
            modele_juge_id=juge,
            statut=StatutCampagne.TERMINEE,
            cout_estime_usd=cout_estime,
            cout_reel_usd=cout_reel_rejeu(rejeu),
            resultats=list(resultats),
        )

    def _executer(
        self,
        campagne_id: str,
        tenant_id: str,
        application: Application,
        modeles: list[str],
        taille_echantillon: int,
        graine: int,
        region: str,
    ) -> RejeuComplet:
        """Tire l'échantillon puis rejoue sur le modèle actuel et chaque candidat."""
        requetes = self._plan.requetes(tenant_id, application.id)
        sources = [
            RequeteSource(
                requete_id=r.requete_id,
                application_id=r.application_id,
                type_requete=r.type_requete,
                longueur_caracteres=r.longueur_caracteres,
                jetons_entree_origine=r.jetons_entree_origine,
            )
            for r in requetes
        ]
        echantillon = tirer_echantillon(
            sources, application_id=application.id, taille=taille_echantillon, graine=graine
        )
        # M4 rejoue toute la campagne avec un seul jeu de paramètres : on retient
        # ceux de la première requête de l'application. Un paramétrage par requête
        # demanderait une évolution du module (besoin signalé dans la PR).
        parametres = requetes[0].parametres if requetes else ParametresRejeu()
        client = self._modele_client or creer_client_modele(
            mode_demo=self._mode_demo, region=region
        )
        executeur = ExecuteurRejeu(client, self._tarifs, region=region, a_la_date=date.today())
        return executeur.rejouer(
            campagne_id=campagne_id,
            application_id=application.id,
            modele_actuel=application.modele_actuel,
            candidats=modeles,
            echantillon=echantillon.requetes,
            parametres=parametres,
            textes=TextesEnMemoire(self._plan.textes(tenant_id, application.id)),
        )

    def _types(self, application: Application, rejeu: RejeuComplet) -> dict[str, str]:
        """Types de tâche des requêtes échantillonnées (contrôles EF-EVA-02)."""
        types = {
            r.requete_id: type_tache_de(r.type_requete)
            for r in self._plan.requetes(application.tenant_id, application.id)
        }
        return {paire.requete_id: types.get(paire.requete_id, "general") for paire in rejeu.paires}
