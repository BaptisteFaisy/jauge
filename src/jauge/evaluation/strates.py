"""Analyse des résultats de rejeu **par strate** (Top 10 n° 4 — §13.1, §13.6).

Le §13.6 déclare la limite : « un échantillon de 200 requêtes ne voit pas les
cas rares ». Une moyenne plate peut masquer une régression localisée sur une
strate minoritaire mais critique. Ce module découpe les résultats selon les
strates **déjà produites par l'échantillonnage** (`jauge.replay.sampling` :
longueur × type de requête), croisées avec l'application et l'équipe quand
elles sont disponibles, et rend pour chaque strate :

- le taux d'équivalence et son effectif ;
- l'intervalle de Wilson à 95 % — calculé par `jauge.engine.stats.intervalle_wilson`,
  jamais réécrit ici (zéro duplication) ;
- la part de dépense de la strate (une strate qui pèse 40 % de la facture doit
  peser 40 % dans la preuve — Top 10 n° 4) ;
- un statut : `conforme`, `regression` (la **borne basse** de l'intervalle
  tombe sous le seuil du §13.4, condition 2 — même si l'agrégat tient) ou
  `non_concluante` (effectif insuffisant : on ne déclare ni conforme ni en
  régression une strate qu'on n'a pas assez observée).

ENF-IA-01 : tous les nombres sont calculés ici par du code déterministe.
ENF-CONF-01 : aucune entrée ni sortie ne contient de texte de requête ou de
réponse — uniquement des identifiants, des verdicts et des coûts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from jauge.common.errors import ValidationError
from jauge.engine.stats import IntervalleTaux, intervalle_wilson
from jauge.evaluation.decision import SeuilsDecision
from jauge.evaluation.scoring import PaireEvaluee
from jauge.models.campaign import EchantillonRequete

# Effectif minimal pour qu'une strate soit concluante. En deçà, l'intervalle
# de Wilson est si large que sa borne basse tomberait sous le seuil sans que
# l'on puisse rien affirmer : la strate est déclarée « non concluante », pas
# « en régression ». 30 reprend la taille du sous-échantillon de relecture
# humaine (§13.5, `jauge.evaluation.human_review.TAILLE_ECHANTILLON`).
EFFECTIF_MINIMUM_STRATE = 30


class StatutStrate(StrEnum):
    """Lecture d'une strate face au seuil du §13.4 (condition 2)."""

    CONFORME = "conforme"
    REGRESSION = "regression"
    NON_CONCLUANTE = "non_concluante"


@dataclass(frozen=True)
class RequetePonderee:
    """Une requête évaluée, sa strate d'échantillonnage et son poids économique.

    `cout_usd` est la **dépense de production** rattachée à cette requête (ou à
    sa classe de requêtes), issue de l'attribution (`jauge.engine.attribution` /
    `jauge.engine.calculator`) — pas le coût du rejeu. `equivalent` est le
    verdict du juge, déjà rendu : rien n'est re-noté ici (ENF-IA-01).
    """

    requete_id: str
    equivalent: bool
    strate: str
    cout_usd: float = 0.0
    application_id: str = ""
    equipe: str = ""

    def __post_init__(self) -> None:
        if self.cout_usd < 0:
            raise ValidationError(
                "Le coût d'une requête ne peut pas être négatif.",
                requete_id=self.requete_id,
                cout_usd=self.cout_usd,
            )
        if not self.strate:
            raise ValidationError(
                "Une requête évaluée doit porter sa strate d'échantillonnage.",
                requete_id=self.requete_id,
            )


@dataclass(frozen=True)
class StrateResultat:
    """Mesures d'une strate : taux, effectif, Wilson, dépense, statut."""

    cle: str
    strate: str
    application_id: str
    equipe: str
    effectif: int
    equivalents: int
    intervalle: IntervalleTaux
    cout_usd: float
    part_depense: float
    statut: StatutStrate

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable pour le plan de contrôle (ENF-CONF-01)."""
        return {
            "cle": self.cle,
            "strate": self.strate,
            "application_id": self.application_id,
            "equipe": self.equipe,
            "effectif": self.effectif,
            "equivalents": self.equivalents,
            "taux": self.intervalle.taux,
            "intervalle_bas": self.intervalle.bas,
            "intervalle_haut": self.intervalle.haut,
            "cout_usd": self.cout_usd,
            "part_depense": self.part_depense,
            "statut": str(self.statut),
        }


@dataclass(frozen=True)
class AnalyseStrates:
    """Analyse complète d'un lot évalué, strate par strate."""

    strates: tuple[StrateResultat, ...] = ()
    effectif_total: int = 0
    equivalents_total: int = 0
    depense_totale_usd: float = 0.0
    effectif_minimum: int = EFFECTIF_MINIMUM_STRATE

    def en_regression(self) -> list[StrateResultat]:
        """Strates en régression, de la plus lourde économiquement à la plus légère."""
        return sorted(
            (s for s in self.strates if s.statut is StatutStrate.REGRESSION),
            key=lambda s: (-s.part_depense, s.cle),
        )

    def non_concluantes(self) -> list[StrateResultat]:
        """Strates sans effectif suffisant : ni conformes, ni en régression."""
        return [s for s in self.strates if s.statut is StatutStrate.NON_CONCLUANTE]

    def part_depense_en_regression(self) -> float:
        """Part de la dépense totale portée par les strates en régression (0 à 1)."""
        return round(
            sum(s.part_depense for s in self.strates if s.statut is StatutStrate.REGRESSION), 6
        )

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable, prête pour la recommandation et le rapport."""
        return {
            "strates": [s.vers_dict() for s in self.strates],
            "effectif_total": self.effectif_total,
            "equivalents_total": self.equivalents_total,
            "depense_totale_usd": self.depense_totale_usd,
            "effectif_minimum": self.effectif_minimum,
            "part_depense_en_regression": self.part_depense_en_regression(),
            "strates_en_regression": [s.cle for s in self.en_regression()],
            "strates_non_concluantes": [s.cle for s in self.non_concluantes()],
        }


def construire_entrees(
    paires: Sequence[PaireEvaluee],
    echantillon: Sequence[EchantillonRequete],
    couts_par_requete: Mapping[str, float] | None = None,
    equipe: str = "",
) -> list[RequetePonderee]:
    """Joint verdicts du juge, strates de l'échantillon et coûts d'attribution.

    Les trois sources existent déjà : `paires` vient de l'évaluation
    (`jauge.evaluation.scoring`), `echantillon` du tirage stratifié
    (`jauge.replay.sampling`), `couts_par_requete` de l'attribution
    (requete_id → dépense de production en USD). Un verdict dont la requête
    est absente de l'échantillon est refusé : une preuve stratifiée ne se
    construit que sur des requêtes attribuables à une strate.
    """
    if not paires:
        raise ValidationError("aucune paire évaluée : l'analyse par strate est impossible")
    par_id = {requete.requete_id: requete for requete in echantillon}
    entrees: list[RequetePonderee] = []
    for paire in paires:
        source = par_id.get(paire.requete_id)
        if source is None:
            raise ValidationError(
                "Requête évaluée absente de l'échantillon : strate introuvable.",
                requete_id=paire.requete_id,
            )
        entrees.append(
            RequetePonderee(
                requete_id=paire.requete_id,
                equivalent=paire.verdict.equivalent,
                strate=source.stratum,
                cout_usd=float((couts_par_requete or {}).get(paire.requete_id, 0.0)),
                application_id=source.application_id,
                equipe=equipe,
            )
        )
    return entrees


def cle_strate(requete: RequetePonderee) -> str:
    """Clé lisible d'une strate : strate d'échantillonnage + application + équipe."""
    morceaux = [m for m in (requete.strate, requete.application_id, requete.equipe) if m]
    return " | ".join(morceaux)


def _grouper(requetes: Sequence[RequetePonderee]) -> dict[str, list[RequetePonderee]]:
    """Regroupe les requêtes évaluées par strate (clé complète)."""
    groupes: dict[str, list[RequetePonderee]] = {}
    for requete in requetes:
        groupes.setdefault(cle_strate(requete), []).append(requete)
    return groupes


def _statut(intervalle: IntervalleTaux, seuil_bas: float, effectif_minimum: int) -> StatutStrate:
    """Statut d'une strate : non concluante si trop peu observée, sinon conforme
    ou en régression selon la **borne basse** de Wilson face au seuil (§13.4)."""
    if intervalle.n < effectif_minimum:
        return StatutStrate.NON_CONCLUANTE
    if intervalle.bas < seuil_bas:
        return StatutStrate.REGRESSION
    return StatutStrate.CONFORME


def _resultat_strate(
    cle: str, lot: Sequence[RequetePonderee], depense_totale: float, seuil_bas: float, minimum: int
) -> StrateResultat:
    """Consolide une strate : Wilson réutilisé du socle, part de dépense exacte."""
    premiere = lot[0]
    equivalents = sum(1 for requete in lot if requete.equivalent)
    intervalle = intervalle_wilson(equivalents, len(lot))
    cout = round(sum(requete.cout_usd for requete in lot), 8)
    part = round(cout / depense_totale, 6) if depense_totale > 0 else 0.0
    return StrateResultat(
        cle=cle,
        strate=premiere.strate,
        application_id=premiere.application_id,
        equipe=premiere.equipe,
        effectif=len(lot),
        equivalents=equivalents,
        intervalle=intervalle,
        cout_usd=cout,
        part_depense=part,
        statut=_statut(intervalle, seuil_bas, minimum),
    )


def analyser_strates(
    requetes: Sequence[RequetePonderee],
    seuils: SeuilsDecision | None = None,
    effectif_minimum: int = EFFECTIF_MINIMUM_STRATE,
) -> AnalyseStrates:
    """Analyse chaque strate : taux, effectif, Wilson, part de dépense, statut.

    Le seuil de régression est `seuils.seuil_intervalle_bas` (90 % par défaut) :
    c'est la borne basse de l'intervalle qui décide, exactement comme la
    condition 2 du §13.4 — une strate dont on ne peut pas exclure qu'elle soit
    sous 90 % n'est pas une strate saine, même si la moyenne tient.
    """
    if not requetes:
        raise ValidationError("aucune requête évaluée : l'analyse par strate est impossible")
    if effectif_minimum < 1:
        raise ValidationError(
            "L'effectif minimal d'une strate concluante doit être >= 1.",
            effectif_minimum=effectif_minimum,
        )
    seuils = seuils or SeuilsDecision()
    depense_totale = round(sum(requete.cout_usd for requete in requetes), 8)
    groupes = _grouper(requetes)
    strates = tuple(
        _resultat_strate(
            cle, groupes[cle], depense_totale, seuils.seuil_intervalle_bas, effectif_minimum
        )
        for cle in sorted(groupes)
    )
    return AnalyseStrates(
        strates=strates,
        effectif_total=len(requetes),
        equivalents_total=sum(1 for requete in requetes if requete.equivalent),
        depense_totale_usd=depense_totale,
        effectif_minimum=effectif_minimum,
    )
