"""Jeu d'essai des ports : services fictifs, cloisonnés, sans réseau.

Sert aux tests des agents (`tests/agents/`) et à la démonstration hors ligne.
Aucun appel AWS, aucun appel LLM, aucun accès disque : tout est en mémoire.

Deux propriétés sont volontairement reproduites, parce que les agents en
dépendent :

- **cloisonnement** (ENF-SEC-02) : une application ou une campagne d'un autre
  client lève `CloisonnementError`, comme le fera l'adaptateur réel ;
- **calculs déterministes** (ENF-IA-01) : l'estimation de coût et les valeurs
  de recommandation sont calculées ici, à partir de la table des tarifs du
  socle (`jauge.engine.calculator`), jamais par un modèle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from jauge.agents.ports import (
    ClusterEchec,
    EntreesRecommandation,
    EstimationCampagne,
    ModeleCandidat,
    ProfilTache,
    Services,
)
from jauge.common.errors import CloisonnementError, IntrouvableError, ValidationError
from jauge.common.ids import aleatoire
from jauge.common.temps import aujourdhui as date_du_jour
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.campaign import ResultatCandidat, StatutCampagne
from jauge.models.cost import AgregatQuotidien, RapprochementJour, SyntheseCout
from jauge.models.lever import Levier
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.models.recommendation import Recommandation
from jauge.models.tenant import Application, Tenant

NIVEAU_REJEU = ServiceTier.FLEX
"""Le rejeu tourne au niveau Flex (§13.2, EF-REJ-03) : c'est le tarif estimé."""


@dataclass
class ServicesFictifs:
    """Implémentation en mémoire de tous les ports de `jauge.agents.ports`."""

    tenants: dict[str, Tenant] = field(default_factory=dict)
    applications: dict[str, Application] = field(default_factory=dict)
    agregats_par_tenant: dict[str, list[AgregatQuotidien]] = field(default_factory=dict)
    leviers_par_tenant: dict[str, list[Levier]] = field(default_factory=dict)
    rapprochements_par_tenant: dict[str, list[RapprochementJour]] = field(default_factory=dict)
    profils: dict[str, ProfilTache] = field(default_factory=dict)
    candidats_par_app: dict[str, list[ModeleCandidat]] = field(default_factory=dict)
    resultats_par_campagne: dict[str, list[ResultatCandidat]] = field(default_factory=dict)
    clusters_par_campagne: dict[str, list[ClusterEchec]] = field(default_factory=dict)
    entrees_par_campagne: dict[str, EntreesRecommandation] = field(default_factory=dict)
    tarifs: list = field(default_factory=list)
    campagnes: dict[str, dict[str, str]] = field(default_factory=dict)
    recommandations: list[Recommandation] = field(default_factory=list)
    demandes_validation: dict[str, str] = field(default_factory=dict)
    appels: list[tuple[str, dict]] = field(default_factory=list)
    """Journal d'appels : sert aux tests pour vérifier quel service a été sollicité."""

    # --- Fabrique ---------------------------------------------------------
    @classmethod
    def depuis_fixtures(
        cls, tenant: Tenant, applications: list[Application], tarifs: list
    ) -> ServicesFictifs:
        """Construit un jeu d'essai cohérent à partir des fixtures du socle."""
        services = cls(tarifs=list(tarifs))
        services.tenants[tenant.id] = tenant
        for app in applications:
            services.applications[app.id] = app
            services.profils[app.id] = ProfilTache(
                application_id=app.id,
                type_tache=app.description_tache,
                controles_deterministes=("format_json", "champs_obligatoires"),
                seuil_qualite=app.seuil_qualite,
                seuil_intervalle_bas=app.seuil_intervalle_bas,
                relecture_humaine=app.relecture_humaine_autorisee,
                modele_actuel=app.modele_actuel,
                volume_mensuel_requetes=310_000,
                jetons_entree_moyens=1_200,
                jetons_sortie_moyens=180,
                stratums=("court", "long"),
            )
        return services

    def ajouter_tenant(self, tenant: Tenant) -> None:
        """Déclare un client supplémentaire (test de cloisonnement)."""
        self.tenants[tenant.id] = tenant

    def _journaliser(self, operation: str, **details: object) -> None:
        self.appels.append((operation, dict(details)))

    def _tenant(self, tenant_id: str) -> Tenant:
        tenant = self.tenants.get(tenant_id)
        if tenant is None:
            raise CloisonnementError(
                f"Client inconnu : {tenant_id}", tenant_id=tenant_id, operation="tenant"
            )
        return tenant

    def _application(self, tenant_id: str, application_id: str) -> Application:
        app = self.applications.get(application_id)
        if app is None:
            raise IntrouvableError(
                f"Application inconnue : {application_id}", application_id=application_id
            )
        if app.tenant_id != tenant_id:
            raise CloisonnementError(
                "Application d'un autre client",
                tenant_id=tenant_id,
                application_id=application_id,
            )
        return app

    # --- PortReferentiel --------------------------------------------------
    def tenant(self, tenant_id: str) -> Tenant:
        return self._tenant(tenant_id)

    def application(self, tenant_id: str, application_id: str) -> Application:
        return self._application(tenant_id, application_id)

    # --- PortAgregats -----------------------------------------------------
    def synthese(
        self, tenant_id: str, application_id: str | None, debut: date, fin: date
    ) -> SyntheseCout:
        self._tenant(tenant_id)
        agregats = self.agregats(tenant_id, application_id, debut, fin)
        par_app: dict[str, float] = {}
        par_equipe: dict[str, float] = {}
        par_modele: dict[str, float] = {}
        for agr in agregats:
            par_app[agr.application_id] = (
                par_app.get(agr.application_id, 0.0) + agr.cout_recalcule_usd
            )
            par_equipe[agr.equipe] = par_equipe.get(agr.equipe, 0.0) + agr.cout_recalcule_usd
            par_modele[agr.modele_id] = par_modele.get(agr.modele_id, 0.0) + agr.cout_recalcule_usd
        total = round(sum(par_app.values()), 4)
        return SyntheseCout(
            tenant_id=tenant_id,
            periode_debut=debut,
            periode_fin=fin,
            cout_total_usd=total,
            cout_par_application=par_app,
            cout_par_equipe=par_equipe,
            cout_par_modele=par_modele,
            couverture_attribution_pourcentage=100.0 if total else 0.0,
            ecart_rapprochement_moyen_pourcentage=0.0,
        )

    def agregats(
        self, tenant_id: str, application_id: str | None, debut: date, fin: date
    ) -> list[AgregatQuotidien]:
        self._tenant(tenant_id)
        if application_id is not None:
            self._application(tenant_id, application_id)
        return [
            agr
            for agr in self.agregats_par_tenant.get(tenant_id, [])
            if debut <= agr.jour <= fin
            and (application_id is None or agr.application_id == application_id)
        ]

    # --- PortLeviers ------------------------------------------------------
    def leviers(self, tenant_id: str, application_id: str | None) -> list[Levier]:
        self._tenant(tenant_id)
        if application_id is not None:
            self._application(tenant_id, application_id)
        return [
            levier
            for levier in self.leviers_par_tenant.get(tenant_id, [])
            if application_id is None or levier.application_id == application_id
        ]

    # --- PortRapprochement ------------------------------------------------
    def rapprochements(self, tenant_id: str, debut: date, fin: date) -> list[RapprochementJour]:
        self._tenant(tenant_id)
        return [
            jour
            for jour in self.rapprochements_par_tenant.get(tenant_id, [])
            if debut <= jour.jour <= fin
        ]

    # --- PortProfilTache --------------------------------------------------
    def profil_tache(self, tenant_id: str, application_id: str) -> ProfilTache:
        self._application(tenant_id, application_id)
        profil = self.profils.get(application_id)
        if profil is None:
            raise IntrouvableError(
                f"Profil inconnu pour {application_id}", application_id=application_id
            )
        return profil

    # --- PortModelesCandidats --------------------------------------------
    def modeles_candidats(self, tenant_id: str, application_id: str) -> list[ModeleCandidat]:
        self._application(tenant_id, application_id)
        return list(self.candidats_par_app.get(application_id, []))

    # --- PortEstimationCout ----------------------------------------------
    def estimer(
        self,
        tenant_id: str,
        application_id: str,
        modeles: list[str],
        taille_echantillon: int,
    ) -> EstimationCampagne:
        """Estime le coût du rejeu au niveau Flex (référence + candidats)."""
        tenant = self._tenant(tenant_id)
        profil = self.profil_tache(tenant_id, application_id)
        if not modeles:
            raise ValidationError("Aucun modèle candidat fourni", application_id=application_id)
        if taille_echantillon <= 0:
            raise ValidationError("Taille d'échantillon invalide", taille=taille_echantillon)
        table = TableTarifs(self.tarifs)
        metriques = MetriquesRequete(
            jetons_entree=profil.jetons_entree_moyens, jetons_sortie=profil.jetons_sortie_moyens
        )
        aujourdhui = date_du_jour()
        par_modele: dict[str, float] = {}
        for modele_id in [profil.modele_actuel, *modeles]:
            tarif = table.trouver(modele_id, tenant.region_principale, NIVEAU_REJEU, aujourdhui)
            cout = calculer_cout_requete(tarif, metriques).cout_total
            par_modele[modele_id] = round(cout * taille_echantillon, 6)
        total = round(sum(par_modele.values()), 6)
        self._journaliser(
            "estimer",
            application_id=application_id,
            modeles=tuple(modeles),
            taille=taille_echantillon,
        )
        return EstimationCampagne(
            montant_usd=total,
            devise="USD",
            taille_echantillon=taille_echantillon,
            plafond_usd=tenant.plafond_campagne_usd,
            par_modele_usd=par_modele,
        )

    # --- PortRejeu --------------------------------------------------------
    def lancer(
        self,
        tenant_id: str,
        application_id: str,
        modeles: list[str],
        taille_echantillon: int,
        graine: int,
    ) -> str:
        self._application(tenant_id, application_id)
        campagne_id = aleatoire("campaign")
        self.campagnes[campagne_id] = {
            "tenant_id": tenant_id,
            "application_id": application_id,
            "modeles": ",".join(modeles),
            "taille": str(taille_echantillon),
            "graine": str(graine),
        }
        self._journaliser("lancer_rejeu", campagne_id=campagne_id, taille=taille_echantillon)
        return campagne_id

    def statut(self, tenant_id: str, campagne_id: str) -> StatutCampagne:
        self._campagne(tenant_id, campagne_id)
        return StatutCampagne.PLANIFIEE

    def _campagne(self, tenant_id: str, campagne_id: str) -> dict[str, str]:
        campagne = self.campagnes.get(campagne_id)
        if campagne is None:
            raise IntrouvableError(f"Campagne inconnue : {campagne_id}", campagne_id=campagne_id)
        if campagne["tenant_id"] != tenant_id:
            raise CloisonnementError(
                "Campagne d'un autre client", tenant_id=tenant_id, campagne_id=campagne_id
            )
        return campagne

    def enregistrer_campagne(self, campagne_id: str, tenant_id: str, application_id: str) -> None:
        """Déclare une campagne déjà calculée (jeu d'essai des résultats)."""
        self.campagnes[campagne_id] = {
            "tenant_id": tenant_id,
            "application_id": application_id,
            "modeles": "",
            "taille": "0",
            "graine": "0",
        }

    # --- PortResultats ----------------------------------------------------
    def resultats(self, tenant_id: str, campagne_id: str) -> list[ResultatCandidat]:
        self._campagne(tenant_id, campagne_id)
        return list(self.resultats_par_campagne.get(campagne_id, []))

    def familles_en_echec(self, tenant_id: str, campagne_id: str) -> list[ClusterEchec]:
        self._campagne(tenant_id, campagne_id)
        return list(self.clusters_par_campagne.get(campagne_id, []))

    # --- PortEntreesRecommandation ---------------------------------------
    def valeurs(self, tenant_id: str, campagne_id: str) -> EntreesRecommandation:
        self._campagne(tenant_id, campagne_id)
        entrees = self.entrees_par_campagne.get(campagne_id)
        if entrees is None:
            raise IntrouvableError(
                "Valeurs de recommandation indisponibles", campagne_id=campagne_id
            )
        return entrees

    # --- PortEnregistrement ----------------------------------------------
    def enregistrer(self, tenant_id: str, recommandation: Recommandation) -> str:
        if recommandation.tenantId != tenant_id:
            raise CloisonnementError(
                "Recommandation d'un autre client",
                tenant_id=tenant_id,
                recommandation_id=recommandation.id,
            )
        self.recommandations.append(recommandation)
        self._journaliser("enregistrer_recommandation", recommandation_id=recommandation.id)
        return recommandation.id

    # --- PortValidationHumaine -------------------------------------------
    def demander(
        self, tenant_id: str, application_id: str, campagne_id: str, modele_cible: str
    ) -> str:
        self._application(tenant_id, application_id)
        demande_id = aleatoire("validation")
        self.demandes_validation[demande_id] = "en_attente"
        self._journaliser("demande_validation", demande_id=demande_id, modele=modele_cible)
        return demande_id

    def statut_validation(self, tenant_id: str, demande_id: str) -> str:
        statut = self.demandes_validation.get(demande_id)
        if statut is None:
            raise IntrouvableError(
                f"Demande de validation inconnue : {demande_id}", demande_id=demande_id
            )
        return statut

    def approuver(self, demande_id: str) -> None:
        """Simule la décision d'un humain (hors agents, ENF-IA-02)."""
        if demande_id not in self.demandes_validation:
            raise IntrouvableError(f"Demande inconnue : {demande_id}", demande_id=demande_id)
        self.demandes_validation[demande_id] = "approuvee"

    # --- Aides de construction du jeu d'essai -----------------------------
    def remplir_periode(self, agregats: list[AgregatQuotidien]) -> None:
        """Ajoute des agrégats au jeu d'essai, indexés par client."""
        for agr in agregats:
            self.agregats_par_tenant.setdefault(agr.tenant_id, []).append(agr)

    def remplir_leviers(self, leviers: list[Levier]) -> None:
        for levier in leviers:
            self.leviers_par_tenant.setdefault(levier.tenant_id, []).append(levier)

    def remplir_rapprochements(self, jours: list[RapprochementJour]) -> None:
        for jour in jours:
            self.rapprochements_par_tenant.setdefault(jour.tenant_id, []).append(jour)

    def remplir_resultats(
        self,
        campagne_id: str,
        resultats: list[ResultatCandidat],
        clusters: list[ClusterEchec] | None = None,
        entrees: EntreesRecommandation | None = None,
    ) -> None:
        self.resultats_par_campagne[campagne_id] = list(resultats)
        self.clusters_par_campagne[campagne_id] = list(clusters or [])
        if entrees is not None:
            self.entrees_par_campagne[campagne_id] = entrees

    def vers_services(self) -> Services:
        """Assemble les ports en `Services` : c'est ce que reçoivent les agents.

        Le jeu d'essai implémente les douze protocoles à lui seul ; cette
        méthode évite de répéter l'assemblage dans chaque test.
        """
        return Services(
            referentiel=self,
            agregats=self,
            leviers=self,
            profils=self,
            rapprochement=self,
            candidats=self,
            estimation=self,
            rejeu=self,
            resultats=self,
            entrees=self,
            enregistrement=self,
            validation=self,
        )

    def periode(self, jours: int) -> tuple[date, date]:
        """Fenêtre glissante de `jours` jours finissant aujourd'hui."""
        fin = date_du_jour()
        return fin - timedelta(days=jours), fin
