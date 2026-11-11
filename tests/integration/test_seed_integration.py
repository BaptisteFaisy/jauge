"""`python -m demo.seed` alimente le store via les modules réels (M17).

Vérifie que l'amorçage de la démonstration passe bien par le chemin de
production (attribution M1, leviers M3, rejeu M4, évaluation M5, schéma
d'Annexe B M7) et reste **100 % hors ligne**.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from demo.profils import TENANT_A, TENANT_B
from demo.seed import peupler_store, resume

from jauge.integration import PlanDeDonnees
from jauge.models.recommendation import StatutRecommandation
from jauge.store.memory import MemoryStore

RACINE = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def store_amorce() -> tuple[MemoryStore, PlanDeDonnees]:
    """Store de démonstration amorcé sur une fenêtre courte (3 jours)."""
    magasin = MemoryStore()
    plan = peupler_store(magasin, jours=3)
    return magasin, plan


def test_seed_remplit_le_store(store_amorce) -> None:
    """Le store contient les clients, applications, agrégats et leviers."""
    store, plan = store_amorce
    assert {t.id for t in store.lister_tenants()} == {TENANT_A, TENANT_B}
    assert store.lister_applications(TENANT_A)
    assert store.lister_agregats(TENANT_A)
    assert store.lister_rapprochements(TENANT_A)
    assert store.lister_leviers(TENANT_A)
    assert plan.profil(TENANT_A, "ticket-classifier") is not None


def test_seed_produit_campagnes_et_recommandations(store_amorce) -> None:
    """Les campagnes et recommandations sont produites par les modules."""
    store, _ = store_amorce
    campagnes = store.lister_campagnes(TENANT_A)
    assert len(campagnes) == 3
    assert all(c.statut.value == "terminee" for c in campagnes)
    assert all(len(c.resultats) >= 2 for c in campagnes)
    recommandations = store.lister_recommandations(TENANT_A)
    assert len(recommandations) == 3
    statuts = {reco.applicationId: reco.status for reco in recommandations}
    assert statuts["ticket-classifier"] == StatutRecommandation.EN_ATTENTE_APPROBATION
    assert statuts["support-assistant"] == StatutRecommandation.REFUSEE


def test_seed_rest_reproductible() -> None:
    """Deux amorçages identiques donnent les mêmes agrégats (déterminisme)."""
    premier = MemoryStore()
    peupler_store(premier, jours=2)
    second = MemoryStore()
    peupler_store(second, jours=2)
    assert _empreinte_agregats(premier) == _empreinte_agregats(second)


def _empreinte_agregats(store: MemoryStore) -> list[tuple]:
    """Empreinte des agrégats du client A (application, jour, volume, coût)."""
    return [
        (a.application_id, a.jour.isoformat(), a.nombre_requetes, a.cout_recalcule_usd)
        for a in store.lister_agregats(TENANT_A)
    ]


def test_resume_console(store_amorce) -> None:
    """Le résumé console cite les applications, les leviers et les décisions."""
    store, _ = store_amorce
    texte = resume(store, jours=3)
    assert "ticket-classifier" in texte
    assert "Leviers détectés" in texte
    assert "Décisions" in texte


def test_module_seed_s_execute_en_ligne_de_commande() -> None:
    """`python -m demo.seed` s'exécute sans erreur ni appel réseau."""
    resultat = subprocess.run(
        [sys.executable, "-m", "demo.seed"],
        cwd=RACINE,
        capture_output=True,
        text=True,
        timeout=600,
        env={"PYTHONPATH": str(RACINE), "PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
    )
    assert resultat.returncode == 0, resultat.stderr
    assert "Jauge — démonstration hors ligne" in resultat.stdout
