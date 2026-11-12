"""Tests de l'analyse par strate (Top 10 n° 4 — §13.1, §13.6).

Le cas central : une strate minoritaire en régression doit être détectée
**alors que l'agrégat passe** le §13.4 — c'est exactement la limite déclarée
au §13.6 (« un échantillon de 200 requêtes ne voit pas les cas rares »).
Les valeurs attendues sont calculées à la main, aucun chiffre inventé.
"""

from __future__ import annotations

import json

import pytest

from jauge.common.errors import ValidationError
from jauge.evaluation.decision import SeuilsDecision
from jauge.evaluation.judge import VerdictJuge
from jauge.evaluation.scoring import PaireEvaluee
from jauge.evaluation.strates import (
    EFFECTIF_MINIMUM_STRATE,
    RequetePonderee,
    StatutStrate,
    analyser_strates,
    cle_strate,
    construire_entrees,
)
from jauge.models.campaign import EchantillonRequete

SEUILS = SeuilsDecision()  # 0.95 / 0.90 (§13.4)


def entree(
    identifiant: str,
    equivalent: bool,
    strate: str = "court:general",
    cout: float = 0.001,
    application: str = "app-1",
    equipe: str = "support",
) -> RequetePonderee:
    """Construit une requête évaluée (verdict déjà rendu par le juge)."""
    return RequetePonderee(
        requete_id=identifiant,
        equivalent=equivalent,
        strate=strate,
        cout_usd=cout,
        application_id=application,
        equipe=equipe,
    )


def population_minoritaire() -> list[RequetePonderee]:
    """160 requêtes parfaites peu coûteuses + 40 requêtes chères à 75 %.

    Agrégat : 190/200 = 95,0 % (borne basse 0,9104 > 0,90) → le §13.4 passe.
    Strate « long:extraction » : 30/40 = 75 %, borne basse ≈ 0,598 → régression.
    Dépense : 0,16 USD d'un côté, 0,40 USD de l'autre → la strate en régression
    porte 0,40/0,56 ≈ 71,4 % de la facture.
    """
    requetes = [entree(f"q-{index}", True, "court:general", 0.001) for index in range(160)]
    requetes += [entree(f"r-{index}", index < 30, "long:extraction", 0.01) for index in range(40)]
    return requetes


# --- construire_entrees : jointure verdicts × échantillon × coûts ------------


def test_construire_entrees_joint_strate_et_cout() -> None:
    """La strate vient de l'échantillon, le coût de l'attribution, le verdict du juge."""
    paires = [
        PaireEvaluee("q-1", VerdictJuge(True, "ok"), famille="general"),
        PaireEvaluee("q-2", VerdictJuge(False, "ko"), famille="general"),
    ]
    echantillon = [
        EchantillonRequete(
            requete_id="q-1",
            application_id="app-1",
            type_requete="extraction",
            longueur_caracteres=2500,
            jetons_entree_origine=600,
            stratum="long:extraction",
        ),
        EchantillonRequete(
            requete_id="q-2",
            application_id="app-1",
            type_requete="general",
            longueur_caracteres=120,
            jetons_entree_origine=40,
            stratum="court:general",
        ),
    ]
    entrees = construire_entrees(paires, echantillon, {"q-1": 0.02}, equipe="support")
    assert [e.requete_id for e in entrees] == ["q-1", "q-2"]
    assert entrees[0].strate == "long:extraction"
    assert entrees[0].cout_usd == 0.02
    assert entrees[0].equivalent is True
    assert entrees[1].cout_usd == 0.0  # coût absent : 0, jamais inventé
    assert entrees[1].application_id == "app-1"
    assert entrees[1].equipe == "support"


def test_construire_entrees_refuse_verdict_hors_echantillon() -> None:
    """Un verdict non attribuable à une strate ne peut pas entrer dans la preuve."""
    paires = [PaireEvaluee("q-inconnue", VerdictJuge(True, "ok"))]
    with pytest.raises(ValidationError):
        construire_entrees(paires, [], {})


def test_construire_entrees_refuse_liste_vide() -> None:
    """Aucune paire : refus explicite, pas d'analyse vide silencieuse."""
    with pytest.raises(ValidationError):
        construire_entrees([], [], {})


def test_requete_ponderee_valide_ses_entrees() -> None:
    """Coût négatif ou strate absente : refus (ENF-IA-01, données saines)."""
    with pytest.raises(ValidationError):
        entree("q-1", True, cout=-0.5)
    with pytest.raises(ValidationError):
        RequetePonderee(requete_id="q-1", equivalent=True, strate="")


def test_cle_strate_inclut_application_et_equipe() -> None:
    """La clé croise strate × application × équipe quand elles sont disponibles."""
    assert cle_strate(entree("q-1", True)) == "court:general | app-1 | support"
    sans_equipe = entree("q-2", True, equipe="")
    assert cle_strate(sans_equipe) == "court:general | app-1"


# --- analyser_strates : taux, effectif, Wilson, part de dépense --------------


def test_analyse_par_strate_valeurs_main() -> None:
    """Chaque strate rend taux, effectif, Wilson et part de dépense exacts."""
    analyse = analyser_strates(population_minoritaire(), SEUILS)
    assert analyse.effectif_total == 200
    assert analyse.equivalents_total == 190
    assert analyse.depense_totale_usd == pytest.approx(0.56)
    assert len(analyse.strates) == 2
    par_cle = {strate.strate: strate for strate in analyse.strates}
    courte = par_cle["court:general"]
    assert courte.effectif == 160
    assert courte.equivalents == 160
    assert courte.intervalle.taux == 1.0
    assert courte.cout_usd == pytest.approx(0.16)
    assert courte.part_depense == pytest.approx(0.16 / 0.56)
    longue = par_cle["long:extraction"]
    assert longue.effectif == 40
    assert longue.intervalle.taux == 0.75
    assert longue.part_depense == pytest.approx(0.40 / 0.56)


def test_regression_minoritaire_invisible_dans_l_agregat() -> None:
    """Le §13.6 en action : l'agrégat tient, la strate chère s'effondre."""
    analyse = analyser_strates(population_minoritaire(), SEUILS)
    regressions = analyse.en_regression()
    assert [strate.strate for strate in regressions] == ["long:extraction"]
    # Borne basse de Wilson de la strate : 30/40 ≈ 0,598 < 0,90.
    assert regressions[0].intervalle.bas == pytest.approx(0.598, abs=0.002)
    assert regressions[0].intervalle.bas < SEUILS.seuil_intervalle_bas
    # Poids économique de la régression : 0,40 / 0,56.
    assert analyse.part_depense_en_regression() == pytest.approx(0.40 / 0.56)
    # L'agrégat, lui, reste au-dessus des deux seuils du §13.4.
    taux_agrege = analyse.equivalents_total / analyse.effectif_total
    assert taux_agrege >= SEUILS.seuil_equivalence


def test_wilson_par_strate_borne_dans_zero_un() -> None:
    """Intervalles par strate toujours dans [0, 1], y compris aux extrêmes."""
    requetes = (
        [entree(f"a-{i}", True, "court:general") for i in range(40)]
        + [entree(f"b-{i}", False, "long:extraction") for i in range(40)]
        + [entree(f"c-{i}", i < 20, "moyen:resume") for i in range(40)]
    )
    for strate in analyser_strates(requetes, SEUILS).strates:
        assert 0.0 <= strate.intervalle.bas <= strate.intervalle.haut <= 1.0
        assert 0.0 <= strate.intervalle.taux <= 1.0


def test_strate_sans_effectif_suffisant_non_concluante() -> None:
    """10 requêtes < 30 : « non concluante », jamais « conforme » ni « régression »."""
    requetes = [entree(f"q-{i}", True, "court:general") for i in range(60)]
    # Strate parfaite mais minuscule, et strate catastrophique mais minuscule.
    requetes += [entree(f"parfaite-{i}", True, "moyen:rare") for i in range(10)]
    requetes += [entree(f"ratee-{i}", False, "long:rare") for i in range(10)]
    analyse = analyser_strates(requetes, SEUILS)
    statuts = {strate.strate: strate.statut for strate in analyse.strates}
    assert statuts["court:general"] is StatutStrate.CONFORME
    assert statuts["moyen:rare"] is StatutStrate.NON_CONCLUANTE
    assert statuts["long:rare"] is StatutStrate.NON_CONCLUANTE
    # Une strate non concluante n'est jamais une régression déclarée.
    assert analyse.en_regression() == []
    assert {s.strate for s in analyse.non_concluantes()} == {"moyen:rare", "long:rare"}
    assert analyse.effectif_minimum == EFFECTIF_MINIMUM_STRATE


def test_sans_donnee_de_cout_parts_de_depense_nulles_mais_taux_intacts() -> None:
    """Aucune dépense connue : parts à 0.0 (pas de chiffre inventé), taux exacts."""
    requetes = [entree(f"q-{i}", i < 30, "court:general", cout=0.0) for i in range(40)]
    analyse = analyser_strates(requetes, SEUILS)
    assert analyse.depense_totale_usd == 0.0
    assert analyse.strates[0].part_depense == 0.0
    assert analyse.strates[0].intervalle.taux == 0.75


def test_analyse_refuse_entrees_invalides() -> None:
    """Population vide ou effectif minimal absurde : refus explicite."""
    with pytest.raises(ValidationError):
        analyser_strates([])
    with pytest.raises(ValidationError):
        analyser_strates([entree("q-1", True)], effectif_minimum=0)


def test_analyse_serialisable_sans_texte() -> None:
    """`vers_dict` passe en JSON et ne laisse fuir aucun texte (ENF-CONF-01)."""
    # Des explications du juge sentinelles : si l'une apparaît, le test échoue.
    paires = [
        PaireEvaluee(
            f"q-{index}",
            VerdictJuge(index < 190, "SECRET-EXPLICATION-ne-doit-pas-sortir"),
        )
        for index in range(200)
    ]
    echantillon = [
        EchantillonRequete(
            requete_id=paire.requete_id,
            application_id="app-1",
            longueur_caracteres=100 if index < 160 else 2500,
            jetons_entree_origine=40,
            stratum="court:general" if index < 160 else "long:extraction",
        )
        for index, paire in enumerate(paires)
    ]
    couts = {
        paire.requete_id: (0.001 if index < 160 else 0.01) for index, paire in enumerate(paires)
    }
    entrees = construire_entrees(paires, echantillon, couts, equipe="support")
    analyse = analyser_strates(entrees, SEUILS)
    vue = analyse.vers_dict()
    brut = json.dumps(vue, ensure_ascii=False)
    assert "SECRET" not in brut
    assert "long:extraction | app-1 | support" in vue["strates_en_regression"]
    assert vue["part_depense_en_regression"] == pytest.approx(0.40 / 0.56)
