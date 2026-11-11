"""Tests du mode statique du portail (publication sur GitHub Pages).

Le portail publié n'a pas de backend : il sert l'instantané
`portal/data/demo.json`, produit par `scripts/export_portail_statique.py` à
partir de la véritable application FastAPI. Trois propriétés sont vérifiées :

1. l'instantané **versionné** correspond à ce que l'exporteur produit
   aujourd'hui (garde contre un instantané périmé) ;
2. la logique JavaScript du mode statique se comporte comme l'API : cloisonnement
   entre clients (403), approbation obligatoire avant bascule (409), paliers
   10 → 50 → 100, refus explicite de ce qui exige le plan de contrôle réel (501) ;
3. aucun chemin absolu dans le portail : publié sous `/jauge/`, un `fetch("/v1/…")`
   sortirait du préfixe et casserait tous les écrans.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
PORTAIL = RACINE / "portal"
INSTANTANE = PORTAIL / "data" / "demo.json"
FUMEE = Path(__file__).resolve().parent / "smoke_statique.mjs"


def test_instantane_versionne_est_a_jour(tmp_path: Path) -> None:
    """L'instantané publié est celui que l'exporteur produit (mêmes réponses)."""
    from scripts.export_portail_statique import exporter

    cible = tmp_path / "demo.json"
    genere = exporter(cible)
    versionne = json.loads(INSTANTANE.read_text(encoding="utf-8"))
    # L'horodatage de génération diffère par nature : il ne fait pas partie de
    # la comparaison. Tout le reste (identifiants compris) est déterministe.
    genere["meta"].pop("genere_le", None)
    versionne["meta"].pop("genere_le", None)

    assert set(genere["reponses"]) == set(versionne["reponses"]), (
        "l'instantané versionné ne couvre pas les mêmes points d'API que l'export : "
        "relancer `uv run python scripts/export_portail_statique.py`"
    )
    assert genere["reponses"] == versionne["reponses"], (
        "l'instantané versionné est périmé : relancer "
        "`uv run python scripts/export_portail_statique.py`"
    )
    assert genere["meta"]["clients"] == versionne["meta"]["clients"]


def test_instantane_contient_les_deux_clients_de_demo() -> None:
    """Les deux clients de la démonstration (§18.1) sont dans l'instantané."""
    document = json.loads(INSTANTANE.read_text(encoding="utf-8"))
    assert document["meta"]["clients"] == ["t-demo-a", "t-demo-b"]
    assert "GET /v1/tenants" in document["reponses"]
    assert "GET /v1/tenants/t-demo-a/savings" in document["reponses"]
    assert "GET /v1/applications/ticket-classifier/levers" in document["reponses"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js absent du PATH")
def test_fumee_node_du_mode_statique() -> None:
    """La logique JS du mode statique est exercée par Node (aucun navigateur)."""
    resultat = subprocess.run(
        ["node", str(FUMEE), str(INSTANTANE)],
        cwd=RACINE,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert "SMOKE OK" in resultat.stdout


def test_aucun_chemin_absolu_dans_le_portail() -> None:
    """Aucun `fetch("/…")` ni asset absolu : le portail doit vivre sous un préfixe."""
    fautifs: list[str] = []
    fichiers = [PORTAIL / "index.html", *sorted(PORTAIL.rglob("*.js"))]
    for fichier in fichiers:
        for numero, ligne in enumerate(fichier.read_text(encoding="utf-8").splitlines(), 1):
            code = ligne.split("//", 1)[0] if fichier.suffix == ".js" else ligne
            for motif in ('fetch("/', "fetch('/", 'src="/', 'href="/'):
                if motif in code:
                    fautifs.append(f"{fichier.relative_to(RACINE)}:{numero} {motif}")
    assert not fautifs, "chemins absolus incompatibles avec GitHub Pages : " + ", ".join(fautifs)


def test_instantane_present_dans_le_paquet_publie() -> None:
    """Le dossier publié contient bien l'index, les modules et l'instantané."""
    for attendu in ("index.html", "js/api.js", "js/statique.js", "data/demo.json"):
        assert (PORTAIL / attendu).is_file(), f"portail/{attendu} manquant"
