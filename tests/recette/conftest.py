"""Fixtures partagées de la recette bout en bout (M15, §22 — scénarios R1 à R13).

La recette exerce le **code réel** des modules sur `MemoryStore` avec les
adaptateurs fictifs fournis par chaque paquet (`FakeModeleClient`,
`FakeJugeClient`, `FakePasserelleClient`, `FakeQualiteEnLigne`) et le trafic
synthétique de `demo/` : aucun appel réseau, aucun compte AWS (BRIEF §2.4).

Les fixtures du socle (`tests/conftest.py`) sont réutilisées telles quelles :
`tenant`, `tenant_b`, `app_classement`, `app_assistant`, `tarifs` et les
constantes de modèles. Ce fichier n'ajoute que le câblage propre à la recette.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jauge.api.app import creer_app
from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.engine.calculator import TableTarifs
from jauge.models.pricing import TarifModele
from jauge.models.tenant import Application, Tenant
from jauge.store.memory import MemoryStore


@pytest.fixture
def store_recette() -> MemoryStore:
    """Store en mémoire vierge : chaque scénario de recette part de zéro."""
    return MemoryStore()


@pytest.fixture
def table_socle(tarifs: list[TarifModele]) -> TableTarifs:
    """Table de tarifs datée du socle (Annexe D), prête pour le moteur de coût."""
    return TableTarifs(tarifs)


@pytest.fixture
def audit_recette() -> MemoryAuditStore:
    """Journal d'audit en mémoire (EF-ADM-02), inspecté par R6/R11."""
    return MemoryAuditStore()


@pytest.fixture
def client_api(store_recette: MemoryStore, audit_recette: MemoryAuditStore) -> TestClient:
    """API du plan de contrôle en mode démo (en-têtes X-Jauge-*), sans réseau."""
    return TestClient(creer_app(store=store_recette, mode_demo=True, audit=audit_recette))


def entetes(
    tenant_id: str,
    role: RoleUtilisateur | str = RoleUtilisateur.DIRECTEUR,
    utilisateur: str = "u-recette",
) -> dict[str, str]:
    """En-têtes d'authentification du mode démo (mêmes conventions que tests/api)."""
    return {
        "X-Jauge-Tenant": tenant_id,
        "X-Jauge-Role": str(role),
        "X-Jauge-User": utilisateur,
    }


def enregistrer_referentiel(store: MemoryStore, tenants: list[Tenant], apps: list[Application]):
    """Enregistre clients et applications dans le store (parcours §8.1, étapes 3-5)."""
    for element in tenants:
        store.enregistrer_tenant(element)
    for application in apps:
        store.enregistrer_application(application)


def ligne_journal(record: Any, **surcharges: Any) -> dict[str, object]:
    """Ligne de journal d'invocation Bedrock pour un enregistrement de la démo.

    `demo.traffic.vers_ligne_cloudwatch()` n'inclut pas l'identifiant du modèle
    (écart M12 signalé dans la recette) ; les journaux Bedrock réels le portent.
    On l'ajoute ici, comme le fait un vrai journal, pour exercer le parseur M1.
    """
    ligne = record.vers_ligne_cloudwatch()
    ligne["modelId"] = record.modele_id
    ligne.update(surcharges)
    return ligne


def ecrire_journal_ndjson(chemin: Path, lignes: list[dict[str, object]]) -> Path:
    """Écrit un journal NDJSON, format de dépôt S3 des journaux d'invocation."""
    with chemin.open("w", encoding="utf-8") as flux:
        for ligne in lignes:
            flux.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    return chemin
