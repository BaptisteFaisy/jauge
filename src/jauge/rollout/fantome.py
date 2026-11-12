"""Trafic fantôme puis canari avant la bascule (piste n° 7, EF-BAS-01..03, §8.6).

Aujourd'hui, la preuve est de laboratoire (rejeu) et la bascule est directe :
10 % → 50 % → 100 %. L'objection « votre rejeu ne reproduit pas la production »
(§21) reste entière. Ce module comble l'écart avec deux phases qui précèdent
les paliers existants :

1. **l'ombre** — le candidat reçoit une **copie** du trafic réel et y répond, mais
   **rien de ce qu'il produit n'est servi** : le client continue de recevoir les
   réponses de la production. On compare les deux, sur les mêmes requêtes ;
2. **le canari** — une petite part du trafic réel (5 % par défaut) est servie par
   le candidat, avec mesure et seuil de retour arrière.

Les deux phases vivent **avant** la machine à paliers existante
(`jauge.rollout.controller.ControlleurBascule`), qui reste propriétaire des
paliers 10/50/100 et du rollback automatique. La remise en main est explicite :
`ControleurFantome.passer_canari` rend la règle de canari, et c'est ensuite au
contrôleur de bascule de reprendre la main.

Trois invariants, tenus par le code et vérifiés par les tests :

- **une réponse fantôme n'est jamais servie** : la règle d'ombre porte
  `pourcentage_servi_cible = 0`, le candidat ne reçoit qu'une copie ;
- **aucune bascule sans approbation humaine** (ENF-IA-02) : le canari exige un
  approbateur, comme la bascule ;
- **le kill-switch du client arrête le trafic fantôme** : la garde est appelée
  avant toute copie, et l'ombre ne survit pas à un client qui a coupé.

Aucun texte de requête ni de réponse n'est conservé : seules des mesures
agrégées (taux, effectifs, durées) franchissent cette frontière (ENF-CONF-01),
et aucun chiffre n'est produit par un modèle (ENF-IA-01).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from jauge.common.errors import EtatIncompatibleError, ValidationError
from jauge.models.recommendation import Recommandation, StatutRecommandation
from jauge.models.tenant import Application
from jauge.rollout.controller import QualiteEnLigne
from jauge.rollout.devis import EstimationShadow, verifier_plafond
from jauge.rollout.gateway import PasserelleClient, ReglePasserelle
from jauge.rollout.journal import JournalEtape, JournalEtapes, maintenant

#: Part du trafic réel servie par le candidat pendant le canari (points de %).
POURCENTAGE_CANARI_DEFAUT = 5

#: Durée minimale d'observation, en heures, avant de parler de canari.
DUREE_MINIMUM_HEURES_DEFAUT = 24.0

#: Volume minimal de requêtes observées en ombre avant de parler de canari.
VOLUME_MINIMUM_REQUETES_DEFAUT = 200

#: Palier mesuré en phase d'ombre : aucun trafic réel n'est servi au candidat.
PALIER_OMBRE = 0


class PhaseBascule(StrEnum):
    """Les phases d'une bascule, dans l'ordre où elles se succèdent."""

    OMBRE = "ombre"
    CANARI = "canari"
    PALIERS = "paliers"
    ANNULEE = "annulee"


@dataclass(frozen=True)
class RegleOmbre:
    """Règle qui sert **toujours** la production et copie le trafic au candidat.

    `pourcentage_servi_cible` vaut 0 par construction : le candidat ne sert
    jamais la réponse. C'est l'invariant central de la phase fantôme.
    """

    id: str
    passerelle_id: str
    modele_demande: str
    modele_cible: str
    pourcentage_servi_cible: int = 0
    description: str = ""


@dataclass(frozen=True)
class ReglesOmbre:
    """Seuils explicites du passage ombre → canari (aucun nombre magique)."""

    volume_minimum_requetes: int = VOLUME_MINIMUM_REQUETES_DEFAUT
    duree_minimum_heures: float = DUREE_MINIMUM_HEURES_DEFAUT
    taux_equivalence_minimum: float = 0.95
    intervalle_bas_minimum: float = 0.90
    taux_erreur_maximum: float = 0.02
    pourcentage_canari: int = POURCENTAGE_CANARI_DEFAUT

    def __post_init__(self) -> None:
        """Refuse des seuils qui ne seraient pas des seuils."""
        if self.volume_minimum_requetes < 1:
            raise ValidationError(
                "ombre : volume minimum hors bornes", volume=self.volume_minimum_requetes
            )
        if not 0.0 <= self.taux_equivalence_minimum <= 1.0:
            raise ValidationError(
                "ombre : taux d'équivalence hors bornes", valeur=self.taux_equivalence_minimum
            )
        if not 0.0 <= self.taux_erreur_maximum <= 1.0:
            raise ValidationError(
                "ombre : taux d'erreur hors bornes", valeur=self.taux_erreur_maximum
            )
        if not 1 <= self.pourcentage_canari <= 100:
            raise ValidationError(
                "ombre : pourcentage de canari hors bornes", valeur=self.pourcentage_canari
            )


@dataclass(frozen=True)
class ObservationOmbre:
    """Ce qu'un échantillon de trafic fantôme a montré (agrégats, jamais de texte)."""

    nombre_requetes: int
    duree_heures: float
    taux_equivalence: float
    intervalle_bas: float
    taux_erreur: float


@dataclass(frozen=True)
class DecisionOmbre:
    """Passage au canari : autorisé ou non, avec les critères qui l'ont décidé."""

    autorise: bool
    criteres: Mapping[str, bool] = field(default_factory=dict)
    motifs: tuple[str, ...] = ()

    def resume(self) -> str:
        """Motifs joints, pour le rapport et l'audit (aucun texte de requête)."""
        return " ; ".join(self.motifs)


@dataclass
class EtatOmbre:
    """État d'une bascule en phase fantôme (volatile, comme `SuiviBascule`)."""

    tenant_id: str
    application_id: str
    modele_cible: str
    regle_ombre: RegleOmbre
    phase: PhaseBascule = PhaseBascule.OMBRE
    debut_heures: float = 0.0
    observations: list[ObservationOmbre] = field(default_factory=list)
    decision: DecisionOmbre | None = None
    regle_canari: ReglePasserelle | None = None


def decider_passage_canari(observation: ObservationOmbre, regles: ReglesOmbre) -> DecisionOmbre:
    """Décide, à partir de critères explicites, si l'ombre peut devenir canari."""
    criteres = {
        "volume_suffisant": observation.nombre_requetes >= regles.volume_minimum_requetes,
        "duree_suffisante": observation.duree_heures >= regles.duree_minimum_heures,
        "equivalence_suffisante": observation.taux_equivalence >= regles.taux_equivalence_minimum,
        "intervalle_suffisant": observation.intervalle_bas >= regles.intervalle_bas_minimum,
        "erreurs_maitrisees": observation.taux_erreur <= regles.taux_erreur_maximum,
    }
    motifs = tuple(
        f"critère non satisfait : {nom}" for nom, rempli in criteres.items() if not rempli
    )
    return DecisionOmbre(autorise=all(criteres.values()), criteres=dict(criteres), motifs=motifs)


class PasserelleFantome(PasserelleClient, Protocol):
    """Passerelle de bascule qui sait **copier** le trafic vers le candidat.

    Tout ce qui sert la bascule palier par paliers reste disponible (protocole
    `PasserelleClient`) ; s'y ajoute la copie « fantôme », dont la règle sert
    0 % du candidat.
    """

    def copier_trafic_vers(
        self,
        passerelle_id: str,
        modele_demande: str,
        modele_cible: str,
        description: str = "",
    ) -> RegleOmbre: ...


class ControleurFantome:
    """Conduit la phase fantôme, puis le canari, avant les paliers existants.

    Le contrôleur ne décide rien seul : `demarrer_ombre` exige le nom du
    responsable qui a approuvé (ENF-IA-02), et `passer_canari` n'ouvre la phase
    suivante que si les critères de `ReglesOmbre` sont tous satisfaits.
    """

    def __init__(
        self,
        passerelle: PasserelleFantome,
        qualite: QualiteEnLigne,
        regles: ReglesOmbre | None = None,
        horloge: Callable[[], float] | None = None,
        journal: JournalEtapes | None = None,
    ) -> None:
        self.passerelle = passerelle
        self.qualite = qualite
        self.regles = regles or ReglesOmbre()
        self._horloge = horloge or (lambda: time.time() / 3600.0)
        self.journal = journal or JournalEtapes()
        self._etats: dict[str, EtatOmbre] = {}

    # --- Phase fantôme -------------------------------------------------------

    def demarrer_ombre(
        self,
        tenant_id: str,
        application: Application,
        recommandation: Recommandation,
        approuve_par: str | None,
        garde: Callable[[], None] | None = None,
        devis: EstimationShadow | None = None,
        plafond_usd: float | None = None,
    ) -> EtatOmbre:
        """Copie le trafic vers le candidat, sans rien lui servir.

        `garde` est appelée avant toute copie : c'est là que le kill-switch du
        client s'applique (un client qui a coupé ne voit pas son trafic copié).
        """
        self._controler_entree(application, recommandation, approuve_par, garde, devis, plafond_usd)
        passerelle_id = application.identifiant_passerelle or f"gw-{application.id}"
        regle = self.passerelle.copier_trafic_vers(
            passerelle_id=passerelle_id,
            modele_demande=application.modele_actuel,
            modele_cible=recommandation.targetModel,
            description=f"Jauge : observation fantôme de {application.nom}",
        )
        etat = EtatOmbre(
            tenant_id=tenant_id,
            application_id=application.id,
            modele_cible=recommandation.targetModel,
            regle_ombre=regle,
            debut_heures=self._horloge(),
        )
        self._etats[self._cle(tenant_id, application.id)] = etat
        self._consigner(
            etat,
            etape="ombre",
            decision="ombre_demarree",
            qui=approuve_par,
            seuils=(
                ("volume_minimum_requetes", float(self.regles.volume_minimum_requetes)),
                ("duree_minimum_heures", self.regles.duree_minimum_heures),
            ),
            mesures=((("cout_total_usd", devis.cout_total_usd),) if devis is not None else ()),
        )
        return etat

    def _controler_entree(
        self,
        application: Application,
        recommandation: Recommandation,
        approuve_par: str | None,
        garde: Callable[[], None] | None,
        devis: EstimationShadow | None,
        plafond_usd: float | None,
    ) -> None:
        """Vérifie les quatre préconditions de la phase fantôme, dans l'ordre.

        Approbation humaine (ENF-IA-02), recommandation approuvée, devis sous le
        plafond du client (ENF-COUT-01), et garde du kill-switch : aucun trafic
        n'est copié tant qu'une seule de ces conditions n'est pas remplie.
        """
        if not approuve_par:
            raise EtatIncompatibleError(
                "Le trafic fantôme exige une approbation humaine.", application_id=application.id
            )
        if recommandation.status != StatutRecommandation.APPROUVEE:
            raise EtatIncompatibleError(
                "La recommandation n'est pas approuvée : pas de trafic fantôme.",
                recommandation_id=recommandation.id,
                status=recommandation.status.value,
            )
        if devis is not None and plafond_usd is not None:
            verifier_plafond(devis, plafond_usd)
        if garde is not None:
            garde()

    def mesurer_ombre(self, tenant_id: str, application_id: str) -> ObservationOmbre:
        """Mesure le trafic fantôme : agrégats uniquement, jamais de texte."""
        etat = self._etat(tenant_id, application_id)
        mesure = self.qualite.mesurer(tenant_id, application_id, PALIER_OMBRE)
        observation = ObservationOmbre(
            nombre_requetes=mesure.nombre_requetes,
            duree_heures=round(self._horloge() - etat.debut_heures, 4),
            taux_equivalence=mesure.taux_equivalence,
            intervalle_bas=mesure.intervalle_bas,
            taux_erreur=mesure.taux_erreur,
        )
        etat.observations.append(observation)
        return observation

    # --- Passage au canari ----------------------------------------------------

    def passer_canari(self, etat: EtatOmbre, observation: ObservationOmbre) -> DecisionOmbre:
        """Passe au canari si les critères le permettent ; sinon annule l'ombre."""
        decision = decider_passage_canari(observation, self.regles)
        etat.decision = decision
        if not decision.autorise:
            self.annuler(etat, decision.resume())
            self._consigner(
                etat,
                etape="ombre",
                decision="canari_refuse",
                qui="controleur",
                motif=decision.resume(),
                mesures=(("nombre_requetes", float(observation.nombre_requetes)),),
            )
            return decision
        passerelle_id = etat.regle_ombre.passerelle_id
        regle = self.passerelle.creer_regle_ponderee(
            passerelle_id=passerelle_id,
            modele_demande=etat.regle_ombre.modele_demande,
            modele_cible=etat.modele_cible,
            pourcentage_cible=self.regles.pourcentage_canari,
            description=f"Jauge : canari {self.regles.pourcentage_canari} % avant bascule",
        )
        self.passerelle.supprimer_regle(etat.regle_ombre.id)
        etat.regle_canari = regle
        etat.phase = PhaseBascule.CANARI
        self._consigner(
            etat,
            etape="canari",
            decision="canari_ouvert",
            qui="controleur",
            seuils=(("pourcentage_canari", float(self.regles.pourcentage_canari)),),
            mesures=(("nombre_requetes", float(observation.nombre_requetes)),),
        )
        return decision

    def annuler(self, etat: EtatOmbre, motif: str) -> None:
        """Retire la règle d'ombre : le trafic n'est plus copié, rien n'est servi."""
        self.passerelle.supprimer_regle(etat.regle_ombre.id)
        etat.phase = PhaseBascule.ANNULEE
        self._consigner(
            etat, etape="ombre", decision="ombre_annulee", qui="controleur", motif=motif
        )

    def _consigner(
        self,
        etat: EtatOmbre,
        etape: str,
        decision: str,
        qui: str,
        motif: str = "",
        seuils: tuple[tuple[str, float], ...] = (),
        mesures: tuple[tuple[str, float], ...] = (),
    ) -> None:
        """Trace une étape de la bascule (EF-ADM-02) : qui, quand, quelle décision."""
        self.journal.consigner(
            JournalEtape(
                etape=etape,
                tenant_id=etat.tenant_id,
                application_id=etat.application_id,
                qui=qui,
                quand=maintenant(),
                decision=decision,
                seuils=seuils,
                mesures=mesures,
                motif=motif,
            )
        )

    def etat(self, tenant_id: str, application_id: str) -> EtatOmbre:
        """État de la phase fantôme d'une application."""
        return self._etat(tenant_id, application_id)

    def _etat(self, tenant_id: str, application_id: str) -> EtatOmbre:
        etat = self._etats.get(self._cle(tenant_id, application_id))
        if etat is None:
            raise EtatIncompatibleError(
                "Aucune phase fantôme en cours pour cette application.",
                application_id=application_id,
            )
        return etat

    @staticmethod
    def _cle(tenant_id: str, application_id: str) -> str:
        return f"{tenant_id}/{application_id}"


class FakePasserelleFantome:
    """Passerelle fictive : règle d'ombre, copie du trafic et canari.

    Elle reproduit le comportement observable de la passerelle sans aucun appel
    réseau, et journalise séparément ce qui est **servi** et ce qui est
    **copié** : c'est ce qui permet de vérifier qu'une réponse fantôme n'est
    jamais servie.
    """

    def __init__(self) -> None:
        self.regles: dict[str, ReglePasserelle] = {}
        self.regles_ombre: dict[str, RegleOmbre] = {}
        self.servis: list[str] = []
        self.copies: list[tuple[str, str]] = []
        self._compteur = 0

    def _nouvel_id(self, prefixe: str = "ombre") -> str:
        self._compteur += 1
        return f"{prefixe}-{self._compteur:03d}"

    def copier_trafic_vers(
        self,
        passerelle_id: str,
        modele_demande: str,
        modele_cible: str,
        description: str = "",
    ) -> RegleOmbre:
        """Crée la règle d'ombre : le candidat reçoit une copie, il ne sert rien."""
        regle = RegleOmbre(
            id=self._nouvel_id(),
            passerelle_id=passerelle_id,
            modele_demande=modele_demande,
            modele_cible=modele_cible,
            description=description,
        )
        self.regles_ombre[regle.id] = regle
        return regle

    def creer_regle_ponderee(
        self,
        passerelle_id: str,
        modele_demande: str,
        modele_cible: str,
        pourcentage_cible: int,
        description: str = "",
    ) -> ReglePasserelle:
        """Règle de canari : une part du trafic réel est servie par le candidat."""
        regle = ReglePasserelle(
            id=self._nouvel_id("regle"),
            passerelle_id=passerelle_id,
            modele_demande=modele_demande,
            pourcentage_cible=pourcentage_cible,
            modele_cible=modele_cible,
            description=description,
        )
        self.regles[regle.id] = regle
        return regle

    def creer_regle_statique(
        self,
        passerelle_id: str,
        modele_demande: str,
        modele_cible: str,
        description: str = "",
    ) -> ReglePasserelle:
        """Règle statique : tout le trafic va au cible (hors périmètre de l'ombre)."""
        regle = ReglePasserelle(
            id=self._nouvel_id("regle"),
            passerelle_id=passerelle_id,
            modele_demande=modele_demande,
            pourcentage_cible=100,
            modele_cible=modele_cible,
            description=description,
        )
        self.regles[regle.id] = regle
        return regle

    def mettre_a_jour_poids(self, regle_id: str, pourcentage_cible: int) -> ReglePasserelle:
        """Change le poids d'une règle de canari."""
        regle = self.regles[regle_id]
        Mise_a_jour = replace(regle, pourcentage_cible=pourcentage_cible)
        self.regles[regle_id] = Mise_a_jour
        return Mise_a_jour

    def supprimer_regle(self, regle_id: str) -> None:
        """Retire une règle, quelle qu'elle soit (ombre ou canari)."""
        self.regles.pop(regle_id, None)
        self.regles_ombre.pop(regle_id, None)

    def lire_regle(self, regle_id: str) -> ReglePasserelle | None:
        """Règle de canari connue de la passerelle."""
        return self.regles.get(regle_id)

    def lister_regles(self, passerelle_id: str) -> list[ReglePasserelle]:
        """Règles de canari d'une passerelle."""
        return [r for r in self.regles.values() if r.passerelle_id == passerelle_id]

    def servir(self, modele: str) -> str:
        """Simule un appel servi au client : seul le modèle servi est journalisé."""
        self.servis.append(modele)
        return "réponse servie"

    def copier(self, modele_serve: str, modele_ombre: str) -> str:
        """Simule la copie du trafic : la réponse d'ombre n'est pas servie."""
        self.copies.append((modele_serve, modele_ombre))
        return "réponse d'ombre (non servie)"
