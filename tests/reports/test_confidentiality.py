"""Tests de confidentialité et de cloisonnement du rapport (ENF-CONF-01, ENF-SEC-02).

Le rapport mensuel sort du plan de données : il ne doit transporter que des
agrégats, des scores et des identifiants. Trois barrières sont vérifiées :

1. la liste des champs du rapport est close (un champ ajouté sans revue casse
   le test) ;
2. aucun nom de champ ne désigne un texte de requête ou de réponse ;
3. `construire_rapport` n'appelle que les lectures d'agrégats du store : il ne
   peut pas atteindre les objets du plan de données, qui n'y sont d'ailleurs
   pas exposés.
"""

from __future__ import annotations

import json
from datetime import date

from jauge.reports.builder import construire_rapport
from jauge.reports.storage import rendre_json, rendre_markdown
from jauge.store.memory import MemoryStore

MOIS = (2026, 10)

# Champs autorisés au premier niveau du rapport (contrat du portail).
CHAMPS_AUTORISES = {
    "schema_version",
    "id",
    "tenant_id",
    "tenant_nom",
    "annee",
    "mois",
    "periode_debut",
    "periode_fin",
    "devise",
    "cout_total_usd",
    "nombre_requetes",
    "couverture_attribution_pourcentage",
    "par_application",
    "par_equipe",
    "par_modele",
    "tendance",
    "ecart_facture",
    "leviers",
    "economies",
    "recommandations_en_attente",
    "bascules",
    "methodologie",
    "synthese",
}

# Fragments interdits dans un nom de champ : ils désigneraient une donnée de requête.
FRAGMENTS_INTERDITS = ("texte", "reponse", "prompt", "message", "contenu", "explanation")

# Lectures autorisées du store : agrégats, leviers, recos, bascules, vérifications.
LECTURES_AUTORISEES = {
    "obtenir_tenant",
    "lister_applications",
    "lister_agregats",
    "synthese_cout",
    "lister_rapprochements",
    "lister_leviers",
    "lister_recommandations",
    "lister_bascules",
    "lister_verifications",
    "lister_campagnes",
}


class StoreEspion:
    """Store qui délègue au store en mémoire et note chaque lecture."""

    def __init__(self, interieur: MemoryStore) -> None:
        self.interieur = interieur
        self.appels: list[str] = []

    def __getattr__(self, nom: str):
        attribut = getattr(self.interieur, nom)

        def enveloppe(*args: object, **kwargs: object):
            self.appels.append(nom)
            return attribut(*args, **kwargs)

        return enveloppe


def _cles(charge: object) -> set[str]:
    """Toutes les clés d'une structure JSON, à tous les niveaux."""
    cles: set[str] = set()
    if isinstance(charge, dict):
        for cle, valeur in charge.items():
            cles.add(str(cle))
            cles |= _cles(valeur)
    elif isinstance(charge, list):
        for element in charge:
            cles |= _cles(element)
    return cles


def test_champs_du_rapport_fermes(store_peuple: MemoryStore) -> None:
    """Le premier niveau du JSON est exactement le contrat attendu."""
    charge = json.loads(rendre_json(construire_rapport(store_peuple, "t-demo-a", *MOIS)))
    assert set(charge) == CHAMPS_AUTORISES


def test_aucun_champ_de_donnee_de_requete(store_peuple: MemoryStore) -> None:
    """Aucune clé du rapport ne désigne un texte de requête ou de réponse."""
    charge = json.loads(rendre_json(construire_rapport(store_peuple, "t-demo-a", *MOIS)))
    for cle in _cles(charge):
        minuscule = cle.lower()
        for fragment in FRAGMENTS_INTERDITS:
            assert fragment not in minuscule, f"champ suspect dans le rapport : {cle}"


def test_aucun_texte_de_requete_dans_les_rendus(store_peuple: MemoryStore) -> None:
    """Le Markdown comme le JSON sont exempts de contenu de requête."""
    rapport = construire_rapport(store_peuple, "t-demo-a", *MOIS)
    for rendu in (rendre_json(rapport), rendre_markdown(rapport)):
        minuscule = rendu.lower()
        assert "texte_requete" not in minuscule
        assert "reponse_candidate" not in minuscule
        assert "reponse_reference" not in minuscule
        assert "score_equivalence" not in minuscule


def test_le_builder_ne_lit_que_des_agregats(store_peuple: MemoryStore) -> None:
    """Les seules lectures du rapport sont des agrégats et des états."""
    espion = StoreEspion(store_peuple)
    construire_rapport(espion, "t-demo-a", *MOIS)
    assert espion.appels
    assert set(espion.appels) <= LECTURES_AUTORISEES


def test_le_builder_ne_lit_rien_hors_du_client(store_peuple: MemoryStore, tenant_b) -> None:
    """Toute lecture porte le tenant demandé (ENF-SEC-02)."""
    store_peuple.enregistrer_tenant(tenant_b)
    espion = StoreEspion(store_peuple)
    charge = json.loads(rendre_json(construire_rapport(espion, tenant_b.id, *MOIS)))
    assert charge["tenant_id"] == tenant_b.id
    assert charge["cout_total_usd"] == 0.0
    assert "ticket-classifier" not in json.dumps(charge)
    assert espion.appels  # le rapport a bien interrogé le store, filtré par client


def test_rapport_ne_contient_pas_d_identifiants_de_requete(store_peuple: MemoryStore) -> None:
    """Aucun identifiant de requête rejouée ne fuit dans le rapport."""
    charge = json.loads(rendre_json(construire_rapport(store_peuple, "t-demo-a", *MOIS)))
    texte = json.dumps(charge)
    assert "requete_id" not in texte
    assert "campagne_id" not in texte


def test_periode_vide_ne_produit_pas_de_donnees(store_peuple: MemoryStore) -> None:
    """Un mois sans agrégat donne un rapport vide, jamais celui d'un autre mois."""
    rapport = construire_rapport(store_peuple, "t-demo-a", 2026, 8)
    assert rapport.cout_total_usd == 0.0
    assert rapport.nombre_requetes == 0
    assert rapport.par_application == []
    assert rapport.tendance.cout_mois_precedent_usd == 0.0
    assert rapport.tendance.sens == "stable"
    assert rapport.periode_fin == date(2026, 8, 31)
