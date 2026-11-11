"""Tests de la relecture humaine : sous-échantillon de 30 paires et accord (EF-EVA-05, §13.5).

ENF-CONF-01 : le module ne déplace aucun texte — le sous-échantillon ne porte
que des `requete_id`.
"""

from __future__ import annotations

import pytest

from jauge.common.errors import ValidationError
from jauge.evaluation.human_review import (
    TAILLE_ECHANTILLON,
    VerdictHumain,
    preparer_relecture,
    tirer_sous_echantillon,
)
from jauge.evaluation.judge import VerdictJuge
from jauge.evaluation.scoring import PaireEvaluee


def paires(n: int) -> list[PaireEvaluee]:
    """`n` paires notées, une sur dix jugée non équivalente."""
    return [
        PaireEvaluee(
            requete_id=f"q-{index:03d}",
            verdict=VerdictJuge(equivalent=index % 10 != 0, explication="test"),
        )
        for index in range(n)
    ]


def test_sous_echantillon_de_30_paires() -> None:
    """§13.5 : 30 paires tirées parmi les 200 évaluées, identifiants seuls."""
    sous_echantillon = tirer_sous_echantillon(paires(200))
    assert TAILLE_ECHANTILLON == 30
    assert len(sous_echantillon.requete_ids) == 30
    assert sous_echantillon.complet is True
    assert all(identifiant.startswith("q-") for identifiant in sous_echantillon.requete_ids)


def test_tirage_reproductible_a_graine_egale() -> None:
    """Même graine, même sous-échantillon : la relecture est rejouable (§13.1)."""
    premier = tirer_sous_echantillon(paires(200), graine=42)
    second = tirer_sous_echantillon(paires(200), graine=42)
    autre = tirer_sous_echantillon(paires(200), graine=7)
    assert premier.requete_ids == second.requete_ids
    assert premier.requete_ids != autre.requete_ids


def test_echantillon_plus_petit_que_30_est_pris_en_entier() -> None:
    """Sur 12 paires évaluées, les 12 sont relues : mieux vaut partiel qu'aucun."""
    sous_echantillon = tirer_sous_echantillon(paires(12))
    assert len(sous_echantillon.requete_ids) == 12
    assert sous_echantillon.complet is False


def test_tirage_refuse_un_echantillon_vide() -> None:
    """Sans paire évaluée, il n'y a rien à relire : refus explicite."""
    with pytest.raises(ValidationError):
        tirer_sous_echantillon([])
    with pytest.raises(ValidationError):
        tirer_sous_echantillon(paires(10), taille=0)


def test_enregistrement_des_verdicts_et_accord() -> None:
    """30 verdicts humains enregistrés, un désaccord avec le juge → accord 29/30."""
    registre = preparer_relecture(paires(200))
    for identifiant in registre.sous_echantillon.requete_ids:
        verdict_juge = registre.verdicts_juge[identifiant]
        registre.enregistrer(
            VerdictHumain(
                requete_id=identifiant,
                equivalent=verdict_juge.equivalent,
                relecteur="relecteur-demo",
            )
        )
    assert registre.complet is True
    assert registre.accord() == pytest.approx(1.0)

    premiere = registre.sous_echantillon.requete_ids[0]
    registre.enregistrer(
        VerdictHumain(
            requete_id=premiere,
            equivalent=not registre.verdicts_juge[premiere].equivalent,
            relecteur="relecteur-demo",
            commentaire="désaccord assumé",
        )
    )
    assert registre.accord() == pytest.approx(29 / 30)


def test_verdict_hors_echantillon_refuse() -> None:
    """Un verdict sur une paire non tirée est refusé : pas de relecture opportuniste."""
    registre = preparer_relecture(paires(200))
    hors_echantillon = next(
        f"q-{index:03d}"
        for index in range(200)
        if f"q-{index:03d}" not in registre.sous_echantillon.requete_ids
    )
    with pytest.raises(ValidationError):
        registre.enregistrer(VerdictHumain(requete_id=hors_echantillon, equivalent=True))


def test_accord_refuse_sans_verdict_humain() -> None:
    """Un accord sans donnée ne veut rien dire et ne doit pas être affiché."""
    registre = preparer_relecture(paires(200))
    with pytest.raises(ValidationError):
        registre.accord()
    assert registre.complet is False


def test_relecture_non_autorisee_refusee() -> None:
    """§13.5 : la relecture n'est proposée que si l'application l'autorise."""
    with pytest.raises(ValidationError):
        preparer_relecture(paires(200), relecture_autorisee=False)


def test_restant_liste_les_paires_a_relire() -> None:
    """Après deux verdicts, 28 paires restent à relire."""
    registre = preparer_relecture(paires(200))
    for identifiant in registre.sous_echantillon.requete_ids[:2]:
        registre.enregistrer(VerdictHumain(requete_id=identifiant, equivalent=True))
    assert len(registre.restant()) == 28
    assert len(registre.sous_echantillon.a_relire(registre.verdicts.values())) == 28
