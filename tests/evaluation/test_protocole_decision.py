"""Tests de la marge de non-infériorité dans la règle de décision (piste n° 1).

Ce fichier couvre l'effet du protocole pré-enregistré sur le verdict :

- sans protocole, la règle du §13.4 s'applique **exactement** comme avant
  (trois conditions, aucune marge, vue de décision inchangée) ;
- avec un protocole scellé, la marge de non-infériorité s'**ajoute** aux trois
  conditions, et le verdict dit explicitement « non-inférieur dans la marge de
  X points » ou « inférieur » ;
- un protocole dérivé, non scellé, ou des seuils présentés en contradiction avec
  ceux engagés font **refuser** la décision : jamais de verdict silencieux sur
  une méthode retouchée.

Aucun réseau, aucun AWS, aucun modèle : les taux viennent de
`jauge.engine.stats.intervalle_wilson` sur des comptes fictifs.
"""

from __future__ import annotations

import json

import pytest

from jauge.common.errors import EtatIncompatibleError
from jauge.engine.stats import intervalle_wilson
from jauge.evaluation.decision import (
    Qualification,
    SeuilsDecision,
    condition_non_inferiorite,
    decider,
    decider_lot,
    lecture_non_inferiorite,
    plancher_non_inferiorite,
)
from jauge.evaluation.protocole import (
    PlanEchantillonnage,
    ProtocoleEvaluation,
    construire_protocole,
    sceller,
)
from jauge.models.campaign import ResultatCandidat
from jauge.models.tenant import Application

CANDIDAT = "candidat-petit"
JUGE = "modele-juge-de-demo"
CONDITIONS_DU_13_4 = ["taux_equivalence", "intervalle_confiance_bas", "controles_deterministes"]


def resultat(
    total: int,
    equivalents: int,
    *,
    auto_accord: float = 1.0,
    echecs: int = 0,
    modele_id: str = CANDIDAT,
) -> ResultatCandidat:
    """Résultat consolidé fictif : l'intervalle vient du calcul du socle."""
    intervalle = intervalle_wilson(equivalents, total)
    return ResultatCandidat(
        modele_id=modele_id,
        nombre_requetes_rejouees=total,
        taux_equivalence=intervalle.taux,
        intervalle_confiance_bas=intervalle.bas,
        intervalle_confiance_haut=intervalle.haut,
        auto_accord_modele_actuel=auto_accord,
        echecs_deterministes=echecs,
        cout_par_requete_usd=0.0009,
    )


def protocole_scelle(app: Application, *, marge: float = 0.05) -> ProtocoleEvaluation:
    """Protocole scellé de référence : marge donnée, seuils de l'application."""
    return sceller(
        construire_protocole(
            application=app,
            modeles_candidats=[CANDIDAT],
            modele_juge_id=JUGE,
            plan=PlanEchantillonnage(taille=200, graines=[7]),
            marge_non_inferiorite=marge,
            controles_deterministes=["controle_json_valide"],
        )
    )


def forger(scelle: ProtocoleEvaluation, **champs: object) -> ProtocoleEvaluation:
    """Protocole retouché après scellement : le sceau ne correspond plus au contenu."""
    donnees = scelle.model_dump(mode="json")
    donnees.update(champs)
    return ProtocoleEvaluation.model_validate(donnees)


# --- Règle existante inchangée ----------------------------------------------


def test_sans_protocole_la_regle_du_13_4_s_applique_seule() -> None:
    """Trois conditions, aucune marge : le comportement historique est intact."""
    decision = decider(resultat(200, 195))
    assert [condition.nom for condition in decision.conditions] == CONDITIONS_DU_13_4
    assert decision.propose is True
    assert decision.marge_non_inferiorite is None
    assert decision.non_inferiorite is None
    assert decision.lecture_non_inferiorite == ""
    assert set(decision.vers_dict()) == {
        "modele_id",
        "propose",
        "qualification",
        "conditions",
        "motifs",
        "lecture_relative",
    }


def test_sans_protocole_un_candidat_sous_l_auto_accord_passe_encore() -> None:
    """Sans engagement de marge, la règle ne bloque pas un écart à l'auto-accord."""
    decision = decider(resultat(200, 195, auto_accord=1.0))
    assert decision.propose is True


def test_avec_protocole_une_quatrieme_condition_apparait(app_classement: Application) -> None:
    """La marge s'ajoute aux trois conditions du §13.4, elle ne les remplace pas."""
    decision = decider(resultat(200, 195), protocole=protocole_scelle(app_classement))
    noms = [condition.nom for condition in decision.conditions]
    assert noms == [*CONDITIONS_DU_13_4, "non_inferiorite"]
    assert all(condition.remplie for condition in decision.conditions)
    assert decision.propose is True
    assert decision.marge_non_inferiorite == pytest.approx(0.05)
    assert decision.non_inferiorite is True


# --- Marge de non-infériorité ----------------------------------------------


def test_marge_respectee_verdict_explicite(app_classement: Application) -> None:
    """95 % face à un auto-accord de 100 % avec une marge de 5 points : non-inférieur."""
    decision = decider(
        resultat(200, 190, auto_accord=1.0), protocole=protocole_scelle(app_classement, marge=0.05)
    )
    assert decision.propose is True
    assert decision.non_inferiorite is True
    assert decision.lecture_non_inferiorite.startswith("non-inférieur dans la marge de 5.0 points")
    assert "5.0 point(s)" in decision.lecture_non_inferiorite


def test_marge_depassee_candidat_inferieur(app_classement: Application) -> None:
    """95 % avec une marge de 2 points : inférieur, et c'est le seul motif de rejet."""
    decision = decider(
        resultat(200, 190, auto_accord=1.0), protocole=protocole_scelle(app_classement, marge=0.02)
    )
    assert decision.propose is False
    assert decision.non_inferiorite is False
    assert [motif.split(" :")[0] for motif in decision.motifs] == ["non_inferiorite"]
    assert decision.lecture_non_inferiorite.startswith("inférieur :")
    assert "marge pré-engagée de 2.0 points" in decision.lecture_non_inferiorite


def test_marge_nulle_exige_au_moins_l_auto_accord(app_classement: Application) -> None:
    """Une marge de zéro point n'accepte aucune dégradation sous l'auto-accord."""
    stricte = protocole_scelle(app_classement, marge=0.0)
    assert decider(resultat(200, 200, auto_accord=1.0), protocole=stricte).propose is True
    decision = decider(resultat(200, 195, auto_accord=1.0), protocole=stricte)
    assert decision.propose is False
    assert decision.non_inferiorite is False


def test_plancher_et_condition_sont_coherents() -> None:
    """Le plancher annoncé est bien celui que la condition applique."""
    mesure = resultat(200, 190, auto_accord=0.98)
    assert plancher_non_inferiorite(mesure, 0.05) == pytest.approx(0.93)
    assert condition_non_inferiorite(mesure, 0.05).remplie is True
    assert condition_non_inferiorite(mesure, 0.02).remplie is False


def test_lecture_explicite_dans_les_deux_sens() -> None:
    """La phrase de verdict dit « non-inférieur » ou « inférieur », jamais rien d'implicite."""
    mesure = resultat(200, 190, auto_accord=1.0)
    assert lecture_non_inferiorite(mesure, 0.05).startswith("non-inférieur dans la marge de")
    assert lecture_non_inferiorite(mesure, 0.01).startswith("inférieur :")


def test_les_trois_conditions_du_13_4_restent_appliquees(app_classement: Application) -> None:
    """Une marge confortable ne rachète pas un taux sous le seuil de l'application."""
    stricte = app_classement.model_copy(update={"seuil_qualite": 0.99})
    decision = decider(
        resultat(200, 195, auto_accord=0.975),
        protocole=protocole_scelle(stricte, marge=0.5),
    )
    assert decision.non_inferiorite is True
    assert decision.propose is False
    assert [motif.split(" :")[0] for motif in decision.motifs] == ["taux_equivalence"]


def test_marge_et_autres_motifs_se_cumulent(app_classement: Application) -> None:
    """Les motifs s'additionnent dans l'ordre des conditions, marge en dernier."""
    decision = decider(
        resultat(200, 180, auto_accord=1.0, echecs=1),
        protocole=protocole_scelle(app_classement, marge=0.02),
    )
    assert decision.propose is False
    assert [motif.split(" :")[0] for motif in decision.motifs] == [
        "taux_equivalence",
        "intervalle_confiance_bas",
        "controles_deterministes",
        "non_inferiorite",
    ]


def test_qualification_relative_inchangee(app_classement: Application) -> None:
    """La lecture relative du §13.4 continue de qualifier le taux face à l'auto-accord."""
    decision = decider(
        resultat(200, 190, auto_accord=0.96), protocole=protocole_scelle(app_classement, marge=0.05)
    )
    assert decision.qualification is Qualification.EXCELLENT
    assert "95.0 %" in decision.lecture_relative
    assert decision.propose is True


# --- Décision refusée sur protocole en dérive -------------------------------


def test_decision_refusee_sur_protocole_derive(app_classement: Application) -> None:
    """Une marge élargie après coup ne produit aucun verdict : la décision est refusée."""
    scelle = protocole_scelle(app_classement, marge=0.02)
    falsifie = forger(scelle, marge_non_inferiorite=0.9)
    with pytest.raises(EtatIncompatibleError) as erreur:
        decider(resultat(200, 190, auto_accord=1.0), protocole=falsifie)
    assert erreur.value.details["empreinte_attendue"] == scelle.empreinte


def test_decision_refusee_sur_seuils_derives(app_classement: Application) -> None:
    """Abaisser les seuils engagés après le rejeu est refusé au moment de conclure."""
    scelle = protocole_scelle(app_classement)
    falsifie = forger(scelle, seuils={"seuil_equivalence": 0.5, "borne_basse_intervalle": 0.4})
    with pytest.raises(EtatIncompatibleError):
        decider(resultat(200, 190), protocole=falsifie)


def test_decision_refusee_sur_protocole_non_scelle(app_classement: Application) -> None:
    """Un protocole encore à l'état de brouillon n'engage personne : refus explicite."""
    brouillon = construire_protocole(
        application=app_classement, modeles_candidats=[CANDIDAT], modele_juge_id=JUGE
    )
    with pytest.raises(EtatIncompatibleError):
        decider(resultat(200, 195), protocole=brouillon)


def test_decision_refusee_sur_lot_derive(app_classement: Application) -> None:
    """Le refus vaut pour le lot entier : aucune décision partielle n'est rendue."""
    falsifie = forger(protocole_scelle(app_classement), prompt_juge_version="9.9")
    with pytest.raises(EtatIncompatibleError):
        decider_lot([resultat(200, 195), resultat(200, 190)], protocole=falsifie)


# --- Seuils engagés ---------------------------------------------------------


def test_seuils_du_protocole_s_imposent(app_classement: Application) -> None:
    """Un seuil plus exigeant engagé avant le rejeu s'applique au verdict."""
    stricte = app_classement.model_copy(update={"seuil_qualite": 0.99})
    decision = decider(
        resultat(200, 195, auto_accord=0.975), protocole=protocole_scelle(stricte, marge=0.05)
    )
    assert decision.propose is False
    assert [motif.split(" :")[0] for motif in decision.motifs] == ["taux_equivalence"]


def test_seuils_presentes_coherents_acceptes(app_classement: Application) -> None:
    """Présenter explicitement les seuils engagés ne change rien au verdict."""
    scelle = protocole_scelle(app_classement)
    seuils = SeuilsDecision(
        seuil_equivalence=app_classement.seuil_qualite,
        seuil_intervalle_bas=app_classement.seuil_intervalle_bas,
    )
    decision = decider(resultat(200, 195), seuils, protocole=scelle)
    assert decision.propose is True


def test_seuils_presentes_contradictoires_refuses(app_classement: Application) -> None:
    """Des seuils différents de ceux du protocole scellé sont une dérive : refus."""
    scelle = protocole_scelle(app_classement)
    with pytest.raises(EtatIncompatibleError) as erreur:
        decider(resultat(200, 195), SeuilsDecision(seuil_equivalence=0.6), protocole=scelle)
    assert erreur.value.details["empreinte_protocole"] == scelle.empreinte


def test_lot_sous_protocole_partage_la_meme_methode(app_classement: Application) -> None:
    """Tout le lot est jugé sous le même protocole scellé (pas de méthode par candidat)."""
    scelle = protocole_scelle(app_classement, marge=0.05)
    decisions = decider_lot(
        [
            resultat(200, 195, auto_accord=0.98, modele_id="candidat-moyen"),
            resultat(200, 190, auto_accord=0.98),
        ],
        protocole=scelle,
    )
    assert len(decisions) == 2
    assert {decision.marge_non_inferiorite for decision in decisions} == {0.05}
    assert all(decision.propose for decision in decisions)
    assert all(decision.non_inferiorite is True for decision in decisions)


def test_aucun_texte_dans_la_decision_sous_protocole(app_classement: Application) -> None:
    """ENF-CONF-01 : la vue de décision sous protocole ne porte aucun texte de requête."""
    decision = decider(
        resultat(200, 190, auto_accord=1.0), protocole=protocole_scelle(app_classement, marge=0.05)
    )
    vue = decision.vers_dict()
    assert vue["non_inferiorite"] is True
    brut = json.dumps(vue, ensure_ascii=False)
    assert "SECRET" not in brut
    assert "texte" not in brut
