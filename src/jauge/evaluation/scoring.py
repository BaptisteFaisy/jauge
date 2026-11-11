"""Consolidation des notes du juge en mesures (EF-EVA-03, EF-EVA-04, EF-EVA-05).

Ce module ne note rien : il **compte**. Tous les nombres sont calculés ici par
du code déterministe (ENF-IA-01) à partir des verdicts rendus par le juge
(`jauge.evaluation.judge`) et des contrôles déterministes
(`jauge.evaluation.checks`).

Références :
- EF-EVA-03 / §13.3 : taux d'équivalence et intervalle de confiance à 95 %
  (intervalle de Wilson, `jauge.engine.stats.intervalle_wilson`).
- EF-EVA-04 / §13.2 : auto-accord du modèle actuel — deux exécutions du modèle
  actuel comparées entre elles mesurent sa variabilité naturelle. C'est la
  référence de lecture : un candidat à 95 % face à un auto-accord de 96 % est
  excellent (§13.4).
- EF-EVA-05 / §13.5 : accord entre le juge et le relecteur humain.
- §13.6 : les familles de requêtes en échec sont listées nommément, pour que la
  recommandation les cite en risques.

Le résultat produit est `jauge.models.campaign.ResultatCandidat`, entité du
socle : il ne contient aucun texte de requête ni de réponse (ENF-CONF-01).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from jauge.common.errors import ValidationError
from jauge.engine.stats import IntervalleTaux, intervalle_wilson
from jauge.evaluation.checks import ResultatControle, echecs_critiques
from jauge.evaluation.judge import VerdictJuge
from jauge.models.campaign import ResultatCandidat

# Taux minimal d'équivalence d'une famille de requêtes pour ne pas être
# signalée en échec (§13.6).
SEUIL_FAMILLE = 0.90


@dataclass(frozen=True)
class PaireEvaluee:
    """Paire notée : verdict du juge et contrôles déterministes (sans les textes).

    Les textes restent dans `PaireRejoueeDataPlane`, stockée dans le compte
    client ; cette vue ne porte que des identifiants et des verdicts.
    """

    requete_id: str
    verdict: VerdictJuge
    famille: str = "general"
    controles: tuple[ResultatControle, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MesuresCoutLatence:
    """Mesures de rejeu par requête (EF-REJ-04), fournies par le module de rejeu."""

    cout_par_requete_usd: float
    latence_p50_ms: int = 0
    latence_p95_ms: int = 0


def taux_equivalence(paires: Sequence[PaireEvaluee]) -> IntervalleTaux:
    """Taux d'équivalence et son intervalle de Wilson à 95 % (EF-EVA-03)."""
    if not paires:
        raise ValidationError("aucune paire évaluée : le taux n'est pas calculable")
    succes = sum(1 for paire in paires if paire.verdict.equivalent)
    return intervalle_wilson(succes, len(paires))


def taux_par_famille(paires: Sequence[PaireEvaluee]) -> dict[str, IntervalleTaux]:
    """Taux d'équivalence par famille de requêtes (§13.6)."""
    groupes: dict[str, list[PaireEvaluee]] = defaultdict(list)
    for paire in paires:
        groupes[paire.famille].append(paire)
    return {famille: taux_equivalence(groupe) for famille, groupe in sorted(groupes.items())}


def familles_en_echec(paires: Sequence[PaireEvaluee], seuil: float = SEUIL_FAMILLE) -> list[str]:
    """Familles dont le taux d'équivalence passe sous `seuil` (§13.6).

    Liste triée, pour que la recommandation cite ces familles en risques.
    """
    taux = taux_par_famille(paires)
    return [famille for famille, resultat in taux.items() if resultat.taux < seuil]


def auto_accord(paires_reference: Sequence[PaireEvaluee]) -> IntervalleTaux:
    """Auto-accord du modèle actuel (EF-EVA-04).

    Les paires passées sont les comparaisons entre les deux exécutions du
    modèle actuel ; le taux obtenu mesure sa variabilité naturelle et sert de
    point de comparaison au taux du candidat (§13.4).
    """
    return taux_equivalence(paires_reference)


def accord_juge_humain(
    verdicts_juge: Sequence[VerdictJuge], verdicts_humain: Sequence[bool]
) -> float:
    """Accord brut entre le juge et le relecteur humain (EF-EVA-05).

    Rend la proportion de paires où les deux verdicts concordent. Les deux
    listes doivent être alignées (même paire, même rang).
    """
    if not verdicts_juge:
        raise ValidationError("aucun verdict comparé : l'accord n'est pas calculable")
    if len(verdicts_juge) != len(verdicts_humain):
        raise ValidationError(
            "verdicts du juge et de l'humain de tailles différentes",
            juge=len(verdicts_juge),
            humain=len(verdicts_humain),
        )
    accords = sum(
        1
        for verdict, humain in zip(verdicts_juge, verdicts_humain, strict=True)
        if verdict.equivalent == humain
    )
    return round(accords / len(verdicts_juge), 6)


def nombre_echecs_deterministes(paires: Sequence[PaireEvaluee]) -> int:
    """Paires portant au moins un contrôle critique en échec (§13.4, condition 3)."""
    return sum(1 for paire in paires if echecs_critiques(paire.controles))


def consolider_resultat(
    modele_id: str,
    paires: Sequence[PaireEvaluee],
    mesures: MesuresCoutLatence,
    auto_accord_modele_actuel: float = 1.0,
    accord_humain: float | None = None,
    est_reference: bool = False,
) -> ResultatCandidat:
    """Produit le `ResultatCandidat` du socle pour un candidat (§14, Annexe B).

    `auto_accord_modele_actuel` est toujours renseigné, y compris pour le
    candidat : le taux se lit à côté de cette référence (§13.4). Aucun texte
    n'entre dans l'objet rendu (ENF-CONF-01).
    """
    intervalle = taux_equivalence(paires)
    return ResultatCandidat(
        modele_id=modele_id,
        est_reference=est_reference,
        nombre_requetes_rejouees=len(paires),
        taux_equivalence=intervalle.taux,
        intervalle_confiance_bas=intervalle.bas,
        intervalle_confiance_haut=intervalle.haut,
        auto_accord_modele_actuel=auto_accord_modele_actuel,
        echecs_deterministes=nombre_echecs_deterministes(paires),
        accord_juge_humain=accord_humain,
        cout_par_requete_usd=mesures.cout_par_requete_usd,
        latence_p50_ms=mesures.latence_p50_ms,
        latence_p95_ms=mesures.latence_p95_ms,
        familles_en_echec=familles_en_echec(paires),
    )
