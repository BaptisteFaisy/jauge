"""Adaptateurs de recommandation : entrées calculées, enregistrement, validation.

Trois ports de `jauge.agents.ports` sont branchés ici :

- `PortEntreesRecommandation` : les blocs `quality`, `cost` et `latency` de
  l'Annexe B, **calculés** par le code (résultats de la campagne M5, coûts du
  socle, économie mensuelle de `jauge.engine.stats`) ;
- `PortEnregistrement` : l'écriture de la recommandation dans le store, avec
  une trace d'audit (EF-ADM-02) ;
- `PortValidationHumaine` : l'interruption de l'enregistrement (§12.3). Une
  demande créée reste `en_attente` : **seul un humain** peut l'approuver, et
  l'agent n'a aucun moyen de le faire (ENF-IA-02).

Exigences visées : EF-REC-01, EF-REC-02, EF-ADM-02, §8.5, §12.3, ENF-IA-01.
"""

from __future__ import annotations

from jauge.agents.ports import (
    EntreesRecommandation,
    PortEnregistrement,
    PortEntreesRecommandation,
    PortValidationHumaine,
)
from jauge.api.audit import AuditSink, EvenementAudit
from jauge.common.errors import CloisonnementError, IntrouvableError, ValidationError
from jauge.common.ids import aleatoire
from jauge.engine.calculator import TableTarifs
from jauge.engine.stats import economie_mensuelle
from jauge.evaluation.decision import SeuilsDecision, decider_lot
from jauge.integration.evaluation import charger_campagne
from jauge.integration.plan_de_donnees import PlanDeDonnees
from jauge.integration.profil import AdaptateurProfilTache
from jauge.integration.socle import charger_application, charger_tenant
from jauge.models.campaign import ResultatCandidat
from jauge.models.recommendation import (
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
)
from jauge.models.tenant import Application
from jauge.store.protocol import Store

#: Paliers imposés par l'Annexe C (§8.6).
PLAN_BASCULE = (10, 50, 100)

#: Statuts possibles d'une demande de validation humaine (§12.3).
STATUT_EN_ATTENTE = "en_attente"
STATUT_APPROUVEE = "approuvee"
STATUT_REFUSEE = "refusee"


class AdaptateurEntrees(PortEntreesRecommandation):
    """Port des valeurs calculées d'une recommandation (Annexe B)."""

    def __init__(
        self,
        store: Store,
        table_tarifs: TableTarifs,
        plan: PlanDeDonnees,
        profils: AdaptateurProfilTache | None = None,
    ) -> None:
        self._store = store
        self._plan = plan
        self._profils = profils or AdaptateurProfilTache(store, table_tarifs, plan)

    def valeurs(self, tenant_id: str, campagne_id: str) -> EntreesRecommandation:
        """Valeurs calculées de la recommandation, à recopier telles quelles."""
        campagne = charger_campagne(self._store, tenant_id, campagne_id)
        application = charger_application(self._store, tenant_id, campagne.application_id)
        reference, cible = _modeles_retenus(campagne.resultats, application)
        volume = self._profils.profil_tache(tenant_id, application.id).volume_mensuel_requetes
        clusters = self._plan.campagne(campagne_id)
        nombres = _nombres_de_contexte(clusters.clusters if clusters else [], cible.modele_id)
        return EntreesRecommandation(
            tenant_id=tenant_id,
            application_id=application.id,
            campagne_id=campagne_id,
            modele_actuel=reference.modele_id,
            modele_cible=cible.modele_id,
            taille_echantillon=campagne.taille_echantillon,
            quality=_bloc_qualite(cible, reference),
            cost=_bloc_cout(reference, cible, volume),
            latency=_bloc_latence(reference, cible),
            plan_bascule=PLAN_BASCULE,
            nombres_contexte=nombres,
        )


def _modeles_retenus(
    resultats: list[ResultatCandidat], application: Application
) -> tuple[ResultatCandidat, ResultatCandidat]:
    """Référence et cible : le candidat conforme le moins cher, sinon le moins cher."""
    references = [resultat for resultat in resultats if resultat.est_reference]
    candidats = [resultat for resultat in resultats if not resultat.est_reference]
    if not references or not candidats:
        raise ValidationError(
            "Campagne sans référence ou sans candidat",
            application_id=application.id,
        )
    seuils = SeuilsDecision.depuis_application(application)
    conformes = [
        decision.resultat for decision in decider_lot(candidats, seuils) if decision.propose
    ]
    retenus = [resultat for resultat in conformes if resultat is not None] or candidats
    cible = min(retenus, key=lambda resultat: resultat.cout_par_requete_usd)
    return references[0], cible


def _bloc_qualite(cible: ResultatCandidat, reference: ResultatCandidat) -> MetriquesQualite:
    """Bloc `quality` de l'Annexe B (chiffres consolidés par le code)."""
    return MetriquesQualite(
        equivalenceRate=cible.taux_equivalence,
        confidenceIntervalLow=cible.intervalle_confiance_bas,
        selfAgreementOfCurrentModel=reference.auto_accord_modele_actuel,
        deterministicFailures=cible.echecs_deterministes,
        judgeHumanAgreement=cible.accord_juge_humain,
    )


def _bloc_cout(reference: ResultatCandidat, cible: ResultatCandidat, volume: int) -> MetriquesCout:
    """Bloc `cost` : coût par requête avant/après et économie mensuelle attendue."""
    return MetriquesCout(
        costPerRequestBefore=reference.cout_par_requete_usd,
        costPerRequestAfter=cible.cout_par_requete_usd,
        monthlyRequests=volume,
        expectedMonthlySaving=economie_mensuelle(
            reference.cout_par_requete_usd, cible.cout_par_requete_usd, volume
        ),
    )


def _bloc_latence(reference: ResultatCandidat, cible: ResultatCandidat) -> MetriquesLatence:
    """Bloc `latency` : p50/p95 avant et après."""
    return MetriquesLatence(
        p50Before=reference.latence_p50_ms,
        p50After=cible.latence_p50_ms,
        p95Before=reference.latence_p95_ms,
        p95After=cible.latence_p95_ms,
    )


def _nombres_de_contexte(clusters: list[object], modele_id: str) -> tuple[float, ...]:
    """Effectifs des familles en échec : citables dans un texte (ENF-IA-01)."""
    nombres: list[float] = []
    for cluster in clusters:
        if getattr(cluster, "modele_id", "") != modele_id:
            continue
        nombres.append(float(cluster.nombre_requetes))  # type: ignore[attr-defined]
        nombres.append(float(cluster.nombre_echecs))  # type: ignore[attr-defined]
    return tuple(nombres)


class AdaptateurEnregistrement(PortEnregistrement):
    """Port d'enregistrement d'une recommandation (store + audit)."""

    def __init__(self, store: Store, audit: AuditSink | None = None) -> None:
        self._store = store
        self._audit = audit

    def enregistrer(self, tenant_id: str, recommandation: Recommandation) -> str:
        """Enregistre la recommandation du client, après contrôle de cloisonnement."""
        charger_tenant(self._store, tenant_id)
        if recommandation.tenantId != tenant_id:
            raise CloisonnementError(
                "Recommandation d'un autre client",
                tenant_id=tenant_id,
                recommandation_id=recommandation.id,
            )
        self._store.enregistrer_recommandation(recommandation)
        self._journaliser(recommandation)
        return recommandation.id

    def _journaliser(self, recommandation: Recommandation) -> None:
        """Trace d'audit de l'enregistrement (EF-ADM-02)."""
        if self._audit is None:
            return
        self._audit.enregistrer(
            EvenementAudit(
                tenant_id=recommandation.tenantId,
                utilisateur_id="agents",
                role="agent",
                action="recommandation_enregistree",
                ressource_type="recommandation",
                ressource_id=recommandation.id,
                details={
                    "application_id": recommandation.applicationId,
                    "campagne_id": recommandation.campaignId,
                    "statut": recommandation.status.value,
                },
            )
        )


class AdaptateurValidation(PortValidationHumaine):
    """Port de validation humaine : l'interruption attendue par les agents (§12.3).

    Une demande est créée `en_attente`. Aucun agent ne peut l'approuver : la
    méthode `approuver` est une action **humaine**, hors du protocole des
    agents (ENF-IA-02).
    """

    def __init__(self, store: Store) -> None:
        self._store = store
        self._demandes: dict[str, dict[str, str]] = {}

    def demander(
        self, tenant_id: str, application_id: str, campagne_id: str, modele_cible: str
    ) -> str:
        """Crée une demande de validation et rend son identifiant."""
        charger_application(self._store, tenant_id, application_id)
        demande_id = aleatoire("validation")
        self._demandes[demande_id] = {
            "tenant_id": tenant_id,
            "application_id": application_id,
            "campagne_id": campagne_id,
            "modele_cible": modele_cible,
            "statut": STATUT_EN_ATTENTE,
        }
        return demande_id

    def statut_validation(self, tenant_id: str, demande_id: str) -> str:
        """Statut d'une demande : `en_attente`, `approuvee` ou `refusee`."""
        demande = self._demandes.get(demande_id)
        if demande is None:
            raise IntrouvableError(f"Demande de validation inconnue : {demande_id}")
        if demande["tenant_id"] != tenant_id:
            raise CloisonnementError(
                "Demande de validation d'un autre client",
                tenant_id=tenant_id,
                demande_id=demande_id,
            )
        return demande["statut"]

    def approuver(self, demande_id: str, par: str, statut: str = STATUT_APPROUVEE) -> str:
        """Décision **humaine** sur une demande ; aucun agent ne peut l'appeler."""
        demande = self._demandes.get(demande_id)
        if demande is None:
            raise IntrouvableError(f"Demande de validation inconnue : {demande_id}")
        if statut not in (STATUT_APPROUVEE, STATUT_REFUSEE):
            raise ValidationError("Statut de validation inconnu", statut=statut)
        demande["statut"] = statut
        demande["decideur"] = par
        return statut

    def demandes(self, tenant_id: str) -> list[dict[str, str]]:
        """Demandes de validation d'un client (pour le portail)."""
        return [demande for demande in self._demandes.values() if demande["tenant_id"] == tenant_id]
