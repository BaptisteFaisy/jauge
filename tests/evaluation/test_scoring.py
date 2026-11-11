"""Tests des mesures : taux, intervalle, auto-accord, familles, accord juge/humain.

Les valeurs attendues viennent de l'Annexe B et du §13.4 (195/200 → borne basse
au-dessus de 90 %).
"""

from __future__ import annotations

import pytest

from jauge.common.errors import ValidationError
from jauge.evaluation.checks import executer_controles
from jauge.evaluation.judge import VerdictJuge
from jauge.evaluation.scoring import (
    MesuresCoutLatence,
    PaireEvaluee,
    accord_juge_humain,
    auto_accord,
    consolider_resultat,
    familles_en_echec,
    nombre_echecs_deterministes,
    taux_equivalence,
    taux_par_famille,
)

MESURES = MesuresCoutLatence(cout_par_requete_usd=0.0009, latence_p50_ms=410, latence_p95_ms=900)


def paires(n: int, equivalents: int, famille: str = "general") -> list[PaireEvaluee]:
    """Construit `n` paires dont `equivalents` jugées équivalentes."""
    return [
        PaireEvaluee(
            requete_id=f"q-{famille}-{index}",
            verdict=VerdictJuge(equivalent=index < equivalents, explication="test"),
            famille=famille,
        )
        for index in range(n)
    ]


def test_taux_195_sur_200_borne_basse_annexe_b() -> None:
    """195/200 → 97,5 % et borne basse ≈ 0,943 (§13.4, Annexe B)."""
    intervalle = taux_equivalence(paires(200, 195))
    assert intervalle.taux == pytest.approx(0.975)
    assert intervalle.bas == pytest.approx(0.943, abs=0.005)
    assert intervalle.bas > 0.90
    assert intervalle.n == 200


def test_taux_sur_echantillon_vide_refuse() -> None:
    """Un taux sans donnée n'est pas calculable : refus explicite."""
    with pytest.raises(ValidationError):
        taux_equivalence([])


def test_taux_par_famille_et_familles_en_echec() -> None:
    """Les familles sous le seuil sont nommées (§13.6)."""
    echantillon = paires(50, 50, "francais") + paires(20, 10, "langue-rare")
    taux = taux_par_famille(echantillon)
    assert set(taux) == {"francais", "langue-rare"}
    assert taux["francais"].taux == 1.0
    assert familles_en_echec(echantillon) == ["langue-rare"]


def test_familles_en_echec_vide_quand_tout_passe() -> None:
    """Aucune famille sous le seuil : liste vide, pas de risque inventé."""
    assert familles_en_echec(paires(40, 40, "francais")) == []


def test_auto_accord_du_modele_actuel() -> None:
    """EF-EVA-04 : deux exécutions du modèle actuel ne concordent pas toujours."""
    deux_passages = paires(200, 192)
    resultat = auto_accord(deux_passages)
    assert resultat.taux == pytest.approx(0.96)
    assert resultat.n == 200


def test_accord_juge_humain() -> None:
    """EF-EVA-05 : accord brut sur 30 paires relues, une divergence."""
    verdicts_juge = [VerdictJuge(equivalent=True) for _ in range(30)]
    verdicts_juge[0] = VerdictJuge(equivalent=False)
    verdicts_humain = [True] * 30
    assert accord_juge_humain(verdicts_juge, verdicts_humain) == pytest.approx(29 / 30)


def test_accord_juge_humain_refuse_les_tailles_differentes() -> None:
    """Comparer des listes désalignées produirait un accord faux."""
    with pytest.raises(ValidationError):
        accord_juge_humain([VerdictJuge(equivalent=True)] * 3, [True, True])
    with pytest.raises(ValidationError):
        accord_juge_humain([], [])


def test_nombre_echecs_deterministes_compte_les_paires_critiques() -> None:
    """Une paire avec un contrôle critique en échec compte pour un échec (§13.4)."""
    reference = '{"etiquette": "acces"}'
    en_echec = PaireEvaluee(
        requete_id="q-ko",
        verdict=VerdictJuge(equivalent=True),
        controles=tuple(executer_controles("classification", reference, "pas du json {")),
    )
    conforme = PaireEvaluee(
        requete_id="q-ok",
        verdict=VerdictJuge(equivalent=True),
        controles=tuple(executer_controles("classification", reference, reference)),
    )
    assert nombre_echecs_deterministes([en_echec, conforme]) == 1


def test_consolider_resultat_produit_l_entite_du_socle() -> None:
    """Le résultat est un `ResultatCandidat` complet, sans aucun texte (ENF-CONF-01)."""
    resultat = consolider_resultat(
        modele_id="petit-modele",
        paires=paires(200, 195),
        mesures=MESURES,
        auto_accord_modele_actuel=0.985,
        accord_humain=0.93,
    )
    assert resultat.modele_id == "petit-modele"
    assert resultat.nombre_requetes_rejouees == 200
    assert resultat.taux_equivalence == pytest.approx(0.975)
    assert resultat.intervalle_confiance_bas == pytest.approx(0.943, abs=0.005)
    assert resultat.auto_accord_modele_actuel == pytest.approx(0.985)
    assert resultat.accord_juge_humain == pytest.approx(0.93)
    assert resultat.cout_par_requete_usd == pytest.approx(0.0009)
    assert resultat.latence_p95_ms == 900
    assert resultat.familles_en_echec == []
    assert resultat.conforme_seuils is False  # rempli par la règle de décision


def test_consolider_resultat_liste_les_familles_en_echec() -> None:
    """Une famille faible remonte dans le résultat, pour les risques de l'Annexe B."""
    resultat = consolider_resultat(
        modele_id="petit-modele",
        paires=paires(100, 100, "francais") + paires(20, 10, "langue-rare"),
        mesures=MESURES,
    )
    assert resultat.familles_en_echec == ["langue-rare"]
    assert resultat.taux_equivalence == pytest.approx(110 / 120, abs=1e-6)
