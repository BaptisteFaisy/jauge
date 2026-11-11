"""Tests de la règle de décision (§13.4) : trois conditions, rejet motivé,
lecture du taux à côté de l'auto-accord du modèle actuel.
"""

from __future__ import annotations

import pytest

from jauge.common.errors import ValidationError
from jauge.evaluation.decision import (
    Qualification,
    SeuilsDecision,
    candidats_proposes,
    decider,
    decider_lot,
    qualifier,
)
from jauge.evaluation.judge import VerdictJuge
from jauge.evaluation.scoring import MesuresCoutLatence, PaireEvaluee, consolider_resultat

MESURES = MesuresCoutLatence(cout_par_requete_usd=0.0009)


def resultat(n: int, equivalents: int, auto_accord: float = 1.0, modele_id: str = "petit-modele"):
    """Construit un `ResultatCandidat` à partir d'un nombre d'équivalences."""
    paires = [
        PaireEvaluee(requete_id=f"q-{index}", verdict=VerdictJuge(equivalent=index < equivalents))
        for index in range(n)
    ]
    return consolider_resultat(
        modele_id=modele_id,
        paires=paires,
        mesures=MESURES,
        auto_accord_modele_actuel=auto_accord,
    )


def test_candidat_conforme_est_propose() -> None:
    """195/200 (97,5 %, borne basse 94,3 %), aucun échec : les trois conditions tiennent."""
    decision = decider(resultat(200, 195))
    assert decision.propose is True
    assert decision.motifs == ()
    assert all(condition.remplie for condition in decision.conditions)
    assert [condition.nom for condition in decision.conditions] == [
        "taux_equivalence",
        "intervalle_confiance_bas",
        "controles_deterministes",
    ]


def test_taux_sous_le_seuil_est_rejete_et_motive() -> None:
    """Condition 1 : un taux de 90 % n'atteint pas le seuil de 95 %."""
    decision = decider(resultat(200, 180))
    assert decision.propose is False
    assert any(motif.startswith("taux_equivalence") for motif in decision.motifs)
    assert any(motif.startswith("intervalle_confiance_bas") for motif in decision.motifs)


def test_borne_basse_sous_90_pourcent_est_rejetee() -> None:
    """Condition 2 : 95 % sur 40 requêtes ne suffit pas, la borne basse tombe à 83 %."""
    decision = decider(resultat(40, 38))
    assert decision.resultat is not None
    assert decision.resultat.taux_equivalence == pytest.approx(0.95)
    assert decision.propose is False
    assert [motif.split(" :")[0] for motif in decision.motifs] == ["intervalle_confiance_bas"]


def test_echec_deterministe_critique_est_rejete() -> None:
    """Condition 3 : un contrôle critique en échec bloque, même à 100 % d'équivalence."""
    resultat_parfait = resultat(200, 200).model_copy(update={"echecs_deterministes": 2})
    decision = decider(resultat_parfait)
    assert decision.propose is False
    assert [motif.split(" :")[0] for motif in decision.motifs] == ["controles_deterministes"]
    assert "2 échec(s)" in decision.motifs[0]


def test_seuils_de_l_application_sont_respectes() -> None:
    """Les seuils viennent de l'application (`seuil_qualite`, `seuil_intervalle_bas`)."""
    strict = SeuilsDecision(seuil_equivalence=0.99, seuil_intervalle_bas=0.95)
    decision = decider(resultat(200, 195), strict)
    assert decision.propose is False
    assert len(decision.motifs) == 2


def test_seuils_depuis_application(app_classement) -> None:
    """`SeuilsDecision.depuis_application` lit les seuils de l'application (§13.4)."""
    seuils = SeuilsDecision.depuis_application(app_classement)
    assert seuils.seuil_equivalence == pytest.approx(0.95)
    assert seuils.seuil_intervalle_bas == pytest.approx(0.90)


def test_cas_excellent_a_95_pourcent() -> None:
    """§13.4 : modèle actuel auto-équivalent à 96 %, candidat à 95 % → excellent."""
    decision = decider(resultat(200, 190, auto_accord=0.96))
    assert decision.resultat is not None
    assert decision.resultat.taux_equivalence == pytest.approx(0.95)
    assert decision.propose is True
    assert decision.qualification is Qualification.EXCELLENT
    assert "95.0 %" in decision.lecture_relative
    assert "96.0 %" in decision.lecture_relative


def test_candidat_loin_de_l_auto_accord_n_est_plus_excellent() -> None:
    """À 96 % face à un auto-accord de 100 %, le candidat n'est plus « excellent »."""
    decision = decider(resultat(200, 192, auto_accord=1.0))
    assert decision.propose is True
    assert decision.qualification is Qualification.ACCEPTABLE


def test_qualifier_insuffisant() -> None:
    """Un écart de plus de 10 points à l'auto-accord est insuffisant."""
    assert qualifier(resultat(200, 170, auto_accord=1.0)) is Qualification.INSUFFISANT


def test_modele_actuel_n_est_jamais_propose() -> None:
    """La référence de la campagne n'est pas un candidat de bascule."""
    reference = resultat(200, 200, modele_id="grand-modele").model_copy(
        update={"est_reference": True}
    )
    decision = decider(reference)
    assert decision.propose is False
    assert decision.motifs[0].startswith("modele_actuel")


def test_seuils_invalides_refuses() -> None:
    """Un seuil hors de ]0, 1] est une erreur de configuration."""
    with pytest.raises(ValidationError):
        SeuilsDecision(seuil_equivalence=0.0)
    with pytest.raises(ValidationError):
        SeuilsDecision(seuil_intervalle_bas=1.5)


def test_lot_de_candidats_et_selection_des_proposes() -> None:
    """Le lot rend une décision par candidat, et la sélection trie par taux."""
    decisions = decider_lot(
        [
            resultat(200, 190, modele_id="moyen-modele"),
            resultat(200, 197, modele_id="petit-modele"),
            resultat(200, 170, modele_id="minuscule-modele"),
        ]
    )
    proposes = candidats_proposes(decisions)
    assert [decision.modele_id for decision in proposes] == ["petit-modele", "moyen-modele"]


def test_lot_vide_refuse() -> None:
    """Décider sur zéro candidat n'a pas de sens : refus explicite."""
    with pytest.raises(ValidationError):
        decider_lot([])
