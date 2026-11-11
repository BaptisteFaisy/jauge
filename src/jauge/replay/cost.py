"""Estimation du coût d'une campagne de rejeu, avant son lancement (§17.1).

Exigences visées :
- ENF-COUT-01 : le coût d'une campagne est **estimé avant son lancement et
  plafonné** ; une estimation au-dessus du plafond du client est refusée
  (`PlafondDepasseError`) avant le moindre appel de modèle ;
- §17.1 : coût du rejeu = taille d'échantillon × (2 exécutions du modèle actuel
  + 1 par candidat) × coût moyen d'une requête, au **tarif Flex** ; coût de
  l'évaluation = taille × nombre de comparaisons × coût d'un appel au modèle
  juge.

Aucun chiffre n'est produit par un modèle de langage (ENF-IA-01) : tout vient de
`jauge.engine.calculator.calculer_cout_requete` et de la table des tarifs.

Hypothèses explicites (à confirmer avec le client, cf. corps de PR) :
- le nombre de comparaisons jugées vaut `candidats + 1` : chaque candidat est
  comparé à la référence, et la référence est comparée à elle-même pour mesurer
  la variabilité naturelle (§13.4, EF-EVA-04) — l'exemple du §17.1 (200 requêtes,
  3 candidats → 800 jugements) suit exactement cette règle ;
- le modèle juge tourne au niveau Standard (il n'a pas besoin du niveau Flex) ;
- les jetons moyens d'une requête et d'un jugement sont des hypothèses fournies
  par l'appelant, jamais devinées à partir du texte.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from jauge.common.errors import PlafondDepasseError, ValidationError
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.pricing import MetriquesRequete, ServiceTier, TarifModele
from jauge.replay.executor import EXECUTIONS_REFERENCE

# Niveau de service du rejeu (§17.1 : « au tarif Flex »).
NIVEAU_REJEU = ServiceTier.FLEX
# Niveau de service du modèle juge : hypothèse Standard.
NIVEAU_JUGE = ServiceTier.STANDARD
# Une comparaison par candidat, plus la référence comparée à elle-même (§13.4).
COMPARAISONS_PAR_CANDIDAT = 1

# Hypothèses par défaut, en jetons. Elles ne dépendent pas du texte : c'est un
# ordre de grandeur de facturation, à remplacer par les moyennes de l'échantillon
# et par la longueur réelle des invites du juge.
JETONS_SORTIE_MOYENS_DEFAUT = 300
JETONS_ENTREE_JUGE_DEFAUT = 1200
JETONS_SORTIE_JUGE_DEFAUT = 400


@dataclass(frozen=True)
class HypothesesCout:
    """Hypothèses de facturation d'une campagne (jetons moyens, en entrée/sortie)."""

    jetons_entree_moyens: int
    jetons_sortie_moyens: int
    jetons_entree_juge: int = JETONS_ENTREE_JUGE_DEFAUT
    jetons_sortie_juge: int = JETONS_SORTIE_JUGE_DEFAUT

    def __post_init__(self) -> None:
        for nom in ("jetons_entree_moyens", "jetons_sortie_moyens"):
            if getattr(self, nom) < 0:
                raise ValidationError(
                    "Une hypothèse de jetons ne peut pas être négative.", champ=nom
                )


@dataclass(frozen=True)
class EstimationCampagne:
    """Devis d'une campagne, poste par poste (§17.1)."""

    taille_echantillon: int
    nombre_candidats: int
    executions_rejeu: int
    jugements: int
    cout_rejeu_usd: float
    cout_evaluation_usd: float
    cout_total_usd: float
    plafond_usd: float | None = None
    sous_plafond: bool = True

    def marge_usd(self) -> float:
        """Reste disponible sous le plafond (0 si aucun plafond n'est fixé)."""
        if self.plafond_usd is None:
            return 0.0
        return round(max(0.0, self.plafond_usd - self.cout_total_usd), 8)


def _cout_requete(tarif: TarifModele, hypotheses: HypothesesCout) -> float:
    """Coût d'une requête moyenne au tarif donné (Annexe D)."""
    metriques = MetriquesRequete(
        jetons_entree=hypotheses.jetons_entree_moyens,
        jetons_sortie=hypotheses.jetons_sortie_moyens,
    )
    return calculer_cout_requete(tarif, metriques).cout_total


def _cout_jugement(tarif_juge: TarifModele, hypotheses: HypothesesCout) -> float:
    """Coût d'un appel au modèle juge (une comparaison)."""
    metriques = MetriquesRequete(
        jetons_entree=hypotheses.jetons_entree_juge,
        jetons_sortie=hypotheses.jetons_sortie_juge,
    )
    return calculer_cout_requete(tarif_juge, metriques).cout_total


def _tarif(
    tarifs: TableTarifs, modele_id: str, region: str, niveau: ServiceTier, a_la_date: date
) -> TarifModele:
    """Tarif applicable, ou `TarifInconnuError` si la table ne le couvre pas."""
    return tarifs.trouver(modele_id, region, niveau, a_la_date)


def _cout_rejeu(
    tarifs: TableTarifs,
    region: str,
    a_la_date: date,
    modele_actuel: str,
    candidats: Sequence[str],
    taille: int,
    hypotheses: HypothesesCout,
) -> float:
    """Coût du rejeu : 2 exécutions de la référence + 1 par candidat, au Flex."""
    total = (
        EXECUTIONS_REFERENCE
        * taille
        * _cout_requete(_tarif(tarifs, modele_actuel, region, NIVEAU_REJEU, a_la_date), hypotheses)
    )
    for candidat in candidats:
        total += taille * _cout_requete(
            _tarif(tarifs, candidat, region, NIVEAU_REJEU, a_la_date), hypotheses
        )
    return total


def estimer_cout_campagne(
    *,
    tarifs: TableTarifs,
    region: str,
    a_la_date: date,
    modele_actuel: str,
    candidats: Sequence[str],
    taille_echantillon: int,
    hypotheses: HypothesesCout,
    modele_juge_id: str,
    tarif_juge: TarifModele | None = None,
    plafond_usd: float | None = None,
) -> EstimationCampagne:
    """Estime le coût d'une campagne avant lancement (§17.1, ENF-COUT-01).

    `tarif_juge` permet de fournir le tarif du modèle juge quand la table ne le
    couvre pas encore (le juge est souvent un modèle hors du périmètre client).
    """
    if taille_echantillon <= 0:
        raise ValidationError(
            "La taille d'échantillon doit être strictement positive.",
            taille_echantillon=taille_echantillon,
        )
    modeles_candidats = list(candidats)
    rejeu = _cout_rejeu(
        tarifs, region, a_la_date, modele_actuel, modeles_candidats, taille_echantillon, hypotheses
    )
    tarif_du_juge = tarif_juge or _tarif(tarifs, modele_juge_id, region, NIVEAU_JUGE, a_la_date)
    jugements = taille_echantillon * (len(modeles_candidats) * COMPARAISONS_PAR_CANDIDAT + 1)
    evaluation = jugements * _cout_jugement(tarif_du_juge, hypotheses)
    total = rejeu + evaluation
    return EstimationCampagne(
        taille_echantillon=taille_echantillon,
        nombre_candidats=len(modeles_candidats),
        executions_rejeu=taille_echantillon * (EXECUTIONS_REFERENCE + len(modeles_candidats)),
        jugements=jugements,
        cout_rejeu_usd=round(rejeu, 8),
        cout_evaluation_usd=round(evaluation, 8),
        cout_total_usd=round(total, 8),
        plafond_usd=plafond_usd,
        sous_plafond=plafond_usd is None or total <= plafond_usd,
    )


def verifier_plafond(estimation: EstimationCampagne, plafond_usd: float) -> EstimationCampagne:
    """Refuse la campagne si l'estimation dépasse le plafond (ENF-COUT-01)."""
    if plafond_usd < 0:
        raise ValidationError("Le plafond d'une campagne ne peut pas être négatif.")
    if estimation.cout_total_usd > plafond_usd:
        raise PlafondDepasseError(
            "Le coût estimé de la campagne dépasse le plafond du client.",
            cout_estime_usd=estimation.cout_total_usd,
            plafond_usd=plafond_usd,
            depassement_usd=round(estimation.cout_total_usd - plafond_usd, 8),
        )
    return estimation


def estimer_et_verifier(
    *,
    tarifs: TableTarifs,
    region: str,
    a_la_date: date,
    modele_actuel: str,
    candidats: Sequence[str],
    taille_echantillon: int,
    hypotheses: HypothesesCout,
    modele_juge_id: str,
    plafond_usd: float,
    tarif_juge: TarifModele | None = None,
) -> EstimationCampagne:
    """Estime puis vérifie le plafond : porte d'entrée unique avant un rejeu.

    En cas de dépassement, `PlafondDepasseError` est levée **avant** tout appel
    de modèle : aucun dollar n'est dépensé pour découvrir que le plafond saute.
    """
    estimation = estimer_cout_campagne(
        tarifs=tarifs,
        region=region,
        a_la_date=a_la_date,
        modele_actuel=modele_actuel,
        candidats=candidats,
        taille_echantillon=taille_echantillon,
        hypotheses=hypotheses,
        modele_juge_id=modele_juge_id,
        tarif_juge=tarif_juge,
        plafond_usd=plafond_usd,
    )
    return verifier_plafond(estimation, plafond_usd)
