"""Machine de bascule par paliers, avec rejugement en ligne et rollback automatique.

Répond aux exigences :
- EF-BAS-02 : paliers 10 % → 50 % → 100 % ; à chaque palier un échantillon du
  trafic réel est rejugé par un juge en ligne.
- EF-BAS-03 : si la qualité mesurée passe sous le seuil de l'application, ou si
  le taux d'erreur monte au-dessus de sa référence, la règle est **retirée** et
  l'échec est signalé (motif + alerte) sans intervention humaine.
- EF-REC-02 / ENF-IA-02 : la bascule n'est **jamais** décidée par un agent ; elle
  n'existe qu'après approbation explicite du responsable d'application. Sans
  approbation, `ApprobationRequiseError` — le contrôleur n'a aucun mode
  « automatique ».
- ENF-CONF-01 : le contrôleur ne manipule que des taux, des identifiants et des
  pourcentages. `MesureEnLigne` ne peut pas transporter de texte de requête.

Le rejugement lui-même (juge Bedrock en ligne) est derrière le protocole
`QualiteEnLigne` : en production l'adaptateur appelle la tâche d'évaluation dans
le compte client, dans les tests `FakeQualiteEnLigne` rejoue une suite de mesures.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from jauge.common.errors import (
    ApprobationRequiseError,
    CloisonnementError,
    EtatIncompatibleError,
    IntrouvableError,
    ValidationError,
)
from jauge.common.ids import aleatoire
from jauge.models.recommendation import BasculeProgressive, Recommandation, StatutRecommandation
from jauge.models.tenant import Application
from jauge.rollout.gateway import PasserelleClient
from jauge.store.protocol import Store

# Paliers de l'EF-BAS-02 (§8.6). Le dernier palier doit valoir 100.
PALIERS_PAR_DEFAUT: tuple[int, ...] = (10, 50, 100)

# Hausse de taux d'erreur tolérée avant rollback (points de pourcentage absolus).
MARGE_ERREUR_PAR_DEFAUT = 0.02


@dataclass(frozen=True)
class MesureEnLigne:
    """Résultat du rejugement d'un échantillon du trafic réel à un palier.

    Aucun texte de requête ni de réponse : uniquement des taux agrégés
    (ENF-CONF-01, ENF-IA-01 — les nombres viennent du juge, pas d'un agent).
    """

    taux_equivalence: float
    intervalle_bas: float
    taux_erreur: float
    nombre_requetes: int = 0
    palier: int = 0

    def __post_init__(self) -> None:
        for nom, valeur in (
            ("taux_equivalence", self.taux_equivalence),
            ("intervalle_bas", self.intervalle_bas),
            ("taux_erreur", self.taux_erreur),
        ):
            if not 0.0 <= valeur <= 1.0:
                raise ValidationError(
                    f"Mesure en ligne incohérente : {nom} hors de [0, 1].", champ=nom, valeur=valeur
                )
        if self.nombre_requetes < 0:
            raise ValidationError("Mesure en ligne incohérente : nombre de requêtes négatif.")


class QualiteEnLigne(Protocol):
    """Rejuge un échantillon du trafic réel pendant la bascule (§8.6.2)."""

    def mesurer(self, tenant_id: str, application_id: str, palier: int) -> MesureEnLigne: ...


class FakeQualiteEnLigne:
    """Juge en ligne fictif : rejoue une suite de mesures prévues.

    Sert aux tests et à la démo (scénario R7 nominal, R8 qualité dégradée).
    """

    def __init__(self, mesures: Iterable[MesureEnLigne]) -> None:
        self._file: list[MesureEnLigne] = list(mesures)
        self.appels: list[tuple[str, str, int]] = []

    def mesurer(self, tenant_id: str, application_id: str, palier: int) -> MesureEnLigne:
        self.appels.append((tenant_id, application_id, palier))
        if not self._file:
            raise EtatIncompatibleError(
                "Aucune mesure en ligne prévue pour ce palier.",
                application_id=application_id,
                palier=palier,
            )
        mesure = self._file.pop(0)
        return MesureEnLigne(
            taux_equivalence=mesure.taux_equivalence,
            intervalle_bas=mesure.intervalle_bas,
            taux_erreur=mesure.taux_erreur,
            nombre_requetes=mesure.nombre_requetes,
            palier=palier,
        )


@dataclass
class SuiviBascule:
    """État volatil d'une bascule : référence d'erreur et historique des paliers.

    `BasculeProgressive` (socle) ne porte que l'état publié ; ce suivi garde la
    référence du taux d'erreur et les mesures, pour l'audit et les tests.
    """

    bascule_id: str
    tenant_id: str
    seuil_qualite: float = 0.95
    seuil_intervalle: float = 0.90
    taux_erreur_reference: float = 0.0
    mesures: list[MesureEnLigne] = field(default_factory=list)
    alertes: list[str] = field(default_factory=list)


def verifier_paliers(paliers: Sequence[int]) -> tuple[int, ...]:
    """Contrôle que les paliers montent strictement et finissent à 100 %."""
    valeurs = tuple(paliers)
    if not valeurs or valeurs[-1] != 100:
        raise ValidationError("Le dernier palier de bascule doit valoir 100 %.", paliers=valeurs)
    if list(valeurs) != sorted(set(valeurs)):
        raise ValidationError(
            "Les paliers doivent être croissants et sans doublon.", paliers=valeurs
        )
    return valeurs


def prochain_palier(paliers: Sequence[int], palier_actuel: int) -> int | None:
    """Palier suivant, ou `None` si le palier courant est le dernier."""
    suivants = [p for p in paliers if p > palier_actuel]
    return suivants[0] if suivants else None


def motif_rollback(
    mesure: MesureEnLigne,
    seuil_qualite: float,
    seuil_intervalle: float,
    taux_erreur_reference: float,
    marge_erreur: float,
) -> str | None:
    """Motif d'annulation automatique, ou `None` si le palier est acceptable.

    Trois causes possibles, dans l'ordre du §13.4 : taux d'équivalence sous le
    seuil de l'application, borne basse de l'intervalle sous son seuil, hausse du
    taux d'erreur au-delà de la marge au-dessus de la référence.
    """
    if mesure.taux_equivalence < seuil_qualite:
        return (
            f"qualité en ligne sous le seuil : {mesure.taux_equivalence:.3f} < {seuil_qualite:.3f}"
        )
    if mesure.intervalle_bas < seuil_intervalle:
        return (
            "borne basse de l'intervalle sous le seuil : "
            f"{mesure.intervalle_bas:.3f} < {seuil_intervalle:.3f}"
        )
    plafond_erreur = taux_erreur_reference + marge_erreur
    if mesure.taux_erreur > plafond_erreur:
        return (
            f"taux d'erreur en hausse : {mesure.taux_erreur:.3f} > {plafond_erreur:.3f} "
            f"(référence {taux_erreur_reference:.3f})"
        )
    return None


class ControlleurBascule:
    """Conduit une bascule approuvée, palier par palier, et annule au besoin.

    Le contrôleur ne décide rien seul : `demarrer` exige le nom du responsable
    qui a approuvé, et chaque `avancer` ne fait que mesurer, comparer aux seuils
    puis pousser le palier suivant ou retirer la règle.
    """

    def __init__(
        self,
        passerelle: PasserelleClient,
        qualite: QualiteEnLigne,
        paliers: Sequence[int] = PALIERS_PAR_DEFAUT,
        marge_erreur: float = MARGE_ERREUR_PAR_DEFAUT,
        store: Store | None = None,
    ) -> None:
        self.passerelle = passerelle
        self.qualite = qualite
        self.paliers = verifier_paliers(paliers)
        self.marge_erreur = marge_erreur
        self.store = store
        self._suivis: dict[str, SuiviBascule] = {}

    # --- Démarrage ---------------------------------------------------------

    def demarrer(
        self,
        tenant_id: str,
        application: Application,
        recommandation: Recommandation,
        approuve_par: str | None,
        taux_erreur_reference: float = 0.0,
    ) -> BasculeProgressive:
        """Crée la règle de passerelle au premier palier, après approbation."""
        self._controler_approbation(tenant_id, application, recommandation, approuve_par)
        passerelle_id = application.identifiant_passerelle or f"gw-{application.id}"
        palier = self.paliers[0]
        regle = self.passerelle.creer_regle_ponderee(
            passerelle_id=passerelle_id,
            modele_demande=application.modele_actuel,
            modele_cible=recommandation.targetModel,
            pourcentage_cible=palier,
            description=f"Jauge : {application.nom} vers {recommandation.targetModel}",
        )
        bascule = BasculeProgressive(
            id=aleatoire("rollout"),
            tenant_id=tenant_id,
            recommandation_id=recommandation.id,
            application_id=application.id,
            modele_cible=recommandation.targetModel,
            regle_passerelle_id=regle.id,
            palier_actuel_pourcentage=palier,
            qualite_en_ligne_mesuree=1.0,
            seuil_alerte=application.seuil_qualite,
            actif=True,
        )
        self._suivis[bascule.id] = SuiviBascule(
            bascule_id=bascule.id,
            tenant_id=tenant_id,
            seuil_qualite=application.seuil_qualite,
            seuil_intervalle=application.seuil_intervalle_bas,
            taux_erreur_reference=taux_erreur_reference,
        )
        self._enregistrer(bascule, StatutRecommandation.EN_COURS_DE_BASCULE)
        return bascule

    def _controler_approbation(
        self,
        tenant_id: str,
        application: Application,
        recommandation: Recommandation,
        approuve_par: str | None,
    ) -> None:
        """Refuse tout ce qui n'est pas une bascule explicitement approuvée."""
        if application.tenant_id != tenant_id or recommandation.tenantId != tenant_id:
            raise CloisonnementError(
                "Application ou recommandation hors du client demandé.",
                tenant_id=tenant_id,
                application_id=application.id,
            )
        if not approuve_par:
            raise ApprobationRequiseError(
                "Aucune bascule sans approbation du responsable d'application (ENF-IA-02).",
                application_id=application.id,
                recommandation_id=recommandation.id,
            )
        if recommandation.status is not StatutRecommandation.APPROUVEE:
            raise ApprobationRequiseError(
                f"La recommandation n'est pas approuvée : statut {recommandation.status.value}.",
                recommandation_id=recommandation.id,
                statut=recommandation.status.value,
            )
        if not application.passe_par_passerelle:
            raise EtatIncompatibleError(
                "Application hors passerelle : émettre un ticket (EF-BAS-04).",
                application_id=application.id,
            )

    # --- Progression -------------------------------------------------------

    def avancer(
        self, tenant_id: str, bascule: BasculeProgressive, mesure: MesureEnLigne | None = None
    ) -> BasculeProgressive:
        """Rejuge le palier courant, puis monte d'un palier ou annule.

        `mesure` peut être fournie (tests, rejeu différé) ; sinon elle est
        demandée au juge en ligne pour le palier courant.
        """
        suivi = self._suivi(tenant_id, bascule)
        if not bascule.actif:
            raise EtatIncompatibleError(
                "Bascule terminée ou annulée : plus de progression possible.",
                bascule_id=bascule.id,
            )
        courante = mesure or self.qualite.mesurer(
            tenant_id, bascule.application_id, bascule.palier_actuel_pourcentage
        )
        suivi.mesures.append(courante)
        motif = motif_rollback(
            courante,
            seuil_qualite=suivi.seuil_qualite,
            seuil_intervalle=suivi.seuil_intervalle,
            taux_erreur_reference=suivi.taux_erreur_reference,
            marge_erreur=self.marge_erreur,
        )
        if motif is not None:
            return self.annuler(tenant_id, bascule, motif, automatique=True)
        poussee = self._pousser_palier(bascule, courante)
        statut = (
            StatutRecommandation.EN_COURS_DE_BASCULE
            if poussee.actif
            else StatutRecommandation.APPLIQUEE
        )
        self._enregistrer(poussee, statut)
        return poussee

    def _pousser_palier(
        self, bascule: BasculeProgressive, mesure: MesureEnLigne
    ) -> BasculeProgressive:
        """Met à jour la règle au palier suivant, ou clôt la bascule à 100 %."""
        suivant = prochain_palier(self.paliers, bascule.palier_actuel_pourcentage)
        if suivant is None:
            return bascule.model_copy(
                update={
                    "qualite_en_ligne_mesuree": mesure.taux_equivalence,
                    "actif": False,
                }
            )
        self.passerelle.mettre_a_jour_poids(bascule.regle_passerelle_id, suivant)
        return bascule.model_copy(
            update={
                "palier_actuel_pourcentage": suivant,
                "qualite_en_ligne_mesuree": mesure.taux_equivalence,
            }
        )

    # --- Annulation --------------------------------------------------------

    def annuler(
        self,
        tenant_id: str,
        bascule: BasculeProgressive,
        motif: str,
        automatique: bool = True,
    ) -> BasculeProgressive:
        """Retire la règle : tout le trafic revient au modèle actuel (§8.6.2)."""
        suivi = self._suivi(tenant_id, bascule)
        if self.passerelle.lire_regle(bascule.regle_passerelle_id) is not None:
            self.passerelle.supprimer_regle(bascule.regle_passerelle_id)
        alerte = (
            f"Bascule {bascule.id} annulée sur l'application {bascule.application_id} : {motif}"
        )
        suivi.alertes.append(alerte)
        annulee = bascule.model_copy(
            update={
                "actif": False,
                "annule_automatiquement": automatique,
                "motif_annulation": motif,
            }
        )
        self._enregistrer(annulee, StatutRecommandation.ANNULEE)
        return annulee

    def _enregistrer(self, bascule: BasculeProgressive, statut: StatutRecommandation) -> None:
        """Persiste l'état de bascule et le statut de la recommandation (si store).

        Le store est optionnel : le contrôleur reste utilisable seul (tests
        unitaires), et le portail lui passe le store du plan de contrôle.
        """
        if self.store is None:
            return
        self.store.enregistrer_bascule(bascule)
        reco = self.store.obtenir_recommandation(bascule.tenant_id, bascule.recommandation_id)
        if reco is not None and reco.status is not statut:
            self.store.enregistrer_recommandation(reco.model_copy(update={"status": statut}))

    # --- Lecture -----------------------------------------------------------

    def _suivi(self, tenant_id: str, bascule: BasculeProgressive) -> SuiviBascule:
        """Suivi interne d'une bascule, avec contrôle de cloisonnement."""
        if bascule.tenant_id != tenant_id:
            raise CloisonnementError(
                "Bascule hors du client demandé.", tenant_id=tenant_id, bascule_id=bascule.id
            )
        suivi = self._suivis.get(bascule.id)
        if suivi is None:
            raise IntrouvableError(
                "Bascule inconnue de ce contrôleur (démarrer la bascule d'abord).",
                bascule_id=bascule.id,
            )
        return suivi

    def alertes(self, bascule_id: str) -> list[str]:
        """Alertes émises pour une bascule (R8 : l'échec doit être signalé)."""
        suivi = self._suivis.get(bascule_id)
        return list(suivi.alertes) if suivi else []

    def mesures(self, bascule_id: str) -> list[MesureEnLigne]:
        """Mesures en ligne successives d'une bascule (audit EF-ADM-02)."""
        suivi = self._suivis.get(bascule_id)
        return list(suivi.mesures) if suivi else []
