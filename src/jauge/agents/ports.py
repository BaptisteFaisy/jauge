"""Ports d'accès aux services dont dépendent les agents Strands (§12).

Exigences visées : §12.1 (organisation), §12.3 (garde-fous), EF-RAP-04
(question en langage naturel), EF-EVA-03 (taux et intervalle), ENF-CONF-01
(seuls des agrégats et des identifiants circulent), ENF-SEC-02 (cloisonnement),
ENF-IA-01 (les nombres sont calculés par le code, jamais par un modèle).

Les agents ne connaissent que ces **protocoles**. Les services réels viennent
d'autres branches (M1 attribution, M3 leviers, M4 rejeu, M5 évaluation, M6
bascule) : le câblage concret se fait dans la PR d'intégration (M15). Le jeu
d'essai (`jauge.agents.fakes.ServicesFictifs`) sert aux tests et à la démo.

Deux règles structurent tous les contrats :

1. `tenant_id` est le premier argument de chaque lecture ; une donnée d'un
   autre client lève `CloisonnementError` (ENF-SEC-02).
2. Aucun DTO ne transporte de texte de requête ni de réponse candidate : les
   paires rejouées restent dans le plan de données (`PaireRejoueeDataPlane`),
   jamais dans un port d'agent (ENF-CONF-01, §12.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from jauge.models.campaign import ResultatCandidat, StatutCampagne
from jauge.models.cost import AgregatQuotidien, RapprochementJour, SyntheseCout
from jauge.models.lever import Levier
from jauge.models.recommendation import (
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
)
from jauge.models.tenant import Application, Tenant

# --- Objets de transfert (agrégats, scores, identifiants uniquement) --------


@dataclass(frozen=True)
class ProfilTache:
    """Profil d'une application, tel qu'un planificateur de rejeu en a besoin.

    Sert EF-EVA-02 (contrôles déterministes du type de tâche) et le
    dimensionnement de l'échantillon (§13.1). Aucune requête, aucun texte.
    """

    application_id: str
    type_tache: str
    controles_deterministes: tuple[str, ...]
    seuil_qualite: float
    seuil_intervalle_bas: float
    relecture_humaine: bool
    modele_actuel: str
    volume_mensuel_requetes: int
    jetons_entree_moyens: int
    jetons_sortie_moyens: int
    stratums: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModeleCandidat:
    """Candidat proposable pour une campagne, du point de vue du client."""

    modele_id: str
    famille: str
    fenetre_contexte: int
    niveau_service: str = "standard"
    tarif_connu: bool = True
    autorise: bool = True


@dataclass(frozen=True)
class EstimationCampagne:
    """Coût estimé d'une campagne, calculé par le code (ENF-COUT-01)."""

    montant_usd: float
    devise: str
    taille_echantillon: int
    plafond_usd: float
    par_modele_usd: dict[str, float] = field(default_factory=dict)

    @property
    def dans_le_plafond(self) -> bool:
        """Vrai si l'estimation ne dépasse pas le plafond du client."""
        return self.montant_usd <= self.plafond_usd


@dataclass(frozen=True)
class ClusterEchec:
    """Famille de requêtes où un candidat échoue (§12.1, lecteur de résultats).

    `famille` est une étiquette de catégorie (par exemple « tickets en langue
    rare »), jamais un extrait de requête.
    """

    modele_id: str
    famille: str
    nombre_requetes: int
    nombre_echecs: int
    taux_echec: float


@dataclass(frozen=True)
class EntreesRecommandation:
    """Valeurs **calculées** d'une recommandation (Annexe B, ENF-IA-01).

    Les blocs `quality`, `cost` et `latency` sont remplis par le code (M5/M6 et
    le socle) ; l'agent rédacteur ne fait que recopier ces valeurs et écrire les
    risques. C'est la garantie qu'aucun chiffre ne sort d'un modèle.
    """

    tenant_id: str
    application_id: str
    campagne_id: str
    modele_actuel: str
    modele_cible: str
    taille_echantillon: int
    quality: MetriquesQualite
    cost: MetriquesCout
    latency: MetriquesLatence
    plan_bascule: tuple[int, ...] = (10, 50, 100)
    nombres_contexte: tuple[float, ...] = ()
    """Nombres calculés hors des blocs de l'Annexe B, citables dans un texte.

    Par exemple les effectifs des familles en échec (« 4 échecs sur 5 ») : ils
    viennent des résultats de la campagne, jamais d'un modèle.
    """

    def valeurs_autorisees(self) -> set[float]:
        """Nombres qu'un agent a le droit de citer dans un texte (ENF-IA-01)."""
        nombres: set[float] = {float(self.taille_echantillon)}
        nombres.update(float(palier) for palier in self.plan_bascule)
        nombres.update(
            {
                self.quality.equivalenceRate,
                self.quality.confidenceIntervalLow,
                self.quality.selfAgreementOfCurrentModel,
                float(self.quality.deterministicFailures),
                self.cost.costPerRequestBefore,
                self.cost.costPerRequestAfter,
                float(self.cost.monthlyRequests),
                self.cost.expectedMonthlySaving,
                float(self.latency.p50Before),
                float(self.latency.p50After),
                float(self.latency.p95Before),
                float(self.latency.p95After),
            }
        )
        if self.quality.judgeHumanAgreement is not None:
            nombres.add(self.quality.judgeHumanAgreement)
        nombres.update(float(nombre) for nombre in self.nombres_contexte)
        return nombres


# --- Protocoles ------------------------------------------------------------


@runtime_checkable
class PortReferentiel(Protocol):
    """Clients et applications : plafond, modèles autorisés, seuils (§14)."""

    def tenant(self, tenant_id: str) -> Tenant: ...

    def application(self, tenant_id: str, application_id: str) -> Application: ...


@runtime_checkable
class PortAgregats(Protocol):
    """Agrégats de coût (M1, EF-ATT-05) : totaux, par modèle, par jour."""

    def synthese(
        self, tenant_id: str, application_id: str | None, debut: date, fin: date
    ) -> SyntheseCout: ...

    def agregats(
        self, tenant_id: str, application_id: str | None, debut: date, fin: date
    ) -> list[AgregatQuotidien]: ...


@runtime_checkable
class PortLeviers(Protocol):
    """Leviers d'économie détectés (M3, Annexe A)."""

    def leviers(self, tenant_id: str, application_id: str | None) -> list[Levier]: ...


@runtime_checkable
class PortRapprochement(Protocol):
    """Rapprochement coût recalculé / facture (EF-ATT-06, §4)."""

    def rapprochements(self, tenant_id: str, debut: date, fin: date) -> list[RapprochementJour]: ...


@runtime_checkable
class PortProfilTache(Protocol):
    """Profil de la tâche d'une application (type, contrôles, volumes)."""

    def profil_tache(self, tenant_id: str, application_id: str) -> ProfilTache: ...


@runtime_checkable
class PortModelesCandidats(Protocol):
    """Modèles candidats autorisés pour un client (EF-ADM-01, §12.3)."""

    def modeles_candidats(self, tenant_id: str, application_id: str) -> list[ModeleCandidat]: ...


@runtime_checkable
class PortEstimationCout(Protocol):
    """Estimation déterministe du coût d'une campagne (ENF-COUT-01, M4)."""

    def estimer(
        self,
        tenant_id: str,
        application_id: str,
        modeles: list[str],
        taille_echantillon: int,
    ) -> EstimationCampagne: ...


@runtime_checkable
class PortRejeu(Protocol):
    """Lancement d'une campagne de rejeu dans le compte client (§8.4, M4)."""

    def lancer(
        self,
        tenant_id: str,
        application_id: str,
        modeles: list[str],
        taille_echantillon: int,
        graine: int,
    ) -> str: ...

    def statut(self, tenant_id: str, campagne_id: str) -> StatutCampagne: ...


@runtime_checkable
class PortResultats(Protocol):
    """Résultats consolidés et familles en échec (M5, §12.1)."""

    def resultats(self, tenant_id: str, campagne_id: str) -> list[ResultatCandidat]: ...

    def familles_en_echec(self, tenant_id: str, campagne_id: str) -> list[ClusterEchec]: ...


@runtime_checkable
class PortEntreesRecommandation(Protocol):
    """Valeurs calculées d'une recommandation (Annexe B, §8.5).

    `valeurs` renvoie les blocs `quality`, `cost` et `latency` déjà calculés
    ainsi que le plan de bascule : l'agent rédacteur les recopie sans les
    recalculer (ENF-IA-01).
    """

    def valeurs(self, tenant_id: str, campagne_id: str) -> EntreesRecommandation: ...


@runtime_checkable
class PortEnregistrement(Protocol):
    """Enregistrement d'une recommandation validée (EF-REC-01)."""

    def enregistrer(self, tenant_id: str, recommandation: Recommandation) -> str: ...


@runtime_checkable
class PortValidationHumaine(Protocol):
    """Interruption de validation humaine avant enregistrement (§12.3).

    `demander` crée une demande en attente et renvoie son identifiant ;
    `statut_validation` vaut `en_attente`, `approuvee` ou `refusee`. Aucun agent ne peut
    approuver : seul un humain, hors de ce port, fait passer une demande à
    `approuvee` (ENF-IA-02).
    """

    def demander(
        self,
        tenant_id: str,
        application_id: str,
        campagne_id: str,
        modele_cible: str,
    ) -> str: ...

    def statut_validation(self, tenant_id: str, demande_id: str) -> str: ...


# --- Assemblage des ports ---------------------------------------------------


@dataclass(frozen=True)
class Services:
    """Tous les services auxquels les agents accèdent, par protocole.

    Le câblage réel (M1/M3/M4/M5/M6 + `Store`) est fait par la PR
    d'intégration ; les agents ne voient que ce paquet.
    """

    referentiel: PortReferentiel
    agregats: PortAgregats
    leviers: PortLeviers
    rapprochement: PortRapprochement
    profils: PortProfilTache
    candidats: PortModelesCandidats
    estimation: PortEstimationCout
    rejeu: PortRejeu
    resultats: PortResultats
    entrees: PortEntreesRecommandation
    enregistrement: PortEnregistrement
    validation: PortValidationHumaine
