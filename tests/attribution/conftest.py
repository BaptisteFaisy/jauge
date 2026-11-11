"""Fixtures locales du module d'attribution (M1).

Elles complètent les fixtures partagées de `tests/conftest.py` (client,
applications, table de tarifs du socle) sans les modifier : ici vivent les
modèles fictifs du module, la table de tarifs associée, les applications
connues du plan de contrôle et les chemins des fixtures JSON.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jauge.engine.calculator import TableTarifs
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.models.tenant import Application, SchemaAttribution, Tenant

FIXTURES = Path(__file__).parent / "fixtures"
JOURNAUX = FIXTURES / "journaux_invocation.jsonl"
CUR = FIXTURES / "cur_bedrock.jsonl"

# Modèles fictifs du module (aucun identifiant réel).
MODELE_GRAND = "modele-demo-grand"
MODELE_PETIT = "modele-demo-petit"
REGION_DEMO = "eu-west-1"

# Jour des fixtures de journaux (2026-11-09) et de facture.
JOUR = date(2026, 11, 9)


@pytest.fixture
def schema_attribution() -> SchemaAttribution:
    """Schéma d'attribution du client : trois dimensions, défaut explicite."""
    return SchemaAttribution(
        dimensions=["application", "team", "environment"],
        valeurs_equipes=["support", "data", "plateforme"],
        valeurs_environnements=["production", "staging", "dev"],
        application_par_defaut="non-attribue",
    )


@pytest.fixture
def applications(tenant: Tenant) -> list[Application]:
    """Applications connues du plan de contrôle pour le client de démo."""
    return [
        Application(
            id="ticket-classifier",
            tenant_id=tenant.id,
            nom="Classement de tickets",
            equipe="support",
            environnement="production",
            modele_actuel=MODELE_GRAND,
        ),
        Application(
            id="support-assistant",
            tenant_id=tenant.id,
            nom="Assistant de support",
            equipe="support",
            environnement="production",
            modele_actuel=MODELE_GRAND,
        ),
        Application(
            id="resume-nocturne",
            tenant_id=tenant.id,
            nom="Résumé nocturne",
            equipe="data",
            environnement="staging",
            modele_actuel=MODELE_PETIT,
        ),
    ]


@pytest.fixture
def tarifs_demo() -> TableTarifs:
    """Table de tarifs datée : deux modèles, deux niveaux, une région (§3.2)."""
    return TableTarifs(
        [
            TarifModele(
                modele_id=MODELE_GRAND,
                region=REGION_DEMO,
                niveau=ServiceTier.STANDARD,
                date_effet=date(2026, 1, 1),
                prix_entree_1k=0.015,
                prix_sortie_1k=0.075,
                prix_cache_ecriture_1k=0.01875,
                prix_cache_lecture_1k=0.0015,
            ),
            TarifModele(
                modele_id=MODELE_GRAND,
                region=REGION_DEMO,
                niveau=ServiceTier.FLEX,
                date_effet=date(2026, 1, 1),
                prix_entree_1k=0.0075,
                prix_sortie_1k=0.0375,
            ),
            TarifModele(
                modele_id=MODELE_PETIT,
                region=REGION_DEMO,
                niveau=ServiceTier.STANDARD,
                date_effet=date(2026, 1, 1),
                prix_entree_1k=0.0008,
                prix_sortie_1k=0.004,
            ),
        ]
    )


@pytest.fixture
def journaux_texte() -> str:
    """Contenu brut du journal d'invocation de test (NDJSON)."""
    return JOURNAUX.read_text(encoding="utf-8")
