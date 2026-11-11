"""Tests du tirage d'échantillon stratifié et reproductible (EF-REJ-01, §13.1)."""

from __future__ import annotations

import pytest

from jauge.common.errors import ValidationError
from jauge.replay.sampling import (
    BORNE_COURT,
    BORNE_MOYEN,
    RequeteSource,
    classe_longueur,
    tirer_echantillon,
)

APPLICATION = "app-demo"


def test_meme_graine_meme_echantillon(requetes_source: list[RequeteSource]) -> None:
    """§13.1 : même graine ⇒ même échantillon, dans le même ordre."""
    premier = tirer_echantillon(requetes_source, application_id=APPLICATION, taille=40, graine=11)
    second = tirer_echantillon(requetes_source, application_id=APPLICATION, taille=40, graine=11)
    assert premier.identifiants == second.identifiants
    assert premier.strates == second.strates


def test_ordre_d_entree_sans_effet(requetes_source: list[RequeteSource]) -> None:
    """Le tirage ne dépend pas de l'ordre d'arrivée des requêtes (rejouabilité)."""
    direct = tirer_echantillon(requetes_source, application_id=APPLICATION, taille=40, graine=5)
    inverse = tirer_echantillon(
        list(reversed(requetes_source)), application_id=APPLICATION, taille=40, graine=5
    )
    assert direct.identifiants == inverse.identifiants


def test_graine_differente_echantillon_different(requetes_source: list[RequeteSource]) -> None:
    """Deux graines distinctes ne tirent pas le même échantillon."""
    a = tirer_echantillon(requetes_source, application_id=APPLICATION, taille=40, graine=1)
    b = tirer_echantillon(requetes_source, application_id=APPLICATION, taille=40, graine=2)
    assert a.identifiants != b.identifiants


def test_application_differente_tirage_different(requetes_source: list[RequeteSource]) -> None:
    """La graine dérivée inclut l'application : même graine, autre application, autre tirage."""
    a = tirer_echantillon(requetes_source, application_id=APPLICATION, taille=40, graine=9)
    b = tirer_echantillon(
        [
            RequeteSource(
                requete_id=requete.requete_id,
                application_id="app-autre",
                type_requete=requete.type_requete,
                longueur_caracteres=requete.longueur_caracteres,
                jetons_entree_origine=requete.jetons_entree_origine,
            )
            for requete in requetes_source
        ],
        application_id="app-autre",
        taille=40,
        graine=9,
    )
    assert a.identifiants != b.identifiants


def test_taille_respectee_et_sans_doublon(requetes_source: list[RequeteSource]) -> None:
    """La taille demandée est servie, sans doublon, et plafonnée au pool disponible."""
    echantillon = tirer_echantillon(
        requetes_source, application_id=APPLICATION, taille=200, graine=3
    )
    assert echantillon.taille == len(requetes_source)
    assert len(set(echantillon.identifiants)) == echantillon.taille
    assert sum(echantillon.strates.values()) == echantillon.taille


def test_proportions_de_strates_respectees(requetes_source: list[RequeteSource]) -> None:
    """Stratification par longueur ET par type : chaque strate reçoit sa part (§13.1)."""
    echantillon = tirer_echantillon(
        requetes_source, application_id=APPLICATION, taille=60, graine=4
    )
    # 60 % de `long:classement`, 20 % de `moyen:classement`, 20 % de `court:redaction`.
    assert echantillon.strates["long:classement"] == pytest.approx(36, abs=1)
    assert echantillon.strates["moyen:classement"] == pytest.approx(12, abs=1)
    assert echantillon.strates["court:redaction"] == pytest.approx(12, abs=1)
    assert echantillon.strates == {
        "court:redaction": 12,
        "long:classement": 36,
        "moyen:classement": 12,
    }


def test_requetes_sensibles_exclues(requetes_source: list[RequeteSource]) -> None:
    """§13.1 : les requêtes marquées sensibles par le client sont écartées et comptées."""
    sources = [
        RequeteSource(
            requete_id=requete.requete_id,
            application_id=requete.application_id,
            type_requete=requete.type_requete,
            longueur_caracteres=requete.longueur_caracteres,
            jetons_entree_origine=requete.jetons_entree_origine,
            sensible=requete.requete_id.endswith("0"),
        )
        for requete in requetes_source
    ]
    sensibles = {requete.requete_id for requete in sources if requete.sensible}
    echantillon = tirer_echantillon(
        sources, application_id=APPLICATION, taille=len(sources), graine=6
    )
    assert echantillon.exclusions_sensibles == len(sensibles)
    assert not sensibles & set(echantillon.identifiants)


def test_requetes_d_une_autre_application_ecartees(requetes_source: list[RequeteSource]) -> None:
    """Cloisonnement du tirage : une requête d'une autre application n'entre jamais."""
    sources = [
        RequeteSource(
            requete_id="req-etrangere",
            application_id="app-autre",
            type_requete="classement",
            longueur_caracteres=800,
            jetons_entree_origine=100,
        ),
        *requetes_source,
    ]
    echantillon = tirer_echantillon(sources, application_id=APPLICATION, taille=30, graine=8)
    assert echantillon.hors_application == 1
    assert "req-etrangere" not in echantillon.identifiants


def test_pool_vide_refuse() -> None:
    """Sans requête tirable, le tirage échoue explicitement (erreur métier)."""
    with pytest.raises(ValidationError):
        tirer_echantillon(
            [RequeteSource(requete_id="x", application_id="autre")],
            application_id=APPLICATION,
            taille=10,
        )


def test_taille_invalide_refusee(requetes_source: list[RequeteSource]) -> None:
    """Une taille nulle ou négative est refusée avant tout calcul."""
    with pytest.raises(ValidationError):
        tirer_echantillon(requetes_source, application_id=APPLICATION, taille=0)


def test_jetons_entree_moyens(echantillon) -> None:
    """Les jetons moyens de l'échantillon alimentent l'estimation de coût (§17.1)."""
    assert echantillon.jetons_entree_moyens() > 0
    assert echantillon.jetons_entree_moyens() == pytest.approx(
        sum(requete.jetons_entree_origine for requete in echantillon.requetes) / echantillon.taille
    )


@pytest.mark.parametrize(
    ("longueur", "strate"),
    [(0, "court"), (BORNE_COURT - 1, "court"), (BORNE_COURT, "moyen"), (BORNE_MOYEN, "long")],
)
def test_bornes_de_longueur(longueur: int, strate: str) -> None:
    """Les bornes de strate sont inclusives à gauche, exclusives à droite."""
    assert classe_longueur(longueur) == strate
