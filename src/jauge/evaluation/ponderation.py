"""Qualité pondérée par la dépense, sensibilité et verdict enrichi (Top 10 n° 4).

Deux changements de lecture par rapport au taux d'équivalence brut du §13.3,
**sans jamais remplacer la règle de décision du §13.4** :

1. **Qualité par dollar.** À côté du taux brut, un taux pondéré par la dépense
   de production de chaque strate. Formules (tout est calculé par le code,
   ENF-IA-01) :

       T_brut   = Σᵢ xᵢ / n                          (xᵢ ∈ {0,1} : verdict du juge)
       T_dépense = Σᵢ (cᵢ × xᵢ) / Σᵢ cᵢ = Σ_s (C_s × T_s) / Σ_s C_s

   avec cᵢ la dépense de production de la requête i, C_s la dépense et T_s le
   taux de la strate s. Les deux écritures sont égales : une strate qui pèse
   40 % de la facture compte 40 % dans la preuve. **Les deux chiffres sont
   toujours exposés ensemble** (`QualitePonderee`) — jamais l'un à la place de
   l'autre.

2. **Décision lue par strate.** Le §13.6 déclare : « un échantillon de 200
   requêtes ne voit pas les cas rares ». Une strate dont la borne basse de
   Wilson tombe sous le seuil est signalée en régression **même si l'agrégat
   tient** (`jauge.evaluation.strates`).

Le module ajoute trois instruments d'honnêteté, tous sérialisables :

- **Sensibilité aux pondérations** : verdict rendu sous trois pondérations
  (équipondérée par strate, par la dépense, par le volume) ; s'il **bascule**
  selon la pondération, ce n'est pas une preuve robuste. L'intervalle d'un taux
  pondéré utilise l'effectif effectif de Kish
  (`jauge.engine.stats.intervalle_wilson_pondere`) qui, à poids égaux, retombe
  exactement sur `intervalle_wilson` : la pondération par le volume reproduit
  donc la lecture du §13.4 à l'identique.
- **Nombre nécessaire pour inverser** : combien de verdicts du juge devraient
  changer de sens pour faire basculer la conclusion du §13.4 (marge de la
  preuve, en vente comme en audit).
- **Verdict enrichi** : la `DecisionCandidat` de `jauge.evaluation.decision`
  (règle du §13.4 intacte) est **enveloppée**, pas remplacée, par un avis
  motivé « agrégat conforme MAIS régression sur N strates représentant X % de
  la dépense », citable dans la recommandation (Annexe B, `risks`) et le
  rapport mensuel.

ENF-CONF-01 : aucune entrée ni sortie ne contient de texte de requête ou de
réponse — uniquement des identifiants, des verdicts, des coûts et des nombres.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from jauge.common.errors import ValidationError
from jauge.engine.stats import (
    IntervalleTaux,
    effectif_effectif,
    intervalle_wilson,
    intervalle_wilson_pondere,
)
from jauge.evaluation.decision import DecisionCandidat, SeuilsDecision, decider
from jauge.evaluation.strates import (
    AnalyseStrates,
    RequetePonderee,
    analyser_strates,
    cle_strate,
)
from jauge.models.campaign import ResultatCandidat


class TypePonderation(StrEnum):
    """Trois lectures de la même preuve : par strate, par dollar, par requête."""

    EQUIPONDEREE = "equiponderee"  # chaque strate pèse le même poids total
    PAR_DEPENSE = "par_depense"  # chaque requête pèse sa dépense de production
    PAR_VOLUME = "par_volume"  # chaque requête pèse 1 (reproduit le taux brut)


# Pondérations comparées par l'analyse de sensibilité, dans l'ordre du rapport.
PONDERATIONS_SENSIBILITE: tuple[TypePonderation, ...] = (
    TypePonderation.EQUIPONDEREE,
    TypePonderation.PAR_DEPENSE,
    TypePonderation.PAR_VOLUME,
)


def _bornes(intervalle: IntervalleTaux | None) -> dict[str, float] | None:
    """Bornes d'un intervalle en vue sérialisable ; `None` si non calculé."""
    if intervalle is None:
        return None
    return {"bas": intervalle.bas, "haut": intervalle.haut}


@dataclass(frozen=True)
class MesurePonderee:
    """Taux d'équivalence sous une pondération, avec son intervalle de Wilson.

    `calculable` est faux quand la pondération n'a pas de matière (ex. aucune
    donnée de dépense pour `PAR_DEPENSE`) : on rend alors `taux=None`, jamais
    un chiffre inventé (ENF-IA-01).
    """

    ponderation: TypePonderation
    calculable: bool
    taux: float | None
    intervalle: IntervalleTaux | None
    effectif_effectif: float
    poids_total: float

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable pour le plan de contrôle (ENF-CONF-01)."""
        return {
            "ponderation": str(self.ponderation),
            "calculable": self.calculable,
            "taux": self.taux,
            "intervalle": _bornes(self.intervalle),
            "effectif_effectif": round(self.effectif_effectif, 3),
            "poids_total": self.poids_total,
        }


def poids_des_requetes(
    requetes: Sequence[RequetePonderee], ponderation: TypePonderation
) -> list[float]:
    """Poids de chaque requête selon la pondération choisie.

    - `PAR_VOLUME` : wᵢ = 1 — le taux pondéré retombe sur le taux brut ;
    - `PAR_DEPENSE` : wᵢ = cᵢ, la dépense de production de la requête ;
    - `EQUIPONDEREE` : wᵢ = 1/n_s (n_s = effectif de la strate de la requête) —
      chaque strate pèse le même poids total, quelle que soit sa taille.
    """
    if ponderation is TypePonderation.PAR_VOLUME:
        return [1.0] * len(requetes)
    if ponderation is TypePonderation.PAR_DEPENSE:
        return [requete.cout_usd for requete in requetes]
    effectifs: dict[str, int] = {}
    for requete in requetes:
        cle = cle_strate(requete)
        effectifs[cle] = effectifs.get(cle, 0) + 1
    return [1.0 / effectifs[cle_strate(requete)] for requete in requetes]


def mesurer_pondere(
    requetes: Sequence[RequetePonderee], ponderation: TypePonderation
) -> MesurePonderee:
    """Taux et intervalle de Wilson sous une pondération (Top 10 n° 4)."""
    if not requetes:
        raise ValidationError("aucune requête évaluée : la mesure pondérée est impossible")
    poids = poids_des_requetes(requetes, ponderation)
    total = sum(poids)
    n_effectif = effectif_effectif(poids)
    if total <= 0:
        return MesurePonderee(
            ponderation=ponderation,
            calculable=False,
            taux=None,
            intervalle=None,
            effectif_effectif=0.0,
            poids_total=0.0,
        )
    succes_pondere = sum(p for p, r in zip(poids, requetes, strict=True) if r.equivalent)
    intervalle = intervalle_wilson_pondere(succes_pondere, poids)
    return MesurePonderee(
        ponderation=ponderation,
        calculable=True,
        taux=intervalle.taux,
        intervalle=intervalle,
        effectif_effectif=n_effectif,
        poids_total=round(total, 8),
    )


@dataclass(frozen=True)
class QualitePonderee:
    """Les deux chiffres côte à côte : brut **et** pondéré par la dépense.

    Le taux brut reste la référence du §13.4 ; le taux pondéré par la dépense
    est la lecture FinOps (Top 10 n° 4). Aucun des deux ne remplace l'autre.
    """

    effectif: int
    equivalents: int
    taux_brut: float
    intervalle_brut: IntervalleTaux
    taux_pondere_depense: float | None
    intervalle_pondere_depense: IntervalleTaux | None
    depense_totale_usd: float
    effectif_effectif_depense: float

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable : les deux taux et leurs intervalles, jamais l'un sans l'autre."""
        return {
            "effectif": self.effectif,
            "equivalents": self.equivalents,
            "taux_brut": self.taux_brut,
            "intervalle_brut": _bornes(self.intervalle_brut),
            "taux_pondere_depense": self.taux_pondere_depense,
            "intervalle_pondere_depense": _bornes(self.intervalle_pondere_depense),
            "depense_totale_usd": self.depense_totale_usd,
            "effectif_effectif_depense": round(self.effectif_effectif_depense, 3),
        }


def qualite_ponderee(requetes: Sequence[RequetePonderee]) -> QualitePonderee:
    """Taux brut (Wilson, §13.4) et taux pondéré par la dépense, ensemble."""
    if not requetes:
        raise ValidationError("aucune requête évaluée : la qualité pondérée est impossible")
    equivalents = sum(1 for requete in requetes if requete.equivalent)
    brut = intervalle_wilson(equivalents, len(requetes))
    depense = mesurer_pondere(requetes, TypePonderation.PAR_DEPENSE)
    return QualitePonderee(
        effectif=len(requetes),
        equivalents=equivalents,
        taux_brut=brut.taux,
        intervalle_brut=brut,
        taux_pondere_depense=depense.taux,
        intervalle_pondere_depense=depense.intervalle,
        depense_totale_usd=depense.poids_total,
        effectif_effectif_depense=depense.effectif_effectif,
    )


@dataclass(frozen=True)
class VerdictPondere:
    """Verdict du §13.4 rejoué sous une pondération (conditions 1 et 2).

    La condition 3 (échecs déterministes critiques) ne dépend pas de la
    pondération : elle est reprise à l'identique dans les trois verdicts.
    `propose` vaut `None` quand la pondération n'est pas calculable.
    """

    ponderation: TypePonderation
    propose: bool | None
    mesure: MesurePonderee

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable : le verdict et la mesure qui le porte."""
        vue = self.mesure.vers_dict()
        vue["propose"] = self.propose
        return vue


@dataclass(frozen=True)
class AnalyseSensibilite:
    """Le verdict sous les trois pondérations, et s'il bascule entre elles.

    Un verdict qui dépend du choix de pondération n'est pas une preuve :
    `bascule` le signale explicitement, pour la recommandation et le rapport.
    """

    verdicts: tuple[VerdictPondere, ...]
    bascule: bool
    avis: str

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable de la sensibilité aux pondérations."""
        return {
            "verdicts": [verdict.vers_dict() for verdict in self.verdicts],
            "bascule": self.bascule,
            "avis": self.avis,
        }


def _verdict_sous_ponderation(
    requetes: Sequence[RequetePonderee],
    ponderation: TypePonderation,
    seuils: SeuilsDecision,
    echecs_deterministes: int,
) -> VerdictPondere:
    """Applique les conditions 1 et 2 du §13.4 au taux pondéré."""
    mesure = mesurer_pondere(requetes, ponderation)
    if not mesure.calculable or mesure.intervalle is None:
        return VerdictPondere(ponderation=ponderation, propose=None, mesure=mesure)
    propose = (
        mesure.taux is not None
        and mesure.taux >= seuils.seuil_equivalence
        and mesure.intervalle.bas > seuils.seuil_intervalle_bas
        and echecs_deterministes == 0
    )
    return VerdictPondere(ponderation=ponderation, propose=propose, mesure=mesure)


def _avis_sensibilite(verdicts: tuple[VerdictPondere, ...], bascule: bool) -> str:
    """Phrase de sensibilité : verdict stable, ou dépendance à la pondération."""
    details = ", ".join(
        f"{verdict.ponderation}={'?' if verdict.propose is None else ('oui' if verdict.propose else 'non')}"
        for verdict in verdicts
    )
    if bascule:
        return (
            "Le verdict BASCULE selon la pondération : ce n'est pas une preuve robuste "
            f"({details})."
        )
    return f"Le verdict est identique sous les trois pondérations ({details})."


def analyser_sensibilite(
    requetes: Sequence[RequetePonderee],
    seuils: SeuilsDecision | None = None,
    echecs_deterministes: int = 0,
) -> AnalyseSensibilite:
    """Rend le verdict sous les trois pondérations et signale tout basculement."""
    if not requetes:
        raise ValidationError("aucune requête évaluée : la sensibilité est impossible")
    seuils = seuils or SeuilsDecision()
    verdicts = tuple(
        _verdict_sous_ponderation(requetes, ponderation, seuils, echecs_deterministes)
        for ponderation in PONDERATIONS_SENSIBILITE
    )
    connus = [verdict.propose for verdict in verdicts if verdict.propose is not None]
    bascule = len(set(connus)) > 1
    return AnalyseSensibilite(
        verdicts=verdicts, bascule=bascule, avis=_avis_sensibilite(verdicts, bascule)
    )


def _conforme_statistique(succes: int, n: int, seuils: SeuilsDecision) -> bool:
    """Conditions 1 et 2 du §13.4 sur un compte de succès (sans les contrôles)."""
    intervalle = intervalle_wilson(succes, n)
    return (
        intervalle.taux >= seuils.seuil_equivalence and intervalle.bas > seuils.seuil_intervalle_bas
    )


def nombre_pour_inverser(
    succes: int, n: int, seuils: SeuilsDecision | None = None, echecs_deterministes: int = 0
) -> int | None:
    """Nombre minimal de verdicts du juge à retourner pour inverser la conclusion.

    - Candidat **conforme** (conditions 1 et 2) : combien de verdicts
      « équivalent » devraient devenir « non équivalent » pour casser la
      conformité — la marge de la preuve, utile en vente comme en audit.
    - Candidat **non conforme** : combien de verdicts « non équivalent »
      devraient devenir « équivalent » pour atteindre la conformité.
    - `None` quand aucun retournement de verdict ne peut changer la conclusion :
      échec déterministe critique (condition 3, indépendante du juge) ou seuil
      inatteignable à cet effectif (l'intervalle de Wilson ne peut plus monter
      assez haut).
    """
    if n <= 0:
        raise ValidationError("effectif nul : le nombre pour inverser n'est pas calculable", n=n)
    if not 0 <= succes <= n:
        raise ValidationError("succès hors de [0, n]", succes=succes, n=n)
    seuils = seuils or SeuilsDecision()
    if echecs_deterministes > 0:
        return None  # la condition 3 ne dépend pas des verdicts du juge
    if _conforme_statistique(succes, n, seuils):
        return next(
            (k for k in range(1, succes + 1) if not _conforme_statistique(succes - k, n, seuils)),
            None,
        )
    return next(
        (k for k in range(1, n - succes + 1) if _conforme_statistique(succes + k, n, seuils)),
        None,
    )


@dataclass(frozen=True)
class VerdictEnrichi:
    """Décision du §13.4 **inchangée**, enrichie des lectures pondérée et par strate.

    `decision` est exactement ce que rend `jauge.evaluation.decision.decider` :
    la règle à trois conditions reste intacte. Les blocs ajoutés (qualité
    pondérée, strates, sensibilité, marge d'inversion) **informent** la
    recommandation et le rapport ; ils ne remplacent jamais le verdict.
    """

    modele_id: str
    decision: DecisionCandidat
    qualite: QualitePonderee
    strates: AnalyseStrates
    sensibilite: AnalyseSensibilite
    requis_pour_inverser: int | None
    avis: str

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable complète, prête pour l'Annexe B et le rapport.

        Le verdict du §13.4 (`decision.vers_dict()`) est inclus tel quel ;
        aucun texte de requête ni de réponse n'apparaît (ENF-CONF-01).
        """
        vue = dict(self.decision.vers_dict())
        vue.update(
            {
                "qualite_ponderee": self.qualite.vers_dict(),
                "analyse_strates": self.strates.vers_dict(),
                "sensibilite_ponderations": self.sensibilite.vers_dict(),
                "requis_pour_inverser": self.requis_pour_inverser,
                "avis_enrichi": self.avis,
            }
        )
        return vue


def _pourcentage(valeur: float) -> str:
    """Formate un taux en pourcentage à un dixième (lecture humaine, français)."""
    return f"{valeur * 100:.1f} %".replace(".", ",")


def _detail_regressions(analyse: AnalyseStrates) -> str:
    """Liste motivée des strates en régression, avec leur poids économique."""
    morceaux = [
        f"« {strate.cle} » (taux {_pourcentage(strate.intervalle.taux)}, "
        f"borne basse {_pourcentage(strate.intervalle.bas)}, "
        f"{_pourcentage(strate.part_depense)} de la dépense)"
        for strate in analyse.en_regression()
    ]
    return ", ".join(morceaux)


def _avis_verdict(
    decision: DecisionCandidat,
    analyse: AnalyseStrates,
    sensibilite: AnalyseSensibilite,
    requis: int | None,
) -> str:
    """Avis motivé : agrégat du §13.4, régressions par strate, sensibilité, marge."""
    phrases: list[str] = []
    regressions = analyse.en_regression()
    if decision.propose and regressions:
        phrases.append(
            f"Agrégat conforme MAIS régression sur {len(regressions)} strate(s) représentant "
            f"{_pourcentage(analyse.part_depense_en_regression())} de la dépense : "
            f"{_detail_regressions(analyse)}."
        )
    elif decision.propose:
        phrases.append("Agrégat conforme et aucune strate en régression détectée.")
    else:
        phrases.append(f"Agrégat non conforme ({'; '.join(decision.motifs) or 'voir conditions'}).")
        if regressions:
            phrases.append(
                f"Régression confirmée sur {len(regressions)} strate(s) "
                f"({_pourcentage(analyse.part_depense_en_regression())} de la dépense) : "
                f"{_detail_regressions(analyse)}."
            )
    if non_concluantes := analyse.non_concluantes():
        phrases.append(
            f"{len(non_concluantes)} strate(s) non concluante(s) (effectif < "
            f"{analyse.effectif_minimum}) : ni conformes ni en régression — à ne pas lire comme un feu vert."
        )
    phrases.append(sensibilite.avis)
    if requis is None:
        phrases.append("Aucun retournement de verdict du juge ne peut inverser la conclusion.")
    else:
        sens = "casser la conformité" if decision.propose else "atteindre la conformité"
        phrases.append(
            f"Il faudrait retourner {requis} verdict(s) du juge pour {sens} : c'est la marge de la preuve."
        )
    return " ".join(phrases)


def verdict_enrichi(
    resultat: ResultatCandidat,
    requetes: Sequence[RequetePonderee],
    seuils: SeuilsDecision | None = None,
) -> VerdictEnrichi:
    """Produit le verdict enrichi d'un candidat : §13.4 intact + lectures ajoutées.

    `resultat` vient de `jauge.evaluation.scoring.consolider_resultat` et
    `requetes` doit décrire **la même population évaluée** (mêmes paires,
    jointes à leur strate et à leur dépense via
    `jauge.evaluation.strates.construire_entrees`) : effectif et taux brut sont
    recoupés, toute incohérence est refusée plutôt qu'ignorée en silence.
    """
    seuils = seuils or SeuilsDecision()
    if len(requetes) != resultat.nombre_requetes_rejouees:
        raise ValidationError(
            "Population évaluée différente du résultat candidat.",
            requetes=len(requetes),
            resultat=resultat.nombre_requetes_rejouees,
        )
    qualite = qualite_ponderee(requetes)
    if abs(qualite.taux_brut - resultat.taux_equivalence) > 1e-6:
        raise ValidationError(
            "Taux brut incohérent entre les paires évaluées et le résultat candidat.",
            paires=qualite.taux_brut,
            resultat=resultat.taux_equivalence,
        )
    decision = decider(resultat, seuils)
    analyse = analyser_strates(requetes, seuils)
    sensibilite = analyser_sensibilite(requetes, seuils, resultat.echecs_deterministes)
    requis = nombre_pour_inverser(
        qualite.equivalents, qualite.effectif, seuils, resultat.echecs_deterministes
    )
    return VerdictEnrichi(
        modele_id=resultat.modele_id,
        decision=decision,
        qualite=qualite,
        strates=analyse,
        sensibilite=sensibilite,
        requis_pour_inverser=requis,
        avis=_avis_verdict(decision, analyse, sensibilite, requis),
    )
