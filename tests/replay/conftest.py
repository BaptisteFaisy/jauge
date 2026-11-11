"""Fixtures locales du module de rejeu (M4).

`tests/conftest.py` est verrouillé et ses constantes de modèles sont anonymisées
(trois noms identiques), ce qui rendrait ambiguë toute table de tarifs bâtie
dessus. Ce module de test définit donc ses **propres** identifiants de modèles de
démonstration (voir `constantes.py`), distincts et explicites, et sa propre table
de tarifs datée (Flex + Standard, comme en production).
"""

from __future__ import annotations

import pytest
from tests.replay.constantes import (
    APPLICATION,
    GRAINE,
    MODELE_GRAND,
    MODELE_JUGE,
    MODELE_MOYEN,
    MODELE_PETIT,
    tarif,
)

from jauge.engine.calculator import TableTarifs
from jauge.models.pricing import ServiceTier
from jauge.replay.client import FakeModeleClient
from jauge.replay.executor import TextesEnMemoire
from jauge.replay.sampling import RequeteSource, tirer_echantillon


@pytest.fixture
def table_tarifs() -> TableTarifs:
    """Table datée : quatre modèles, niveau Flex pour les rejoués, Standard pour le juge."""
    return TableTarifs(
        [
            tarif(MODELE_GRAND, 0.0075, 0.0375, ServiceTier.FLEX),
            tarif(MODELE_MOYEN, 0.0030, 0.0150, ServiceTier.FLEX),
            tarif(MODELE_PETIT, 0.0004, 0.0020, ServiceTier.FLEX),
            tarif(MODELE_JUGE, 0.0030, 0.0150, ServiceTier.STANDARD),
        ]
    )


@pytest.fixture
def requetes_source() -> list[RequeteSource]:
    """120 requêtes de démonstration, réparties sur trois longueurs et deux types.

    Le déséquilibre est volontaire : la strate `long:classement` domine, ce qui
    permet de vérifier l'allocation proportionnelle du tirage.
    """
    requetes: list[RequeteSource] = []
    for index in range(120):
        if index % 10 < 6:
            type_requete = "classement"
            longueur = 2400 + index  # strate `long`
        elif index % 10 < 8:
            type_requete = "classement"
            longueur = 900 + index  # strate `moyen`
        else:
            type_requete = "redaction"
            longueur = 120 + index  # strate `court`
        requetes.append(
            RequeteSource(
                requete_id=f"req-{index:04d}",
                application_id=APPLICATION,
                type_requete=type_requete,
                longueur_caracteres=longueur,
                jetons_entree_origine=200 + index,
            )
        )
    return requetes


@pytest.fixture
def textes_source(requetes_source: list[RequeteSource]) -> TextesEnMemoire:
    """Textes du plan de données, un par requête source (jamais vus du contrôle)."""
    return TextesEnMemoire(
        {
            requete.requete_id: f"Question de demonstration {requete.requete_id}"
            for requete in requetes_source
        }
    )


@pytest.fixture
def echantillon(requetes_source: list[RequeteSource]):
    """Échantillon de 60 requêtes tiré sur la graine de démonstration."""
    return tirer_echantillon(requetes_source, application_id=APPLICATION, taille=60, graine=GRAINE)


@pytest.fixture
def client_fictif() -> FakeModeleClient:
    """Adaptateur fictif déterministe : 250 ms de latence, 40 jetons générés."""
    return FakeModeleClient(jetons_sortie=40, latence_ms=250)
