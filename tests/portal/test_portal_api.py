"""Tests statiques du contrat d'API du portail (M13, §15).

Deux garanties :
1. tout point d'API appelé par le JavaScript figure dans le tableau du §15 du
   cahier des charges, à l'exception d'une liste d'extensions documentées ;
2. le navigateur ne calcule aucun indicateur métier : les vues n'agrègent rien
   et ne parlent au réseau que par `js/api.js`.
"""

from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
PORTAL = RACINE / "portal"
CAHIER = RACINE / "docs" / "fr" / "cahier-des-charges-finops-ia.md"

#: Extensions assumées, absentes du tableau du §15, déclarées dans le corps de la PR.
EXTENSIONS = {
    ("GET", "/v1/tenants"),  # parc MSP : liste des clients visibles
    ("GET", "/v1/tenants/{}/applications"),  # applications d'un client
    ("GET", "/v1/rollouts/{}"),  # état d'une bascule (le §15 ne liste que le rollback)
    ("GET", "/v1/campaigns/{}/reviews"),  # relecture humaine (EF-EVA-05)
    ("POST", "/v1/campaigns/{}/reviews"),  # vote humain (EF-EVA-05)
}

MOTIF_APPEL = re.compile(r'appeler\(\s*"(GET|POST|PUT|PATCH|DELETE)",\s*[`"]([^`"]+)[`"]')
MOTIF_CAHIER = re.compile(r"`(GET|POST|PUT|PATCH|DELETE)\s+(/v1/[^`]+)`")
MOTIF_PARAMETRE = re.compile(r"\{[^}]*\}")
MOTIF_INTERPOLATION = re.compile(r"\$\{[^}]*\}")


def lire(chemin: Path) -> str:
    """Lit un fichier en UTF-8."""
    return chemin.read_text(encoding="utf-8")


def normaliser(chemin: str) -> str:
    """Remplace les paramètres d'URL par `{}` pour comparer les formes."""
    return MOTIF_PARAMETRE.sub("{}", MOTIF_INTERPOLATION.sub("{}", chemin))


def endpoints_du_cahier() -> set[tuple[str, str]]:
    """Points d'API listés dans le §15 du cahier des charges."""
    texte = lire(CAHIER)
    debut = texte.index("## 15. API du plan de contrôle")
    fin = texte.index("## 16. Sécurité et confidentialité")
    trouves = MOTIF_CAHIER.findall(texte[debut:fin])
    return {(methode, normaliser(chemin)) for methode, chemin in trouves}


def endpoints_du_portail() -> set[tuple[str, str]]:
    """Points d'API réellement appelés par `js/api.js`."""
    appels = MOTIF_APPEL.findall(lire(PORTAL / "js" / "api.js"))
    return {(methode, normaliser(chemin)) for methode, chemin in appels}


def test_le_cahier_liste_bien_des_points_api() -> None:
    """Garde-fou : l'extraction du §15 donne le tableau attendu."""
    points = endpoints_du_cahier()
    assert len(points) >= 12
    assert ("GET", "/v1/applications/{}/costs") in points
    assert ("POST", "/v1/recommendations/{}/decision") in points


def test_points_api_du_portail_dans_le_cahier() -> None:
    """Aucun point d'API hors §15 et hors extensions documentées."""
    hors_cahier = endpoints_du_portail() - endpoints_du_cahier() - EXTENSIONS
    assert hors_cahier == set(), f"points hors §15 : {sorted(hors_cahier)}"


def test_extensions_documentees_pour_les_juges() -> None:
    """Chaque extension figure dans la page « About » servie aux jugateurs."""
    source = lire(PORTAL / "js" / "views" / "about.js")
    for _methode, chemin in sorted(EXTENSIONS):
        modele = chemin.replace("{}", "{id}")
        assert modele in source, f"extension non documentée : {modele}"


def test_ecrans_couvrent_les_exigences() -> None:
    """Chaque écran demandé par la mission existe et appelle les bons points d'API."""
    vues = {chemin.stem for chemin in (PORTAL / "js" / "views").glob("*.js")}
    assert {
        "client-dashboard",
        "fleet-dashboard",
        "application",
        "campaign",
        "recommendation",
        "rollout",
        "review",
    } <= vues
    appels = endpoints_du_portail()
    attendus = {
        ("POST", "/v1/recommendations/{}/decision"),
        ("POST", "/v1/recommendations/{}/rollout"),
        ("POST", "/v1/rollouts/{}/rollback"),
        ("GET", "/v1/applications/{}/levers"),
        ("GET", "/v1/campaigns/{}/results"),
        ("GET", "/v1/tenants/{}/savings"),
    }
    assert attendus <= appels


def test_aucun_calcul_metier_dans_les_vues() -> None:
    """Les vues n'agrègent rien : ni somme, ni moyenne, ni pourcentage local."""
    interdits = ("reduce(", "+=", "* 100", "/ 100")
    for chemin in sorted((PORTAL / "js" / "views").glob("*.js")):
        source = lire(chemin)
        for interdit in interdits:
            assert interdit not in source, f"{interdit} dans {chemin.name}"


def test_aucun_appel_reseau_hors_api() -> None:
    """`fetch` n'apparaît que dans le client d'API, en chemin relatif."""
    for chemin in sorted(PORTAL.rglob("*.js")):
        source = lire(chemin)
        if "fetch(" not in source:
            continue
        assert chemin.name == "api.js", f"fetch trouvé dans {chemin.name}"
        assert "fetch(chemin" in source
        assert not re.search(r"fetch\(\s*[`\"](https?:)?//", source)


def test_session_demo_transmise_en_entetes() -> None:
    """Le compte de démonstration pose les en-têtes lus par l'API (EF-POR-02)."""
    source = lire(PORTAL / "js" / "api.js")
    assert '"X-Jauge-Tenant"' in source
    assert '"X-Jauge-Role"' in source
    config = lire(PORTAL / "js" / "config.js")
    assert 'clientId: "t-demo-a"' in config
    assert 'role: "analyste_finops"' in config
