"""Trafic fantôme (shadow) : le candidat répond en parallèle, rien n'est renvoyé.

Piste n° 7 du top 10 : combler l'écart entre la preuve de laboratoire (le rejeu)
et la production — le risque « le rejeu ne reproduit pas la production » (§21).

Le candidat répond **en parallèle** du modèle en place, sur un échantillon borné
du trafic réel ; sa réponse n'est **jamais** renvoyée à l'utilisateur. Ce module :

- borne l'échantillon (taille maximale et part du trafic observé) et **chiffre
  son coût avant le lancement** ; au-delà du plafond, `PlafondDepasseError` est
  levée **avant tout appel de modèle** (ENF-COUT-01) ;
- mesure le coût et la latence que le shadow **lui-même** induit : le shadow a un
  prix, il doit être visible ;
- compare le taux d'équivalence observé sur le trafic réel à celui mesuré par la
  campagne de rejeu et signale la divergence au-delà du seuil fixé : c'est un
  résultat de premier ordre, pas une note.

ENF-CONF-01 : les paires (réponse réelle, réponse candidate) sont collectées dans
le **plan de données** (`DepotPaires`). `BilanShadow` est le seul objet qui sort
vers le plan de contrôle : aucun texte de requête ni de réponse n'y figure.

ENF-IA-01 : tous les nombres viennent du code — tarifs (`engine.calculator`),
intervalle de Wilson et percentiles (`engine.stats`) — jamais d'un modèle.

Les ports locaux (client de modèle, juge, dépôt de paires) sont déclarés dans
`jauge.rollout.ports`, comme le veut la règle du dépôt : un module code contre
le socle, pas contre l'implémentation d'un autre module. Le devis du shadow
(bornes, coût, plafond) est dans `jauge.rollout.devis`.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date

from jauge.common.errors import CloisonnementError, ValidationError
from jauge.common.ids import aleatoire, graine_de
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.engine.stats import intervalle_wilson, percentile
from jauge.models.campaign import PaireRejoueeDataPlane, ParametresRejeu, ResultatCandidat
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.rollout.devis import (
    NIVEAU_JUGE,
    NIVEAU_SHADOW,
    BornesEchantillon,
    EstimationShadow,
    estimer_cout_shadow,
    verifier_plafond,
)
from jauge.rollout.ports import (
    ClientModele,
    DemandeShadow,
    DepotPaires,
    JugeEquivalence,
    PaireShadow,
    ReponseModele,
    VerdictEquivalence,
)

# Graine par défaut du tirage : celle des campagnes de rejeu (`Campagne`).
GRAINE_DEFAUT = 42


@dataclass(frozen=True)
class ParametresShadow:
    """Ce qui est écrit **avant** le lancement d'un shadow.

    Bornes, plafond et seuils sont fournis ici, figés : ils ne sont jamais
    déduits après coup, et ils sont journalisés au lancement.
    """

    modele_actuel: str
    modele_candidat: str
    bornes: BornesEchantillon
    plafond_usd: float
    seuil_qualite: float
    seuil_ecart_labo_production: float
    jetons_entree_juge: int = 1200
    jetons_sortie_juge: int = 400
    graine: int = GRAINE_DEFAUT

    def __post_init__(self) -> None:
        for nom in ("jetons_entree_juge", "jetons_sortie_juge"):
            if getattr(self, nom) < 0:
                raise ValidationError(
                    "Une hypothèse de jetons du juge ne peut pas être négative.", champ=nom
                )


@dataclass(frozen=True)
class ObservationReelle:
    """Une requête du trafic réel et la réponse que l'utilisateur a reçue.

    Objet du **plan de données** : il porte les textes. Il ne sort jamais vers
    le plan de contrôle — seul `BilanShadow` en sort (ENF-CONF-01).
    """

    tenant_id: str
    application_id: str
    requete_id: str
    texte_requete: str
    reponse_reelle: str
    jetons_entree: int = 0
    jetons_sortie: int = 0
    latence_ms: int = 0
    type_requete: str = "general"
    parametres: ParametresRejeu = field(default_factory=ParametresRejeu)
    consigne_systeme: str = ""


@dataclass(frozen=True)
class PlanShadow:
    """Échantillon retenu et devis validé : le shadow peut être lancé.

    Porte les observations (donc des textes) : c'est un objet du plan de
    données. `BilanShadow` est le seul objet qui en sort.
    """

    run_id: str
    tenant_id: str
    application_id: str
    parametres: ParametresShadow
    observations: tuple[ObservationReelle, ...]
    volume_observe: int
    estimation: EstimationShadow

    @property
    def requete_ids(self) -> tuple[str, ...]:
        """Identifiants des requêtes retenues (aucun texte)."""
        return tuple(observation.requete_id for observation in self.observations)


@dataclass(frozen=True)
class BilanShadow:
    """Bilan agrégé du shadow : le seul objet qui quitte le plan de données.

    Aucun champ ne contient de texte de requête ni de réponse (ENF-CONF-01) :
    uniquement des identifiants, des comptes, des taux et des montants calculés
    par le code (ENF-IA-01).
    """

    run_id: str
    tenant_id: str
    application_id: str
    modele_actuel: str
    modele_candidat: str
    volume_observe: int
    nombre_requetes_tirees: int
    nombre_paires: int
    erreurs_candidat: int
    taux_equivalence: float
    intervalle_bas: float
    seuil_qualite: float
    taux_equivalence_rejeu: float | None
    ecart_labo_production: float | None
    seuil_ecart_labo_production: float
    divergence: bool
    concluant: bool
    cout_candidat_usd: float
    cout_judge_usd: float
    cout_shadow_usd: float
    cout_estime_usd: float
    plafond_usd: float
    jetons_entree_shadow: int
    jetons_sortie_shadow: int
    latence_reelle_p50_ms: int
    latence_shadow_p50_ms: int
    latence_shadow_p95_ms: int
    surcout_latence_p50_ms: int

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable pour le plan de contrôle, l'audit et le proof pack.

        Le bilan ne porte que des scalaires : la conversion ne peut rien faire
        sortir du plan de données (ENF-CONF-01).
        """
        return asdict(self)


@dataclass
class _Cumul:
    """Cumul interne des mesures du shadow (coût, jetons, latences)."""

    cout_candidat_usd: float = 0.0
    jetons_entree: int = 0
    jetons_sortie: int = 0
    latences_reelles: list[int] = field(default_factory=list)
    latences_shadow: list[int] = field(default_factory=list)
    surcouts_latence: list[int] = field(default_factory=list)
    erreurs: int = 0

    def ajouter(self, reponse: ReponseModele, cout_usd: float, latence_reelle_ms: int) -> None:
        """Ajoute une invocation du candidat au cumul."""
        self.cout_candidat_usd += cout_usd
        self.jetons_entree += reponse.jetons_entree
        self.jetons_sortie += reponse.jetons_sortie
        self.latences_shadow.append(reponse.latence_ms)
        self.surcouts_latence.append(max(0, reponse.latence_ms - latence_reelle_ms))


class MoteurShadow:
    """Mène un shadow : échantillon borné, devis plafonné, paires au plan de données.

    Le client de modèle et le juge sont injectés ; les adaptateurs réels
    (`BedrockModeleClient`, `BedrockJugeClient`) ne sont jamais appelés par les
    tests, qui utilisent les doubles hors ligne.
    """

    def __init__(
        self,
        *,
        client: ClientModele,
        juge: JugeEquivalence,
        tarifs: TableTarifs,
        depot: DepotPaires,
        region: str,
        a_la_date: date,
        niveau: ServiceTier = NIVEAU_SHADOW,
    ) -> None:
        self.client = client
        self.juge = juge
        self.tarifs = tarifs
        self.depot = depot
        self.region = region
        self.a_la_date = a_la_date
        self.niveau = niveau

    def preparer(
        self,
        *,
        tenant_id: str,
        application_id: str,
        observations: Sequence[ObservationReelle],
        parametres: ParametresShadow,
    ) -> PlanShadow:
        """Échantillonne le trafic, chiffre le shadow et vérifie le plafond.

        Aucun appel de modèle n'est fait ici : le devis est la porte d'entrée
        (ENF-COUT-01). Au-delà du plafond, `PlafondDepasseError` est levée
        avant tout dollar dépensé.
        """
        retenues = self._observations_du_client(tenant_id, application_id, observations)
        if not retenues:
            raise ValidationError(
                "Aucun trafic réel observé pour cette application.",
                tenant_id=tenant_id,
                application_id=application_id,
            )
        taille = parametres.bornes.taille_pour(len(retenues))
        tirees = self._tirer(retenues, taille, parametres.graine, tenant_id, application_id)
        entree, sortie = self._jetons_moyens(tirees)
        estimation = estimer_cout_shadow(
            tarifs=self.tarifs,
            region=self.region,
            a_la_date=self.a_la_date,
            modele_candidat=parametres.modele_candidat,
            modele_judge=self.juge.modele_id,
            nombre_requetes=len(tirees),
            jetons_entree_moyens=entree,
            jetons_sortie_moyens=sortie,
            jetons_entree_juge=parametres.jetons_entree_juge,
            jetons_sortie_juge=parametres.jetons_sortie_juge,
            plafond_usd=parametres.plafond_usd,
            niveau=self.niveau,
        )
        verifier_plafond(estimation, parametres.plafond_usd)
        return PlanShadow(
            run_id=aleatoire("rollout"),
            tenant_id=tenant_id,
            application_id=application_id,
            parametres=parametres,
            observations=tuple(tirees),
            volume_observe=len(retenues),
            estimation=estimation,
        )

    def _observations_du_client(
        self, tenant_id: str, application_id: str, observations: Sequence[ObservationReelle]
    ) -> list[ObservationReelle]:
        """Écarte les observations d'une autre application, refuse un autre client."""
        retenues: list[ObservationReelle] = []
        for observation in observations:
            if observation.tenant_id != tenant_id:
                raise CloisonnementError(
                    "Trafic réel d'un autre client dans le shadow (ENF-SEC-02).",
                    tenant_id=tenant_id,
                    application_id=application_id,
                    requete_id=observation.requete_id,
                )
            if observation.application_id == application_id:
                retenues.append(observation)
        return retenues

    def _tirer(
        self,
        observations: Sequence[ObservationReelle],
        taille: int,
        graine: int,
        tenant_id: str,
        application_id: str,
    ) -> list[ObservationReelle]:
        """Tire un échantillon reproductible (même graine ⇒ même échantillon)."""
        tries = sorted(observations, key=lambda observation: observation.requete_id)
        generateur = random.Random(graine_de(graine, tenant_id, application_id))
        return generateur.sample(tries, min(taille, len(tries)))

    def _jetons_moyens(self, observations: Sequence[ObservationReelle]) -> tuple[float, float]:
        """Jetons d'entrée et de sortie moyens du trafic observé (base du devis)."""
        if not observations:
            return (0.0, 0.0)
        nombre = len(observations)
        entree = sum(observation.jetons_entree for observation in observations) / nombre
        sortie = sum(observation.jetons_sortie for observation in observations) / nombre
        return (round(entree, 3), round(sortie, 3))

    def executer(
        self,
        plan: PlanShadow,
        resultat_rejeu: ResultatCandidat | None = None,
    ) -> BilanShadow:
        """Appelle le candidat, juge les paires et rend le bilan agrégé.

        `resultat_rejeu` est le résultat de la campagne de rejeu pour le même
        candidat : c'est la référence de comparaison labo ↔ production.
        """
        cumul = _Cumul()
        paires: list[PaireRejoueeDataPlane] = []
        a_juger: list[PaireShadow] = []
        for observation in plan.observations:
            self._jouer(observation, plan, cumul, paires, a_juger)
        verdicts = list(self.juge.noter_lot(a_juger)) if a_juger else []
        self.depot.enregistrer_paires(plan.run_id, paires)
        return self._bilan(plan, cumul, verdicts, resultat_rejeu)

    def _jouer(
        self,
        observation: ObservationReelle,
        plan: PlanShadow,
        cumul: _Cumul,
        paires: list[PaireRejoueeDataPlane],
        a_juger: list[PaireShadow],
    ) -> None:
        """Appelle le candidat sur une requête réelle, mesure et prépare la paire."""
        cumul.latences_reelles.append(observation.latence_ms)
        demande = DemandeShadow(
            requete_id=observation.requete_id,
            modele_id=plan.parametres.modele_candidat,
            texte_requete=observation.texte_requete,
            parametres=observation.parametres,
            niveau=self.niveau,
            consigne_systeme=observation.consigne_systeme,
        )
        try:
            reponse = self.client.invoquer(demande)
        except Exception:  # panne du candidat : le trafic réel, lui, continue
            cumul.erreurs += 1
            return
        cumul.ajouter(reponse, self._cout(reponse), observation.latence_ms)
        paires.append(self._paire(plan, observation, reponse))
        a_juger.append(
            PaireShadow(
                requete_id=observation.requete_id,
                requete=observation.texte_requete,
                reponse_reference=observation.reponse_reelle,
                reponse_candidate=reponse.texte,
                type_tache=observation.type_requete,
            )
        )

    def _cout(self, reponse: ReponseModele) -> float:
        """Coût d'une réponse du candidat, calculé par le code (Annexe D)."""
        tarif = self.tarifs.trouver(reponse.modele_id, self.region, self.niveau, self.a_la_date)
        metriques = MetriquesRequete(
            jetons_entree=reponse.jetons_entree,
            jetons_sortie=reponse.jetons_sortie,
            jetons_cache_ecriture=reponse.jetons_cache_ecriture,
            jetons_cache_lecture=reponse.jetons_cache_lecture,
            latence_ms=reponse.latence_ms,
        )
        return calculer_cout_requete(tarif, metriques).cout_total

    def _paire(
        self, plan: PlanShadow, observation: ObservationReelle, reponse: ReponseModele
    ) -> PaireRejoueeDataPlane:
        """Paire (réponse réelle, réponse candidate) — plan de données uniquement."""
        return PaireRejoueeDataPlane(
            campagne_id=plan.run_id,
            requete_id=observation.requete_id,
            modele_candidat_id=plan.parametres.modele_candidat,
            texte_requete=observation.texte_requete,
            reponse_reference=observation.reponse_reelle,
            reponse_candidate=reponse.texte,
            score_equivalence=0.0,
            explication_judge="",
        )

    def _bilan(
        self,
        plan: PlanShadow,
        cumul: _Cumul,
        verdicts: Sequence[VerdictEquivalence],
        resultat_rejeu: ResultatCandidat | None,
    ) -> BilanShadow:
        """Agrège le run : taux, intervalle, coût, latence, écart labo ↔ production."""
        parametres = plan.parametres
        intervalle = intervalle_wilson(sum(1 for v in verdicts if v.equivalent), len(verdicts))
        cout_juge = self._cout_judge(len(verdicts), parametres)
        taux_rejeu, ecart, divergence = self._ecart_labo_production(
            intervalle.taux, resultat_rejeu, parametres.seuil_ecart_labo_production
        )
        return BilanShadow(
            run_id=plan.run_id,
            tenant_id=plan.tenant_id,
            application_id=plan.application_id,
            modele_actuel=parametres.modele_actuel,
            modele_candidat=parametres.modele_candidat,
            volume_observe=plan.volume_observe,
            nombre_requetes_tirees=len(plan.observations),
            nombre_paires=len(verdicts),
            erreurs_candidat=cumul.erreurs,
            taux_equivalence=intervalle.taux,
            intervalle_bas=intervalle.bas,
            seuil_qualite=parametres.seuil_qualite,
            taux_equivalence_rejeu=taux_rejeu,
            ecart_labo_production=ecart,
            seuil_ecart_labo_production=parametres.seuil_ecart_labo_production,
            divergence=divergence,
            concluant=self._est_concluant(intervalle.taux, len(verdicts), divergence, parametres),
            cout_candidat_usd=round(cumul.cout_candidat_usd, 8),
            cout_judge_usd=cout_juge,
            cout_shadow_usd=round(cumul.cout_candidat_usd + cout_juge, 8),
            cout_estime_usd=plan.estimation.cout_total_usd,
            plafond_usd=plan.estimation.plafond_usd,
            jetons_entree_shadow=cumul.jetons_entree,
            jetons_sortie_shadow=cumul.jetons_sortie,
            latence_reelle_p50_ms=int(round(percentile(cumul.latences_reelles, 50))),
            latence_shadow_p50_ms=int(round(percentile(cumul.latences_shadow, 50))),
            latence_shadow_p95_ms=int(round(percentile(cumul.latences_shadow, 95))),
            surcout_latence_p50_ms=int(round(percentile(cumul.surcouts_latence, 50))),
        )

    def _est_concluant(
        self, taux: float, nombre_paires: int, divergence: bool, parametres: ParametresShadow
    ) -> bool:
        """Un shadow conclut quand il mesure assez, sans divergence, et au seuil."""
        return not divergence and nombre_paires > 0 and taux >= parametres.seuil_qualite

    def _ecart_labo_production(
        self, taux_shadow: float, resultat_rejeu: ResultatCandidat | None, seuil: float
    ) -> tuple[float | None, float | None, bool]:
        """Compare le taux du shadow à celui du rejeu : la divergence est un résultat.

        C'est l'objection « votre rejeu ne reproduit pas la production » (§21) :
        l'écart est calculé, publié, et il bloque l'étape suivante quand il
        dépasse le seuil.
        """
        if resultat_rejeu is None:
            return (None, None, False)
        taux_rejeu = resultat_rejeu.taux_equivalence
        ecart = round(abs(taux_shadow - taux_rejeu), 6)
        return (taux_rejeu, ecart, ecart > seuil)

    def _cout_judge(self, nombre_paires: int, parametres: ParametresShadow) -> float:
        """Coût du jugement : le juge est appelé une fois par paire."""
        if nombre_paires <= 0:
            return 0.0
        tarif = self.tarifs.trouver(self.juge.modele_id, self.region, NIVEAU_JUGE, self.a_la_date)
        unitaire = calculer_cout_requete(
            tarif,
            MetriquesRequete(
                jetons_entree=parametres.jetons_entree_juge,
                jetons_sortie=parametres.jetons_sortie_juge,
            ),
        ).cout_total
        return round(nombre_paires * unitaire, 8)
