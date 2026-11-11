"""Accès en lecture aux fixtures du socle (`tests/conftest.py`).

La démonstration ne redéfinit ni les clients, ni les applications, ni la table
des tarifs : elle **recharge les fixtures du socle** sans les modifier (le
fichier est verrouillé) et les réutilise.

Une fixture pytest est un objet non appelable directement ; on récupère la
fonction d'origine par `__wrapped__` (ou `_fixture_function` selon la version
de pytest) et on l'appelle avec ses dépendances.

Aucun réseau, aucune donnée réelle : les fixtures ne contiennent que des
valeurs fictives de démonstration.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jauge.models.pricing import TarifModele
from jauge.models.tenant import Application, Tenant

CHEMIN_CONFTEST = Path(__file__).resolve().parents[1] / "tests" / "conftest.py"


@dataclass(frozen=True)
class SocleDemo:
    """Référentiel de la démonstration : clients, applications, tarifs."""

    tenant_a: Tenant
    tenant_b: Tenant
    applications: dict[str, Application]
    tarifs: list[TarifModele]

    def par_tenant(self, tenant_id: str) -> list[Application]:
        """Applications d'un client."""
        return [a for a in self.applications.values() if a.tenant_id == tenant_id]


@cache
def constantes() -> Any:
    """Module `conftest` du socle, chargé une seule fois."""
    if not CHEMIN_CONFTEST.exists():
        raise FileNotFoundError(
            f"Fixtures du socle introuvables : {CHEMIN_CONFTEST}. "
            "La démonstration se lance depuis la racine du dépôt Jauge."
        )
    spec = importlib.util.spec_from_file_location("jauge_conftest_socle", CHEMIN_CONFTEST)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossible de charger {CHEMIN_CONFTEST}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def appeler_fixture(fixture: Any, *arguments: Any) -> Any:
    """Appelle une fixture pytest hors contexte de test."""
    fonction = getattr(fixture, "__wrapped__", None) or getattr(fixture, "_fixture_function", None)
    if fonction is None:
        raise RuntimeError(f"Fixture pytest illisible : {fixture!r}")
    return fonction(*arguments)


@cache
def charger_socle_demo() -> SocleDemo:
    """Référentiel de la démonstration, construit depuis les fixtures du socle."""
    module = constantes()
    tenant_a = appeler_fixture(module.tenant)
    tenant_b = appeler_fixture(module.tenant_b)
    applications = {
        "support-assistant": appeler_fixture(module.app_assistant, tenant_a),
        "ticket-classifier": appeler_fixture(module.app_classement, tenant_a),
        "doc-summarizer": _application_doc_summarizer(tenant_a, module.GRAND_MODELE),
        "kb-search": _application_kb_search(tenant_b, module.MOYEN_MODELE),
    }
    return SocleDemo(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        applications=applications,
        tarifs=list(appeler_fixture(module.tarifs)),
    )


def _application_doc_summarizer(tenant: Tenant, modele_id: str) -> Application:
    """Résumé de documents nocturne, resté au niveau Standard (§18.1)."""
    return Application(
        id="doc-summarizer",
        tenant_id=tenant.id,
        nom="Résumé de documents",
        equipe="back-office",
        environnement="production",
        modele_actuel=modele_id,
        seuil_qualite=0.95,
        seuil_intervalle_bas=0.90,
        description_tache="Résumé de documents internes, traitement nocturne",
    )


def _application_kb_search(tenant: Tenant, modele_id: str) -> Application:
    """Recherche documentaire chez le client B (vue parc)."""
    return Application(
        id="kb-search",
        tenant_id=tenant.id,
        nom="Recherche dans la base de connaissances",
        equipe="documentation",
        environnement="production",
        modele_actuel=modele_id,
        seuil_qualite=0.95,
        seuil_intervalle_bas=0.90,
        description_tache="Recherche documentaire interne",
    )
