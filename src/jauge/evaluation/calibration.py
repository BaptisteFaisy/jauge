"""Calibration du modèle juge : les biais **mesurés**, pas supposés (Top 10, n° 2).

Le risque n° 1 du cahier des charges (§21) est « le modèle juge se trompe ».
Jusqu'ici Jauge le **mitige** (30 paires relues par un humain, accord affiché,
§13.5) sans le **mesurer**. Ce module rend la mesure possible :

1. un **jeu de calibration** hors ligne (`jauge.evaluation.jeux_calibration`) :
   paires de qualité connue, items-pièges dorés, paires de qualité identique
   présentées dans les deux ordres, paires où la réponse la plus longue est la
   moins bonne, paires au formatage différent pour un contenu identique ;
2. un **aveuglement** : l'ordre des deux réponses est mélangé et toute trace du
   modèle producteur est effacée avant la notation, avec vérification qu'aucune
   trace ne subsiste dans le texte soumis ;
3. des **mesures** : accord avec la vérité, faux-accept, faux-rejet, biais de
   position, biais de verbosité, biais de format, accord sur les pièges dorés ;
4. un **verdict d'aptitude** (`apte` / `à recalibrer` / `rejeté`) et une
   **dérive** suivie dans le temps, calculés par
   `jauge.evaluation.aptitude_juge` ;
5. un **bloc sérialisable** prêt à être cité dans une recommandation.

ENF-IA-01 : aucun nombre de ce module n'est produit par un modèle. Le juge ne
rend qu'un verdict booléen et une explication ; tous les taux, écarts et
intervalles sont calculés ici, par du code déterministe
(`jauge.engine.stats.intervalle_wilson`).

ENF-CONF-01 : le bloc rendu au plan de contrôle ne porte que des nombres, des
identifiants et des libellés de famille — aucun texte de requête ni de réponse.

Où brancher le bloc (intégration à faire, hors de ce lot) :
`jauge.integration.recommandation.AdaptateurEntrees.valeurs()` construit les
blocs de l'Annexe B ; il suffit d'y ajouter le bloc rendu par
`bloc_calibration()`, comme champ `judgeCalibration` du modèle `Recommandation`
(`src/jauge/models/recommendation.py`, verrouillé pour ce lot).
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from jauge.common.errors import ValidationError
from jauge.common.ids import deterministic, graine_de, jour_compact
from jauge.common.temps import aujourdhui as date_du_jour
from jauge.engine.stats import IntervalleTaux, intervalle_wilson
from jauge.evaluation.aptitude_juge import (
    ObservationCalibration,
    RapportDerive,
    SeuilsCalibration,
    VerdictAptitude,
    juger_aptitude,
)
from jauge.evaluation.jeux_calibration import (
    FamilleCalibration,
    ItemCalibration,
    jeu_calibration_defaut,
    trace_modele,
    verifier_jeu,
)
from jauge.evaluation.judge import PROMPT_EQUIVALENCE, DemandeNotation, JugeClient, VerdictJuge

#: Graine par défaut du mélange d'ordre (reproductible : §13.1).
GRAINE_DEFAUT = 42


def version_prompt_courante() -> str:
    """Empreinte du prompt de juge : identifie la version utilisée.

    Le prompt fait partie de la méthode : changer de prompt change le juge.
    L'empreinte est calculée par `jauge.common.ids.deterministic` — stable
    d'une machine à l'autre.
    """
    return f"prompt-{deterministic(PROMPT_EQUIVALENCE.template)[:12]}"


@dataclass(frozen=True)
class PaireAveuglee:
    """Paire préparée pour le juge : ordre mélangé, identité effacée."""

    item: ItemCalibration
    demande: DemandeNotation
    ordre_inverse: bool


@dataclass(frozen=True)
class ResultatItem:
    """Verdict du juge sur un item (aucun texte de requête ni de réponse)."""

    identifiant: str
    famille: str
    gold_equivalent: bool
    verdict_equivalent: bool
    ordre_inverse: bool
    justification: str


@dataclass(frozen=True)
class MesuresCalibration:
    """Mesures de fiabilité d'un juge sur le jeu de calibration.

    `biais_verbosite` et `biais_format` sont des **écarts** : la différence
    entre la famille sondée et les autres familles. Ils sont positifs quand le
    juge favorise la réponse longue (resp. mal formatée), négatifs quand il
    s'en méfie. Un écart négatif n'est pas un défaut : il se paie en faux-rejet,
    que le taux de faux-rejet mesure.
    """

    nombre_items: int
    accord_gold: IntervalleTaux
    faux_accept: IntervalleTaux
    faux_rejet: IntervalleTaux
    accord_pieges_dores: IntervalleTaux
    biais_position: float
    biais_verbosite: float
    biais_format: float
    paires_position: int
    divergences_position: int
    effectifs: Mapping[str, int] = field(default_factory=dict)


def aveugler_item(item: ItemCalibration, graine: int = GRAINE_DEFAUT) -> PaireAveuglee:
    """Prépare un item pour le juge : mélange l'ordre et efface le modèle.

    Le mélange est tiré d'une graine **explicite** : à graine fixée, l'ordre est
    identique d'une exécution à l'autre, donc les mesures sont comparables dans
    le temps (§13.1).

    Pour une paire présentée dans les deux ordres, le tirage porte sur la
    **paire** (`cle_paire`) et non sur l'item : les deux ordres reçoivent la
    même décision, donc restent bien deux ordres distincts. Un tirage par item
    pourrait remettre les deux réponses dans le même ordre et rendre la mesure
    du biais de position aveugle.

    La vérification d'aveuglement est bloquante : si une trace du modèle
    producteur subsiste dans le texte soumis, la notation est refusée plutôt que
    de laisser le juge deviner qui a produit quoi.
    """
    cle = item.cle_paire or item.identifiant
    generateur = random.Random(graine_de(graine, "calibration", cle))
    inverse = generateur.random() < 0.5
    reference = item.reponse_candidate if inverse else item.reponse_reference
    candidate = item.reponse_reference if inverse else item.reponse_candidate
    demande = DemandeNotation(
        requete_id=item.identifiant,
        requete=item.requete,
        reponse_reference=reference,
        reponse_candidate=candidate,
        type_tache=item.type_tache,
    )
    _verifier_aveuglement(item, demande)
    return PaireAveuglee(item=item, demande=demande, ordre_inverse=inverse)


def _verifier_aveuglement(item: ItemCalibration, demande: DemandeNotation) -> None:
    """Refuse la notation si une trace du modèle producteur subsiste."""
    textes = (
        demande.requete_id,
        demande.requete,
        demande.reponse_reference,
        demande.reponse_candidate,
    )
    for texte in textes:
        trace = trace_modele(texte, item)
        if trace:
            raise ValidationError(
                "trace du modèle producteur dans la paire soumise au juge",
                identifiant=item.identifiant,
                trace=trace,
            )


def aveugler_jeu(
    items: Sequence[ItemCalibration], graine: int = GRAINE_DEFAUT
) -> list[PaireAveuglee]:
    """Prépare tout le jeu pour le juge, dans l'ordre du jeu."""
    return [aveugler_item(item, graine) for item in items]


@dataclass
class _Comptes:
    """Compteurs internes au calcul des mesures (aucun texte)."""

    total: int = 0
    accord: int = 0
    faux_accept: int = 0
    faux_accept_total: int = 0
    faux_rejet: int = 0
    faux_rejet_total: int = 0
    pieges_accord: int = 0
    pieges_total: int = 0
    par_famille: dict[str, list[tuple[bool, bool]]] = field(default_factory=dict)
    par_cle: dict[tuple[str, str], list[bool]] = field(default_factory=dict)
    effectifs: dict[str, int] = field(default_factory=dict)


def _compter(comptes: _Comptes, item: ItemCalibration, equivalent: bool) -> None:
    """Ajoute un verdict aux compteurs."""
    gold = item.gold_equivalent
    comptes.total += 1
    if equivalent == gold:
        comptes.accord += 1
    if gold:
        comptes.faux_rejet_total += 1
        comptes.faux_rejet += 0 if equivalent else 1
    else:
        comptes.faux_accept_total += 1
        comptes.faux_accept += 1 if equivalent else 0
    if item.famille is FamilleCalibration.PIEGE_DORE:
        comptes.pieges_total += 1
        comptes.pieges_accord += 1 if equivalent == gold else 0
    famille = item.famille.value
    comptes.effectifs[famille] = comptes.effectifs.get(famille, 0) + 1
    comptes.par_famille.setdefault(famille, []).append((gold, equivalent))
    if item.cle_paire:
        comptes.par_cle.setdefault((famille, item.cle_paire), []).append(equivalent)


def _taux(succes: int, total: int, libelle: str) -> float:
    """Taux simple ; refuse de calculer sur une famille vide."""
    if total <= 0:
        raise ValidationError(
            "famille absente du jeu de calibration : mesure non calculable", famille=libelle
        )
    return round(succes / total, 6)


def _autres_paires(comptes: _Comptes, famille: str, gold: bool) -> list[tuple[bool, bool]]:
    """Paires d'une autre vérité, hors de la famille sondée (base de contraste)."""
    return [
        paire
        for nom, valeurs in comptes.par_famille.items()
        if nom != famille
        for paire in valeurs
        if paire[0] is gold
    ]


def _biais_verbosite(comptes: _Comptes) -> float:
    """Écart de faux-accept entre la famille « verbosité » et les autres.

    La famille « verbosité » ne contient que des paires non équivalentes où la
    réponse la plus longue est la moins bonne : un juge qui privilégie la
    longueur les accepte à tort, plus souvent que les autres paires fausses.
    """
    famille = FamilleCalibration.VERBOSITE.value
    paires = comptes.par_famille.get(famille, [])
    if not paires:
        raise ValidationError("famille « verbosité » absente du jeu de calibration")
    autres = _autres_paires(comptes, famille, False)
    if not autres:
        raise ValidationError("aucune paire non équivalente hors famille « verbosité »")
    accepts = sum(1 for _, equivalent in paires if equivalent)
    accepts_autres = sum(1 for _, equivalent in autres if equivalent)
    return round(
        _taux(accepts, len(paires), famille) - _taux(accepts_autres, len(autres), "autre"), 6
    )


def _biais_format(comptes: _Comptes) -> float:
    """Écart de faux-rejet entre la famille « format » et les autres.

    La famille « format » ne contient que des paires équivalentes dont seule la
    présentation change : un juge sensible au formatage les rejette à tort, plus
    souvent que les autres paires équivalentes.
    """
    famille = FamilleCalibration.FORMAT.value
    paires = comptes.par_famille.get(famille, [])
    if not paires:
        raise ValidationError("famille « format » absente du jeu de calibration")
    autres = _autres_paires(comptes, famille, True)
    if not autres:
        raise ValidationError("aucune paire équivalente hors famille « format »")
    rejets = sum(1 for _, equivalent in paires if not equivalent)
    rejets_autres = sum(1 for _, equivalent in autres if not equivalent)
    return round(
        _taux(rejets, len(paires), famille) - _taux(rejets_autres, len(autres), "autre"), 6
    )


def _biais_position(comptes: _Comptes) -> tuple[float, int, int]:
    """Taux de divergence entre les deux ordres, nombre de paires, divergences.

    Une paire est **divergente** quand les deux ordres ne reçoivent pas le même
    verdict : c'est la signature d'un juge ancré sur la position.
    """
    groupes = [
        verdicts
        for (famille, _), verdicts in comptes.par_cle.items()
        if famille == FamilleCalibration.POSITION.value
    ]
    if not groupes:
        raise ValidationError("aucune paire présentée dans les deux ordres : biais non calculable")
    divergences = sum(1 for verdicts in groupes if len(set(verdicts)) > 1)
    return round(divergences / len(groupes), 6), len(groupes), divergences


def _analyser(
    paires: Sequence[PaireAveuglee], verdicts: Sequence[VerdictJuge]
) -> tuple[MesuresCalibration, tuple[ResultatItem, ...]]:
    """Mesures et résultats par item, à partir des verdicts du juge."""
    if len(paires) != len(verdicts):
        raise ValidationError(
            "verdicts et paires de tailles différentes", paires=len(paires), verdicts=len(verdicts)
        )
    comptes = _Comptes()
    resultats: list[ResultatItem] = []
    for paire, verdict in zip(paires, verdicts, strict=True):
        item = paire.item
        _compter(comptes, item, verdict.equivalent)
        resultats.append(
            ResultatItem(
                identifiant=item.identifiant,
                famille=item.famille.value,
                gold_equivalent=item.gold_equivalent,
                verdict_equivalent=verdict.equivalent,
                ordre_inverse=paire.ordre_inverse,
                justification=item.justification,
            )
        )
    biais, nb_paires, divergences = _biais_position(comptes)
    mesures = MesuresCalibration(
        nombre_items=comptes.total,
        accord_gold=intervalle_wilson(comptes.accord, comptes.total),
        faux_accept=intervalle_wilson(comptes.faux_accept, comptes.faux_accept_total),
        faux_rejet=intervalle_wilson(comptes.faux_rejet, comptes.faux_rejet_total),
        accord_pieges_dores=intervalle_wilson(comptes.pieges_accord, comptes.pieges_total),
        biais_position=biais,
        biais_verbosite=_biais_verbosite(comptes),
        biais_format=_biais_format(comptes),
        paires_position=nb_paires,
        divergences_position=divergences,
        effectifs=dict(sorted(comptes.effectifs.items())),
    )
    return mesures, tuple(resultats)


def mesurer(paires: Sequence[PaireAveuglee], verdicts: Sequence[VerdictJuge]) -> MesuresCalibration:
    """Mesures de fiabilité du juge sur le jeu de calibration."""
    return _analyser(paires, verdicts)[0]


@dataclass(frozen=True)
class RapportCalibration:
    """Résultat complet d'une calibration : mesures, aptitude et détail."""

    juge_id: str
    version_prompt: str
    jour: str
    graine: int
    mesures: MesuresCalibration
    aptitude: VerdictAptitude
    resultats: tuple[ResultatItem, ...]

    def observation(self) -> ObservationCalibration:
        """Vue de ce rapport comme observation datée (pour l'historique)."""
        return ObservationCalibration(
            version_prompt=self.version_prompt,
            jour=self.jour,
            juge_id=self.juge_id,
            mesures=self.mesures,
            aptitude=self.aptitude.aptitude,
        )


def calibrer(
    juge: JugeClient,
    jeu: Sequence[ItemCalibration] | None = None,
    graine: int = GRAINE_DEFAUT,
    version_prompt: str | None = None,
    jour: str | None = None,
    seuils: SeuilsCalibration | None = None,
) -> RapportCalibration:
    """Calibre un juge : aveuglement, notation, mesures et verdict d'aptitude.

    `juge` reçoit le lot de paires **aveuglées** (ordre mélangé, aucune trace du
    modèle producteur) et rend un verdict par paire. Tout le reste est calculé
    ici. Le jeu par défaut est celui de `jeu_calibration_defaut()`.
    """
    items = list(jeu) if jeu is not None else jeu_calibration_defaut()
    verifier_jeu(items)
    paires = aveugler_jeu(items, graine)
    verdicts = juge.noter_lot([paire.demande for paire in paires])
    mesures, resultats = _analyser(paires, verdicts)
    return RapportCalibration(
        juge_id=juge.modele_id,
        version_prompt=version_prompt or version_prompt_courante(),
        jour=jour or jour_compact(date_du_jour()),
        graine=graine,
        mesures=mesures,
        aptitude=juger_aptitude(mesures, seuils),
        resultats=resultats,
    )


def _intervalle(intervalle: IntervalleTaux) -> dict[str, object]:
    """Vue sérialisable d'un intervalle de taux."""
    return {
        "rate": intervalle.taux,
        "low": intervalle.bas,
        "high": intervalle.haut,
        "n": intervalle.n,
    }


def _bloc_derive(derive: RapportDerive) -> dict[str, object]:
    """Vue sérialisable du rapport de dérive."""
    return {
        "drifted": derive.derive,
        "promptChanged": derive.prompt_modifie,
        "referenceVersion": derive.version_reference,
        "currentVersion": derive.version_courante,
        "referenceDate": derive.jour_reference,
        "currentDate": derive.jour_courant,
        "affectedObservations": list(derive.observations_concernees),
        "metrics": [
            {
                "name": ecart.nom,
                "before": ecart.avant,
                "after": ecart.apres,
                "drift": ecart.derive,
                "threshold": ecart.seuil,
                "exceeded": ecart.depasse,
            }
            for ecart in derive.ecarts
        ],
        "motifs": list(derive.motifs),
    }


def bloc_calibration(
    rapport: RapportCalibration, derive: RapportDerive | None = None
) -> dict[str, object]:
    """Bloc de calibration sérialisable, prêt à être cité (Annexe B).

    Aucun texte de requête ni de réponse : uniquement des nombres, des
    identifiants et des libellés (ENF-CONF-01).
    """
    mesures = rapport.mesures
    return {
        "judgeId": rapport.juge_id,
        "promptVersion": rapport.version_prompt,
        "measuredOn": rapport.jour,
        "seed": rapport.graine,
        "sampleSize": mesures.nombre_items,
        "verdict": str(rapport.aptitude.aptitude),
        "goldAgreement": _intervalle(mesures.accord_gold),
        "falseAcceptRate": _intervalle(mesures.faux_accept),
        "falseRejectRate": _intervalle(mesures.faux_rejet),
        "goldenTrapAgreement": _intervalle(mesures.accord_pieges_dores),
        "positionBias": {
            "rate": mesures.biais_position,
            "pairs": mesures.paires_position,
            "divergences": mesures.divergences_position,
        },
        "verbosityBias": mesures.biais_verbosite,
        "formatBias": mesures.biais_format,
        "thresholds": dict(vars(rapport.aptitude.seuils)),
        "motifs": list(rapport.aptitude.motifs),
        "drift": _bloc_derive(derive) if derive is not None else None,
    }
