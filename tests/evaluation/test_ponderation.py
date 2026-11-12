"""Tests de la qualité pondérée par le coût et du verdict enrichi (Top 10 n° 4).

Cas couverts : pondération exacte vérifiée à la main ; régression par strate
invisible dans la moyenne ; sensibilité du verdict aux pondérations ; nombre
nécessaire pour inverser ; verdict du §13.4 **non remplacé** par l'avis enrichi.
Aucun réseau, aucun AWS, aucun chiffre inventé (ENF-IA-01).
"""

from __future__ import annotations

import json

import pytest

from jauge.common.errors import ValidationError
from jauge.engine.stats import intervalle_wilson
from jauge.evaluation.decision import SeuilsDecision, decider
from jauge.evaluation.judge import VerdictJuge
from jauge.evaluation.ponderation import (
    PONDERATIONS_SENSIBILITE,
    TypePonderation,
    analyser_sensibilite,
    mesurer_pondere,
    nombre_pour_inverser,
    poids_des_requetes,
    qualite_ponderee,
    verdict_enrichi,
)
from jauge.evaluation.scoring import MesuresCoutLatence, PaireEvaluee, consolider_resultat
from jauge.evaluation.strates import RequetePonderee, construire_entrees
from jauge.models.campaign import EchantillonRequete, ResultatCandidat

SEUILS = SeuilsDecision()  # 0.95 / 0.90 (§13.4)
MESURES = MesuresCoutLatence(cout_par_requete_usd=0.0009, latence_p50_ms=410, latence_p95_ms=900)


def entree(
    identifiant: str, equivalent: bool, strate: str = "court:general", cout: float = 0.001
) -> RequetePonderee:
    """Requête évaluée sur l'application de démonstration."""
    return RequetePonderee(
        requete_id=identifiant,
        equivalent=equivalent,
        strate=strate,
        cout_usd=cout,
        application_id="app-1",
        equipe="support",
    )


def petit_jeu() -> list[RequetePonderee]:
    """Jeu à deux strates pour la vérification à la main.

    Strate A : 10 requêtes équivalentes à 1 USD → dépense 10, taux 1,0.
    Strate B : 10 requêtes dont 5 équivalentes à 9 USD → dépense 90, taux 0,5.
    Brut = 15/20 = 0,75 ; dépense = (10×1 + 5×9)/100 = 0,55 ;
    volume = 0,75 ; équipondérée = (1,0 + 0,5)/2 = 0,75.
    """
    requetes = [entree(f"a-{i}", True, "court:general", 1.0) for i in range(10)]
    requetes += [entree(f"b-{i}", i < 5, "long:extraction", 9.0) for i in range(10)]
    return requetes


def jeu_equilibre() -> list[RequetePonderee]:
    """Deux strates de 100 requêtes à 98 % : le verdict est le même partout."""
    requetes = [entree(f"a-{i}", i < 98, "court:general", 0.001) for i in range(100)]
    requetes += [entree(f"b-{i}", i < 98, "long:extraction", 0.010) for i in range(100)]
    return requetes


def jeu_desequilibre() -> list[RequetePonderee]:
    """160 parfaites à 0,001 USD + 40 à 75 % à 0,01 USD : le verdict bascule.

    Volume : 190/200 = 0,950 (borne basse 0,9104) → conforme §13.4.
    Dépense : (0,16 + 0,30)/0,56 ≈ 0,821 → non conforme.
    """
    requetes = [entree(f"q-{i}", True, "court:general", 0.001) for i in range(160)]
    requetes += [entree(f"r-{i}", i < 30, "long:extraction", 0.01) for i in range(40)]
    return requetes


# --- pondérations : poids et mesures -----------------------------------------


def test_poids_des_trois_ponderations() -> None:
    """Volume : 1 partout ; dépense : le coût ; équipondérée : 1/n par strate."""
    requetes = petit_jeu()
    assert poids_des_requetes(requetes, TypePonderation.PAR_VOLUME) == [1.0] * 20
    assert poids_des_requetes(requetes, TypePonderation.PAR_DEPENSE)[0] == 1.0
    assert poids_des_requetes(requetes, TypePonderation.PAR_DEPENSE)[10] == 9.0
    equip = poids_des_requetes(requetes, TypePonderation.EQUIPONDEREE)
    assert equip[0] == pytest.approx(1.0 / 10.0)
    # Chaque strate pèse un poids total de 1, quelle que soit sa taille.
    assert sum(equip[:10]) == pytest.approx(1.0)
    assert sum(equip[10:]) == pytest.approx(1.0)


def test_mesure_par_depense_verifiee_a_la_main() -> None:
    """(10×1 + 5×9)/(10×1 + 10×9) = 55/100 = 0,55 exactement."""
    mesure = mesurer_pondere(petit_jeu(), TypePonderation.PAR_DEPENSE)
    assert mesure.calculable
    assert mesure.taux == pytest.approx(0.55)
    assert mesure.poids_total == pytest.approx(100.0)
    # Effectif effectif de Kish : 100² / (10×1 + 10×81) = 10000/820.
    assert mesure.effectif_effectif == pytest.approx(10000.0 / 820.0)


def test_mesure_par_volume_reproduit_le_brut_a_l_identique() -> None:
    """Pondérer par le volume ne doit pas faire dériver la lecture du §13.4."""
    requetes = jeu_desequilibre()
    mesure = mesurer_pondere(requetes, TypePonderation.PAR_VOLUME)
    brut = intervalle_wilson(190, 200)
    assert mesure.taux == brut.taux == 0.95
    assert mesure.intervalle.bas == brut.bas
    assert mesure.intervalle.haut == brut.haut
    assert mesure.intervalle.n == brut.n


def test_mesure_equiponderee_moyenne_des_strates() -> None:
    """Équipondérée : (1,0 + 0,5)/2 = 0,75 — chaque strate compte pareil."""
    mesure = mesurer_pondere(petit_jeu(), TypePonderation.EQUIPONDEREE)
    assert mesure.taux == pytest.approx(0.75)


def test_mesure_sans_depense_non_calculable() -> None:
    """Aucun coût : la pondération par dépense rend `None`, pas un faux chiffre."""
    requetes = [entree(f"q-{i}", True, cout=0.0) for i in range(40)]
    mesure = mesurer_pondere(requetes, TypePonderation.PAR_DEPENSE)
    assert mesure.calculable is False
    assert mesure.taux is None
    assert mesure.intervalle is None
    with pytest.raises(ValidationError):
        mesurer_pondere([], TypePonderation.PAR_VOLUME)


# --- qualité pondérée : les deux chiffres ensemble ---------------------------


def test_qualite_ponderee_expose_brut_et_pondere() -> None:
    """Les deux chiffres sont rendus ensemble, jamais l'un à la place de l'autre."""
    qualite = qualite_ponderee(petit_jeu())
    assert qualite.effectif == 20
    assert qualite.equivalents == 15
    assert qualite.taux_brut == 0.75
    assert qualite.taux_pondere_depense == pytest.approx(0.55)
    assert qualite.depense_totale_usd == pytest.approx(100.0)
    vue = qualite.vers_dict()
    assert vue["taux_brut"] == 0.75
    assert vue["taux_pondere_depense"] == pytest.approx(0.55)
    assert vue["intervalle_brut"]["bas"] == qualite.intervalle_brut.bas


def test_qualite_ponderee_sans_cout_garde_le_brut() -> None:
    """Sans dépense connue : le brut reste, le pondéré est `None` (honnête)."""
    requetes = [entree(f"q-{i}", i < 38, cout=0.0) for i in range(40)]
    qualite = qualite_ponderee(requetes)
    assert qualite.taux_brut == 0.95
    assert qualite.taux_pondere_depense is None
    with pytest.raises(ValidationError):
        qualite_ponderee([])


# --- sensibilité : le verdict bascule-t-il selon la pondération ? ------------


def test_sensibilite_jeu_equilibre_verdict_identique() -> None:
    """Jeu équilibré (98 % partout) : « oui » sous les trois pondérations."""
    sensibilite = analyser_sensibilite(jeu_equilibre(), SEUILS)
    assert sensibilite.bascule is False
    assert [verdict.propose for verdict in sensibilite.verdicts] == [True, True, True]
    assert "identique" in sensibilite.avis


def test_sensibilite_jeu_desequilibre_verdict_bascule() -> None:
    """Jeu déséquilibré : conforme par volume, non conforme par dépense."""
    sensibilite = analyser_sensibilite(jeu_desequilibre(), SEUILS)
    assert sensibilite.bascule is True
    verdicts = {verdict.ponderation: verdict.propose for verdict in sensibilite.verdicts}
    assert verdicts[TypePonderation.PAR_VOLUME] is True
    assert verdicts[TypePonderation.PAR_DEPENSE] is False
    assert verdicts[TypePonderation.EQUIPONDEREE] is False
    assert "BASCULE" in sensibilite.avis
    assert tuple(v.ponderation for v in sensibilite.verdicts) == PONDERATIONS_SENSIBILITE


def test_sensibilite_ponderation_non_calculable_non_comparee() -> None:
    """Sans coût, la pondération par dépense rend `None` et ne provoque pas de bascule."""
    requetes = [entree(f"q-{i}", i < 98, cout=0.0) for i in range(100)]
    sensibilite = analyser_sensibilite(requetes, SEUILS)
    verdicts = {verdict.ponderation: verdict.propose for verdict in sensibilite.verdicts}
    assert verdicts[TypePonderation.PAR_DEPENSE] is None
    assert sensibilite.bascule is False


def test_sensibilite_echec_deterministe_partout() -> None:
    """Condition 3 (contrôles) inchangée : aucun verdict ne passe avec un échec."""
    sensibilite = analyser_sensibilite(jeu_equilibre(), SEUILS, echecs_deterministes=1)
    assert [verdict.propose for verdict in sensibilite.verdicts] == [False, False, False]
    assert sensibilite.bascule is False


# --- nombre nécessaire pour inverser -----------------------------------------


def test_nombre_pour_inverser_candidat_conforme() -> None:
    """195/200 conforme : 6 verdicts à retourner (189/200 = 94,5 % < 95 %)."""
    assert nombre_pour_inverser(195, 200, SEUILS) == 6
    # Cohérence : à 5 retournements la conclusion tient, à 6 elle casse.
    assert intervalle_wilson(190, 200).taux >= SEUILS.seuil_equivalence
    assert intervalle_wilson(189, 200).taux < SEUILS.seuil_equivalence


def test_nombre_pour_inverser_candidat_non_conforme() -> None:
    """180/200 non conforme : 10 succès de plus atteignent 190/200 = 95 %."""
    assert nombre_pour_inverser(180, 200, SEUILS) == 10


def test_nombre_pour_inverser_impossible() -> None:
    """Échantillon trop petit ou échec déterministe : `None`, jamais un faux nombre."""
    # 10/10 : borne basse 0,72 < 0,90, aucun verdict ne peut l'atteindre.
    assert nombre_pour_inverser(10, 10, SEUILS) is None
    # Échec déterministe critique : la condition 3 ne dépend pas du juge.
    assert nombre_pour_inverser(200, 200, SEUILS, echecs_deterministes=1) is None


def test_nombre_pour_inverser_entrees_invalides() -> None:
    """Effectif nul ou succès hors bornes : refus explicite."""
    with pytest.raises(ValidationError):
        nombre_pour_inverser(0, 0, SEUILS)
    with pytest.raises(ValidationError):
        nombre_pour_inverser(11, 10, SEUILS)


# --- verdict enrichi : le §13.4 intact, l'avis en plus -----------------------


def resultat_et_entrees(requetes: list[RequetePonderee]) -> tuple[ResultatCandidat, list]:
    """Construit le `ResultatCandidat` (scoring) et les entrées jointes du même lot."""
    paires = [
        PaireEvaluee(
            r.requete_id, VerdictJuge(r.equivalent, "SECRET-EXPLICATION-ne-doit-pas-sortir")
        )
        for r in requetes
    ]
    echantillon = [
        EchantillonRequete(
            requete_id=r.requete_id,
            application_id=r.application_id,
            longueur_caracteres=100,
            jetons_entree_origine=40,
            stratum=r.strate,
        )
        for r in requetes
    ]
    couts = {r.requete_id: r.cout_usd for r in requetes}
    intervalle = intervalle_wilson(sum(1 for r in requetes if r.equivalent), len(requetes))
    resultat = ResultatCandidat(
        modele_id="candidat-x",
        nombre_requetes_rejouees=len(requetes),
        taux_equivalence=intervalle.taux,
        intervalle_confiance_bas=intervalle.bas,
        intervalle_confiance_haut=intervalle.haut,
        cout_par_requete_usd=MESURES.cout_par_requete_usd,
    )
    return resultat, construire_entrees(paires, echantillon, couts, equipe="support")


def test_verdict_enrichi_ne_remplace_pas_la_decision() -> None:
    """La décision §13.4 est exactement celle de `decider` ; l'avis s'y ajoute."""
    resultat, entrees = resultat_et_entrees(jeu_desequilibre())
    verdict = verdict_enrichi(resultat, entrees, SEUILS)
    assert verdict.decision == decider(resultat, SEUILS)
    assert verdict.decision.propose is True  # l'agrégat passe le §13.4
    assert "MAIS régression sur 1 strate(s)" in verdict.avis
    assert "71,4 % de la dépense" in verdict.avis
    assert verdict.requis_pour_inverser == 1  # 190/200 → 189/200 casse les 95 %
    assert verdict.sensibilite.bascule is True


def test_verdict_enrichi_chaine_complete_via_scoring() -> None:
    """Chemin réel : `consolider_resultat` (scoring) puis enrichissement."""
    requetes = jeu_desequilibre()
    paires = [PaireEvaluee(r.requete_id, VerdictJuge(r.equivalent, "test")) for r in requetes]
    resultat = consolider_resultat("candidat-x", paires, MESURES, auto_accord_modele_actuel=0.96)
    echantillon = [
        EchantillonRequete(
            requete_id=r.requete_id,
            application_id="app-1",
            longueur_caracteres=100,
            jetons_entree_origine=40,
            stratum=r.strate,
        )
        for r in requetes
    ]
    couts = {r.requete_id: r.cout_usd for r in requetes}
    entrees = construire_entrees(paires, echantillon, couts, equipe="support")
    verdict = verdict_enrichi(resultat, entrees, SEUILS)
    assert verdict.modele_id == "candidat-x"
    assert verdict.qualite.taux_brut == resultat.taux_equivalence == 0.95
    assert verdict.qualite.taux_pondere_depense == pytest.approx(0.46 / 0.56)


def test_verdict_enrichi_serialisable_et_confidentiel() -> None:
    """`vers_dict` passe en JSON, contient les deux taux, aucun texte (ENF-CONF-01)."""
    resultat, entrees = resultat_et_entrees(jeu_desequilibre())
    verdict = verdict_enrichi(resultat, entrees, SEUILS)
    brut = json.dumps(verdict.vers_dict(), ensure_ascii=False)
    vue = json.loads(brut)
    assert vue["propose"] is True  # verdict §13.4 toujours en tête
    assert vue["qualite_ponderee"]["taux_brut"] == 0.95
    assert vue["qualite_ponderee"]["taux_pondere_depense"] == pytest.approx(0.46 / 0.56)
    assert vue["sensibilite_ponderations"]["bascule"] is True
    assert vue["requis_pour_inverser"] == 1
    assert "MAIS régression" in vue["avis_enrichi"]
    assert "SECRET" not in brut


def test_verdict_enrichi_refuse_populations_incoherentes() -> None:
    """Effectif ou taux brut différent du résultat candidat : refus, pas de mix."""
    resultat, entrees = resultat_et_entrees(jeu_desequilibre())
    tronque = resultat.model_copy(update={"nombre_requetes_rejouees": 199})
    with pytest.raises(ValidationError):
        verdict_enrichi(tronque, entrees, SEUILS)
    faux_taux = resultat.model_copy(update={"taux_equivalence": 0.5})
    with pytest.raises(ValidationError):
        verdict_enrichi(faux_taux, entrees, SEUILS)


def test_verdict_enrichi_agregat_sain_avis_complet() -> None:
    """Jeu équilibré conforme : avis sans régression, sensibilité stable, marge chiffrée."""
    resultat, entrees = resultat_et_entrees(jeu_equilibre())
    verdict = verdict_enrichi(resultat, entrees, SEUILS)
    assert verdict.decision.propose is True
    assert verdict.strates.en_regression() == []
    assert verdict.sensibilite.bascule is False
    assert "aucune strate en régression" in verdict.avis
    assert verdict.requis_pour_inverser is not None
    # La marge annoncée est cohérente : à k-1 retournements, la conformité tient.
    k = verdict.requis_pour_inverser
    assert intervalle_wilson(196 - (k - 1), 200).taux >= SEUILS.seuil_equivalence
    assert intervalle_wilson(196 - k, 200).taux < SEUILS.seuil_equivalence
