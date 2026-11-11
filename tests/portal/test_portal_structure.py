"""Tests statiques de structure du portail (M13).

Vérifient, sans navigateur et sans Node, que le portail reste une SPA statique
sûre : fichiers présents, HTML d'accueil valide et accessible, échappement
systématique des données, aucune ressource ni script externe, aucun `eval`.

Exigences visées : EF-POR-01 (cloisonnement, servi par l'API), EF-POR-02
(compte de démonstration), ENF-SEC-02.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
PORTAL = RACINE / "portal"

FICHIERS_ATTENDUS = [
    "index.html",
    "README.md",
    "assets/app.css",
    "js/api.js",
    "js/config.js",
    "js/dom.js",
    "js/format.js",
    "js/chart.js",
    "js/router.js",
    "js/main.js",
    "js/views/client-dashboard.js",
    "js/views/fleet-dashboard.js",
    "js/views/application.js",
    "js/views/campaign.js",
    "js/views/recommendation.js",
    "js/views/rollout.js",
    "js/views/review.js",
    "js/views/about.js",
]

#: Balises dont l'imbrication est vérifiée (les autres sont auto-fermantes ou optionnelles).
BALISES_SUIVIES = {
    "html",
    "head",
    "body",
    "header",
    "nav",
    "main",
    "footer",
    "section",
    "form",
    "div",
    "p",
    "ul",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "select",
    "button",
    "label",
    "noscript",
}


class AnalyseurHTML(HTMLParser):
    """Collecte identifiants, balises ouvertes/fermées et attributs d'événement."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.identifiants: set[str] = set()
        self.pile: list[str] = []
        self.deséquilibres: list[str] = []
        self.evenements: list[str] = []
        self.scripts: list[dict[str, str]] = []
        self.liens: list[dict[str, str]] = []
        self.attributs_aria: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Enregistre la balise ouvrante et ses attributs remarquables."""
        valeurs = {nom: (valeur or "") for nom, valeur in attrs}
        if "id" in valeurs:
            self.identifiants.add(valeurs["id"])
        for nom in valeurs:
            if nom.startswith("on"):
                self.evenements.append(f"<{tag} {nom}>")
            if nom.startswith("aria-") or nom == "role":
                self.attributs_aria.add(nom)
        if tag == "script":
            self.scripts.append(valeurs)
        if tag == "link":
            self.liens.append(valeurs)
        if tag in BALISES_SUIVIES:
            self.pile.append(tag)

    def handle_endtag(self, tag: str) -> None:
        """Vérifie que la balise fermée est bien la dernière ouverte."""
        if tag not in BALISES_SUIVIES:
            return
        if not self.pile or self.pile[-1] != tag:
            self.deséquilibres.append(f"</{tag}> inattendu (pile: {self.pile[-3:]})")
            return
        self.pile.pop()


def lire(chemin_relatif: str) -> str:
    """Lit un fichier du portail en UTF-8."""
    return (PORTAL / chemin_relatif).read_text(encoding="utf-8")


def fichiers_js() -> list[Path]:
    """Tous les modules JavaScript du portail."""
    return sorted(PORTAL.rglob("*.js"))


def test_fichiers_du_portail_presents() -> None:
    """Chaque écran et chaque module du portail existe et n'est pas vide."""
    manquants = [nom for nom in FICHIERS_ATTENDUS if not (PORTAL / nom).is_file()]
    assert manquants == []
    vides = [nom for nom in FICHIERS_ATTENDUS if (PORTAL / nom).stat().st_size == 0]
    assert vides == []


def test_aucune_etape_de_build() -> None:
    """La SPA ne dépend d'aucun outil de build : ni package.json, ni node_modules."""
    assert not (PORTAL / "package.json").exists()
    assert not (PORTAL / "node_modules").exists()
    assert not list(PORTAL.rglob("*.ts"))
    assert not list(PORTAL.rglob("*.jsx"))


def analyser_accueil() -> AnalyseurHTML:
    """Analyse `portal/index.html` et rend le collecteur rempli."""
    analyseur = AnalyseurHTML()
    analyseur.feed(lire("index.html"))
    analyseur.close()
    return analyseur


def test_html_accueil_valide() -> None:
    """Le HTML d'accueil est bien formé, en anglais, et déclare ses métadonnées."""
    html = lire("index.html")
    analyseur = analyser_accueil()
    assert analyseur.deséquilibres == []
    assert analyseur.pile == []
    assert analyseur.evenements == []
    assert html.startswith("<!doctype html>")
    assert 'lang="en"' in html
    assert 'charset="utf-8"' in html
    assert 'name="viewport"' in html


def test_html_accueil_accesssible() -> None:
    """Repères, lien d'évitement et zone principale focusable sont présents."""
    analyseur = analyser_accueil()
    attendus = {"vue", "bandeau", "demo", "demo-client", "demo-role", "demo-note"}
    assert attendus <= analyseur.identifiants
    assert {"role", "aria-label", "aria-current"} & analyseur.attributs_aria
    html = lire("index.html")
    assert 'class="lien-evitement"' in html
    assert 'tabindex="-1"' in html
    assert 'role="alert"' in html or 'role="status"' in html


def test_identifiants_utilises_par_main_existent() -> None:
    """Tout identifiant interrogé par `main.js` existe dans le HTML d'accueil."""
    analyseur = analyser_accueil()
    source = lire("js/main.js")
    identifiants = set(re.findall(r'choisir\(document,\s*"#([A-Za-z0-9_-]+)"\)', source))
    assert identifiants
    assert identifiants <= analyseur.identifiants


def test_aucune_ressource_externe() -> None:
    """Aucun fichier du portail ne référence une URL absolue ou un CDN."""
    motif = re.compile(r"(https?:)?//[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    coupables = []
    for chemin in sorted(PORTAL.rglob("*")):
        if (
            chemin.is_file()
            and chemin.suffix in {".html", ".css", ".js", ".md"}
            and motif.search(chemin.read_text(encoding="utf-8"))
        ):
            coupables.append(chemin.relative_to(PORTAL).as_posix())
    assert coupables == []


def test_scripts_et_styles_locaux() -> None:
    """Le seul script est le module local ; la seule feuille est locale."""
    analyseur = analyser_accueil()
    assert len(analyseur.scripts) == 1
    script = analyseur.scripts[0]
    assert script.get("src") == "js/main.js"
    assert script.get("type") == "module"
    assert [lien.get("href") for lien in analyseur.liens] == ["assets/app.css"]


def test_aucun_eval_ni_html_dynamique_dangereux() -> None:
    """Aucune exécution de code dynamique et un seul point d'insertion HTML."""
    interdits = ("eval(", "new Function", "document.write", "insertAdjacentHTML", 'setTimeout("')
    occurrences_innerhtml = []
    for chemin in fichiers_js():
        source = chemin.read_text(encoding="utf-8")
        for interdit in interdits:
            assert interdit not in source, f"{interdit} trouvé dans {chemin.name}"
        if "innerHTML" in source:
            occurrences_innerhtml.append(chemin.name)
    assert occurrences_innerhtml == ["dom.js"]


def test_echappement_systematique() -> None:
    """`dom.js` échappe les cinq caractères sensibles et tout le reste passe par lui."""
    source = lire("js/dom.js")
    remplacements = ["&amp;", "&lt;", "&gt;", "&quot;", "&#39;"]
    for remplacement in remplacements:
        assert f'"{remplacement}"' in source, f"{remplacement} absent de la table"
    bloc = re.search(r"const ECHAPPEMENTS = \{(.*?)\};", source, re.S)
    assert bloc is not None
    entrees = [ligne for ligne in bloc.group(1).splitlines() if ":" in ligne]
    assert len(entrees) == 5
    assert "replace(/[&<>\"']/g" in source
    for chemin in sorted((PORTAL / "js/views").glob("*.js")):
        contenu = chemin.read_text(encoding="utf-8")
        assert 'from "../dom.js"' in contenu, f"{chemin.name} n'utilise pas dom.js"
        assert "innerHTML" not in contenu
