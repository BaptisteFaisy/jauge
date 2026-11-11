"""Test d'intégration léger du portail servi en statique (M13).

Un serveur `http.server` est lancé sur 127.0.0.1 (port éphémère, aucune sortie
réseau) : il reproduit le comportement de l'API du plan de contrôle, qui monte
le dossier `portal/` tel quel. On vérifie que la page se charge et contient les
conteneurs attendus par `js/main.js`, puis que chaque ressource est servie.
"""

from __future__ import annotations

import http.client
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
PORTAL = RACINE / "portal"

CONTENEURS = ['id="vue"', 'id="bandeau"', 'id="demo-client"', 'id="demo-role"']

RESSOURCES = [
    "assets/app.css",
    "js/main.js",
    "js/api.js",
    "js/dom.js",
    "js/views/client-dashboard.js",
    "js/views/fleet-dashboard.js",
    "js/views/application.js",
    "js/views/campaign.js",
    "js/views/recommendation.js",
    "js/views/rollout.js",
    "js/views/review.js",
    "js/views/about.js",
]


class RepondeurSilencieux(SimpleHTTPRequestHandler):
    """Serveur de fichiers sans journalisation (sortie de test propre)."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """N'écrit rien : le test ne doit produire aucune sortie parasite."""


@pytest.fixture(scope="module")
def portail() -> ThreadingHTTPServer:
    """Serveur statique local qui monte `portal/` sur la boucle locale."""
    gestionnaire = partial(RepondeurSilencieux, directory=str(PORTAL))
    serveur = ThreadingHTTPServer(("127.0.0.1", 0), gestionnaire)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    try:
        yield serveur
    finally:
        serveur.shutdown()
        serveur.server_close()
        fil.join(timeout=5)


def requete(serveur: ThreadingHTTPServer, chemin: str) -> tuple[int, str, str]:
    """Exécute une requête GET locale et rend (statut, type, corps)."""
    connexion = http.client.HTTPConnection("127.0.0.1", serveur.server_port, timeout=10)
    try:
        connexion.request("GET", chemin)
        reponse = connexion.getresponse()
        corps = reponse.read().decode("utf-8", "replace")
        return reponse.status, reponse.getheader("Content-Type", ""), corps
    finally:
        connexion.close()


def test_page_accueil_servie(portail: ThreadingHTTPServer) -> None:
    """La page d'accueil est servie en HTML et contient les conteneurs attendus."""
    statut, type_contenu, corps = requete(portail, "/index.html")
    assert statut == 200
    assert "text/html" in type_contenu
    assert "Jauge" in corps
    for conteneur in CONTENEURS:
        assert conteneur in corps, f"conteneur absent : {conteneur}"


def test_racine_sert_index(portail: ThreadingHTTPServer) -> None:
    """La racine du site rend directement la SPA (aucune réécriture nécessaire)."""
    statut, _type, corps = requete(portail, "/")
    assert statut == 200
    assert '<main id="vue"' in corps


def test_ressources_servies(portail: ThreadingHTTPServer) -> None:
    """Chaque module ES et la feuille de style sont servis et non vides."""
    for ressource in RESSOURCES:
        statut, _type, corps = requete(portail, f"/{ressource}")
        assert statut == 200, f"{ressource} non servie"
        assert corps.strip(), f"{ressource} vide"
        if ressource.endswith(".js") and ressource != "js/main.js":
            assert "export" in corps, f"{ressource} n'expose rien"
        if ressource == "js/main.js":
            assert "import" in corps, "js/main.js doit importer les modules ES"


def test_feuille_de_style_servie(portail: ThreadingHTTPServer) -> None:
    """La feuille de style est servie en CSS et définit la palette du portail."""
    statut, type_contenu, corps = requete(portail, "/assets/app.css")
    assert statut == 200
    assert "text/css" in type_contenu
    assert "--accent" in corps
    assert "http" not in corps


def test_chemin_inconnu_refuse(portail: ThreadingHTTPServer) -> None:
    """Un chemin absent renvoie 404 : le portail n'invente pas de contenu."""
    statut, _type, _corps = requete(portail, "/js/inexistant.js")
    assert statut == 404
