"""Canary : 1 % du trafic réel, retour arrière pré-engagé, puis les paliers.

Séquence imposée : **shadow → canary → paliers** (10 → 50 → 100). Chaque étape
exige la précédente **réussie** et une **approbation humaine conservée**
(ENF-IA-02) ; seul le retour arrière est automatique (EF-BAS-03).

- le **canary** sert un petit pourcentage du trafic réel (1 % par défaut) via le
  `PasserelleClient` existant (EF-BAS-01) ;
- ses **déclencheurs de retour arrière sont écrits avant le lancement** : qualité
  sous le seuil, borne basse sous le plancher, taux d'erreur en hausse au-delà
  de la marge — les trois causes de `motif_rollback` (§13.4), réutilisé tel quel ;
- dès qu'un déclencheur fires, la règle est **retirée automatiquement**, et
  l'annulation est motivée puis journalisée ;
- les **paliers** (10 → 50 → 100) restent conduits par le contrôleur de bascule.

Journalisation : chaque étape laisse une trace (qui, quand, quel seuil, quelle
mesure, quelle décision), dans `jauge.rollout.journal` — aucun texte n'y figure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from jauge.common.errors import (
    ApprobationRequiseError,
    CloisonnementError,
    EtatIncompatibleError,
    JaugeError,
)
from jauge.models.campaign import ResultatCandidat
from jauge.models.recommendation import BasculeProgressive, Recommandation
from jauge.models.tenant import Application
from jauge.rollout.controller import (
    MARGE_ERREUR_PAR_DEFAUT,
    PALIERS_PAR_DEFAUT,
    ControlleurBascule,
    MesureEnLigne,
    QualiteEnLigne,
    motif_rollback,
)
from jauge.rollout.gateway import PasserelleClient
from jauge.rollout.journal import JournalEtape, JournalEtapes, StatutEtape, maintenant
from jauge.rollout.shadow import BilanShadow, MoteurShadow, ObservationReelle, ParametresShadow
from jauge.store.protocol import Store

# Étapes de la séquence imposée : shadow → canary → paliers (§8.6).
ETAPE_SHADOW = "shadow"
ETAPE_CANARY = "canary"
ETAPE_PALIERS = "paliers"

# Part du trafic réel servie par le canary avant les paliers (1 % par défaut).
PALIER_CANARY_DEFAUT = 1


class EtapePrealableManquante(JaugeError):
    """Étape précédente absente ou non concluante (§8.6, séquence imposée).

    Distincte d'`ApprobationRequiseError` : ici, ce n'est pas la décision
    humaine qui manque, c'est la **préliminaire** (le shadow concluant, le
    canary validé). Les deux sont bloquants, et aucun passage automatique n'est
    possible (ENF-IA-02).
    """

    code = "preceding_step_missing"
    statut_http = 409


@dataclass(frozen=True)
class DeclencheursCanary:
    """Déclencheurs de retour arrière, écrits **avant** le lancement du canary.

    Les trois causes sont celles de `motif_rollback` (§13.4) : qualité sous le
    seuil de l'application, borne basse de l'intervalle sous son seuil, taux
    d'erreur en hausse au-delà de la marge. Les seuils sont figés ici : ils sont
    journalisés au lancement, puis appliqués tels quels.
    """

    seuil_qualite: float
    seuil_intervalle_bas: float
    taux_erreur_reference: float
    marge_erreur: float = MARGE_ERREUR_PAR_DEFAUT

    @classmethod
    def depuis_application(
        cls,
        application: Application,
        taux_erreur_reference: float,
        marge_erreur: float = MARGE_ERREUR_PAR_DEFAUT,
    ) -> DeclencheursCanary:
        """Reprend les seuils de l'application (§13.4) et la référence d'erreur."""
        return cls(
            seuil_qualite=application.seuil_qualite,
            seuil_intervalle_bas=application.seuil_intervalle_bas,
            taux_erreur_reference=taux_erreur_reference,
            marge_erreur=marge_erreur,
        )

    def vers_paires(self) -> tuple[tuple[str, float], ...]:
        """Seuils sous forme de paires (nom, valeur), pour le journal."""
        return (
            ("seuil_qualite", self.seuil_qualite),
            ("seuil_intervalle_bas", self.seuil_intervalle_bas),
            ("taux_erreur_reference", self.taux_erreur_reference),
            ("marge_erreur", self.marge_erreur),
        )

    def evaluer(self, mesure: MesureEnLigne) -> str | None:
        """Motif de retour arrière, ou `None` si la mesure est acceptable."""
        return motif_rollback(
            mesure,
            seuil_qualite=self.seuil_qualite,
            seuil_intervalle=self.seuil_intervalle_bas,
            taux_erreur_reference=self.taux_erreur_reference,
            marge_erreur=self.marge_erreur,
        )


def controleur_de_sequence(
    passerelle: PasserelleClient,
    qualite: QualiteEnLigne,
    store: Store | None = None,
    palier_canary: int = PALIER_CANARY_DEFAUT,
    paliers: Sequence[int] = PALIERS_PAR_DEFAUT,
    marge_erreur: float = MARGE_ERREUR_PAR_DEFAUT,
) -> ControlleurBascule:
    """Contrôleur de la chaîne imposée : le canary, puis les paliers (§8.6)."""
    return ControlleurBascule(
        passerelle=passerelle,
        qualite=qualite,
        paliers=(palier_canary, *paliers),
        marge_erreur=marge_erreur,
        store=store,
    )


@dataclass(frozen=True)
class Canary:
    """État de l'étape canary : la règle pondérée et ses déclencheurs."""

    tenant_id: str
    application_id: str
    regle_id: str
    palier_pourcentage: int
    declencheurs: DeclencheursCanary
    bascule: BasculeProgressive


class SequenceBascule:
    """Séquence imposée : shadow → canary → paliers, une approbation par étape.

    Le chef de file ne décide rien : il vérifie que l'étape précédente a
    **réussi**, qu'une approbation humaine existe, exécute l'étape, puis
    journalise qui a décidé, sur quels seuils, sur quelle mesure.
    """

    def __init__(
        self,
        *,
        moteur: MoteurShadow,
        controleur: ControlleurBascule,
        journal: JournalEtapes | None = None,
    ) -> None:
        self.moteur = moteur
        self.controleur = controleur
        self.journal = journal or JournalEtapes()
        self.store: Store | None = controleur.store
        self._etats: dict[str, StatutEtape] = {
            ETAPE_SHADOW: StatutEtape.NON_LANCEE,
            ETAPE_CANARY: StatutEtape.NON_LANCEE,
        }
        self._bilan: BilanShadow | None = None
        self._bascule: BasculeProgressive | None = None
        self._declencheurs: DeclencheursCanary | None = None
        self._canary: Canary | None = None

    # --- Étape 1 : le shadow ----------------------------------------------

    def lancer_shadow(
        self,
        *,
        tenant_id: str,
        application: Application,
        recommandation: Recommandation,
        observations: Sequence[ObservationReelle],
        parametres: ParametresShadow,
        approuve_par: str | None,
    ) -> BilanShadow:
        """Lance le shadow après approbation ; au-delà du plafond, refus (ENF-COUT-01)."""
        self._exiger_approbation(ETAPE_SHADOW, approuve_par)
        self._controler_tenant(tenant_id, application, recommandation)
        plan = self.moteur.preparer(
            tenant_id=tenant_id,
            application_id=application.id,
            observations=observations,
            parametres=parametres,
        )
        self._journaliser_devis(tenant_id, application.id, approuve_par, plan.estimation)
        bilan = self.moteur.executer(plan, self._resultat_rejeu(tenant_id, recommandation))
        self._bilan = bilan
        statut, motif = self._conclusion_du_shadow(bilan)
        self._etats[ETAPE_SHADOW] = statut
        self._journaliser_bilan_shadow(
            tenant_id, application.id, approuve_par, statut, motif, bilan
        )
        return bilan

    def _journaliser_devis(self, tenant_id: str, application_id: str, qui: str, devis) -> None:
        """Journalise le devis du shadow : le coût chiffré **avant** le lancement."""
        self._journaliser(
            ETAPE_SHADOW,
            tenant_id,
            application_id,
            qui,
            "lance",
            mesures=(
                ("cout_estime_usd", devis.cout_total_usd),
                ("plafond_usd", devis.plafond_usd),
                ("nombre_requetes", float(devis.nombre_requetes)),
            ),
        )

    def _journaliser_bilan_shadow(
        self,
        tenant_id: str,
        application_id: str,
        qui: str,
        statut: StatutEtape,
        motif: str,
        bilan: BilanShadow,
    ) -> None:
        """Journalise la mesure du shadow : taux, écart labo/production, coût."""
        self._journaliser(
            ETAPE_SHADOW,
            tenant_id,
            application_id,
            qui,
            statut.value,
            mesures=(
                ("taux_equivalence", bilan.taux_equivalence),
                ("ecart_labo_production", bilan.ecart_labo_production or 0.0),
                ("cout_shadow_usd", bilan.cout_shadow_usd),
            ),
            motif=motif,
        )

    def _conclusion_du_shadow(self, bilan: BilanShadow) -> tuple[StatutEtape, str]:
        """Statut et motif de l'étape shadow : divergent, concluant, ou insuffisant."""
        if bilan.divergence:
            motif = (
                "divergence labo/production : "
                f"{bilan.taux_equivalence:.3f} en shadow contre "
                f"{bilan.taux_equivalence_rejeu or 0.0:.3f} au rejeu "
                f"(écart {bilan.ecart_labo_production or 0.0:.3f}, "
                f"seuil {bilan.seuil_ecart_labo_production:.3f})"
            )
            return (StatutEtape.DIVERGENTE, motif)
        if bilan.concluant:
            return (StatutEtape.CONCLUANTE, "")
        motif = (
            f"qualité insuffisante en shadow : {bilan.taux_equivalence:.3f} "
            f"(seuil {bilan.seuil_qualite:.3f})"
        )
        return (StatutEtape.NON_CONCLUANTE, motif)

    def _resultat_rejeu(
        self, tenant_id: str, recommandation: Recommandation
    ) -> ResultatCandidat | None:
        """Résultat de rejeu du candidat dans sa campagne (référence labo)."""
        if self.store is None:
            return None
        campagne = self.store.obtenir_campagne(tenant_id, recommandation.campaignId)
        if campagne is None:
            return None
        for resultat in campagne.resultats:
            if resultat.modele_id == recommandation.targetModel:
                return resultat
        return None

    # --- Étape 2 : le canary ----------------------------------------------

    def lancer_canary(
        self,
        *,
        tenant_id: str,
        application: Application,
        recommandation: Recommandation,
        approuve_par: str | None,
        taux_erreur_reference: float = 0.0,
    ) -> Canary:
        """Crée la règle pondérée du canary (1 % par défaut) après approbation."""
        self._exiger_etape_concluante(
            ETAPE_CANARY,
            ETAPE_SHADOW,
            "Le canary exige un shadow concluant : l'écart entre le rejeu et la "
            "production doit être connu avant de toucher au trafic réel.",
        )
        self._exiger_approbation(ETAPE_CANARY, approuve_par)
        self._controler_tenant(tenant_id, application, recommandation)
        bascule = self.controleur.demarrer(
            tenant_id, application, recommandation, approuve_par, taux_erreur_reference
        )
        declencheurs = DeclencheursCanary.depuis_application(
            application, taux_erreur_reference, self.controleur.marge_erreur
        )
        canary = Canary(
            tenant_id=tenant_id,
            application_id=application.id,
            regle_id=bascule.regle_passerelle_id,
            palier_pourcentage=bascule.palier_actuel_pourcentage,
            declencheurs=declencheurs,
            bascule=bascule,
        )
        self._bascule = bascule
        self._declencheurs = declencheurs
        self._canary = canary
        self._etats[ETAPE_CANARY] = StatutEtape.LANCEE
        self._journaliser(
            ETAPE_CANARY,
            tenant_id,
            application.id,
            approuve_par,
            "lance",
            seuils=declencheurs.vers_paires(),
            mesures=(("palier_pourcentage", float(bascule.palier_actuel_pourcentage)),),
        )
        return canary

    def surveiller_canary(self, mesure: MesureEnLigne) -> BasculeProgressive:
        """Juge la mesure du canary : un déclencheur firing retire la règle.

        Le retour arrière est **automatique** : il ne demande aucune approbation
        (EF-BAS-03). L'annulation est motivée et journalisée.
        """
        bascule = self._bascule_en_cours()
        declencheurs = self._declencheurs_en_cours()
        motif = declencheurs.evaluer(mesure)
        if motif is not None:
            annulee = self.controleur.annuler(bascule.tenant_id, bascule, motif, automatique=True)
            self._bascule = annulee
            self._etats[ETAPE_CANARY] = StatutEtape.ANNULEE
            self._journaliser(
                ETAPE_CANARY,
                bascule.tenant_id,
                bascule.application_id,
                "systeme",
                "annule",
                seuils=declencheurs.vers_paires(),
                mesures=self._mesures_de(mesure),
                motif=motif,
            )
            return annulee
        self._etats[ETAPE_CANARY] = StatutEtape.CONCLUANTE
        self._journaliser(
            ETAPE_CANARY,
            bascule.tenant_id,
            bascule.application_id,
            "systeme",
            "concluante",
            mesures=self._mesures_de(mesure),
        )
        return bascule

    # --- Étape 3 : les paliers --------------------------------------------

    def avancer_paliers(
        self,
        *,
        approuve_par: str | None,
        mesure: MesureEnLigne | None = None,
    ) -> BasculeProgressive:
        """Passe au palier suivant (10 %, 50 %, 100 %) après approbation.

        La logique des paliers et du rollback reste celle du contrôleur de
        bascule (§8.6) : rien n'est réécrit ici.
        """
        self._exiger_etape_concluante(
            ETAPE_PALIERS,
            ETAPE_CANARY,
            "Les paliers exigent un canary concluant : le retour arrière "
            "pré-engagé doit avoir été vérifié.",
        )
        self._exiger_approbation(ETAPE_PALIERS, approuve_par)
        bascule = self._bascule_en_cours()
        poussee = self.controleur.avancer(bascule.tenant_id, bascule, mesure)
        self._bascule = poussee
        annulee = poussee.annule_automatiquement
        self._journaliser(
            ETAPE_PALIERS,
            poussee.tenant_id,
            poussee.application_id,
            approuve_par,
            "annule" if annulee else "avance",
            mesures=(
                ("palier_pourcentage", float(poussee.palier_actuel_pourcentage)),
                ("taux_equivalence", poussee.qualite_en_ligne_mesuree),
            ),
            motif=poussee.motif_annulation or "",
        )
        return poussee

    # --- Garde-fous --------------------------------------------------------

    def _exiger_approbation(self, etape: str, approuve_par: str | None) -> None:
        """Refuse une étape sans décision humaine nommée (ENF-IA-02)."""
        if not approuve_par:
            raise ApprobationRequiseError(
                f"Étape « {etape} » refusée : aucune approbation humaine (ENF-IA-02).",
                etape=etape,
            )

    def _exiger_etape_concluante(self, etape: str, precedente: str, exigence: str) -> None:
        """Refuse une étape dont la précédente n'a pas été concluante (§8.6)."""
        statut = self._etats[precedente]
        if statut is not StatutEtape.CONCLUANTE:
            raise EtapePrealableManquante(
                exigence,
                etape=etape,
                etape_precedente=precedente,
                statut=statut.value,
            )

    def _controler_tenant(
        self, tenant_id: str, application: Application, recommandation: Recommandation
    ) -> None:
        """Refuse toute donnée d'un autre client (ENF-SEC-02)."""
        if application.tenant_id != tenant_id or recommandation.tenantId != tenant_id:
            raise CloisonnementError(
                "Application ou recommandation hors du client demandé.",
                tenant_id=tenant_id,
                application_id=application.id,
            )

    def _bascule_en_cours(self) -> BasculeProgressive:
        """Bascule courante, ou refus si le canary n'a pas été lancé."""
        bascule = self._bascule
        if bascule is None:
            raise EtatIncompatibleError(
                "Aucune bascule en cours : lancez le canary d'abord.",
                etape=ETAPE_CANARY,
            )
        return bascule

    def _declencheurs_en_cours(self) -> DeclencheursCanary:
        """Déclencheurs pré-engagés du canary courant."""
        declencheurs = self._declencheurs
        if declencheurs is None:
            raise EtatIncompatibleError(
                "Aucun déclencheur pré-engagé : lancez le canary d'abord.",
                etape=ETAPE_CANARY,
            )
        return declencheurs

    def _mesures_de(self, mesure: MesureEnLigne) -> tuple[tuple[str, float], ...]:
        """Mesures d'une mesure en ligne, pour le journal."""
        return (
            ("taux_equivalence", mesure.taux_equivalence),
            ("intervalle_bas", mesure.intervalle_bas),
            ("taux_erreur", mesure.taux_erreur),
        )

    def _journaliser(
        self,
        etape: str,
        tenant_id: str,
        application_id: str,
        qui: str | None,
        decision: str,
        seuils: tuple[tuple[str, float], ...] = (),
        mesures: tuple[tuple[str, float], ...] = (),
        motif: str = "",
    ) -> JournalEtape:
        """Consigne une entrée de journal (qui, quand, seuils, mesures, décision)."""
        entree = JournalEtape(
            etape=etape,
            tenant_id=tenant_id,
            application_id=application_id,
            qui=qui or "inconnu",
            quand=maintenant(),
            decision=decision,
            seuils=seuils,
            mesures=mesures,
            motif=motif,
        )
        return self.journal.consigner(entree)

    # --- Lecture -----------------------------------------------------------

    def etats(self) -> dict[str, StatutEtape]:
        """État de chaque étape de la séquence."""
        return dict(self._etats)

    def bilan_shadow(self) -> BilanShadow | None:
        """Bilan du dernier shadow, s'il y en a eu un."""
        return self._bilan

    def canary(self) -> Canary | None:
        """État du canary, s'il a été lancé."""
        return self._canary
