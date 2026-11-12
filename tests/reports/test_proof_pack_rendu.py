"""Tests des rendus du dossier de preuve : JSON pour la machine, Markdown pour l'humain.

Les deux vues partagent le même contenu scellé : ces tests vérifient qu'aucune
ne dit autre chose que l'autre, que le Markdown reste en anglais et lisible par
un non-technicien, et qu'aucun texte du plan de données n'y apparaît.
"""

from __future__ import annotations

import json

import pytest
from tests.reports import jeu_proof_pack as jeu

from jauge.reports.proof_pack import ORDRE_SECTIONS, ProofPack
from jauge.reports.proof_pack_rendu import (
    rendre_json_pack,
    rendre_markdown_pack,
    serialiser_pack,
)
from jauge.reports.proof_pack_verification import rejouer_verifications
from jauge.reports.scellement import serialiser_canonique, verifier_chaine

TITRES_ATTENDUS = (
    "# Proof pack —",
    "## 1. What this document is",
    "## 2. Protocol and versions",
    "## 3. Frozen sample",
    "## 4. Candidate results",
    "## 5. Deterministic checks",
    "## 6. Human review and judge agreement",
    "## 7. Expected saving",
    "## 8. Recommendation (Annex B format)",
    "## 9. Approval",
    "## 10. Seal: hash chain",
    "## 11. How a third party verifies this pack",
)


@pytest.fixture
def pack() -> ProofPack:
    """Pack nominal du jeu d'essai."""
    return jeu.construire_pack()


# --- Vue JSON ----------------------------------------------------------------


def test_json_valide_et_complet(pack: ProofPack) -> None:
    """La vue JSON se relit et porte toutes les sections scellées."""
    document = json.loads(rendre_json_pack(pack))
    assert document["id"] == pack.id
    assert document["version"] == pack.version
    # Clés triées dans la vue JSON : l'ordre de la chaîne est porté par le sceau.
    assert sorted(document["sections"]) == sorted(ORDRE_SECTIONS)
    assert [maillon["nom"] for maillon in document["scellement"]["sections"]] == list(
        ORDRE_SECTIONS
    )
    assert document["scellement"]["empreinte_pack"] == pack.empreinte_pack


def test_json_rejouable_par_un_tiers(pack: ProofPack) -> None:
    """Le texte JSON reçu par un tiers suffit à revérifier le pack."""
    texte = rendre_json_pack(pack)
    relu = ProofPack.depuis_document(json.loads(texte))
    assert verifier_chaine(json.loads(texte)).conforme
    assert relu.empreinte_pack == pack.empreinte_pack
    assert rejouer_verifications(json.loads(texte)).conforme


def test_json_compact_et_canonique_sont_equivalents(pack: ProofPack) -> None:
    """Indentation ou non : le contenu canonique reste identique."""
    compact = json.loads(rendre_json_pack(pack, indent=None))
    assert serialiser_canonique(compact) == serialiser_canonique(pack.vers_dict())
    assert serialiser_pack(pack) == serialiser_canonique(pack.vers_dict())


def test_json_cles_triees(pack: ProofPack) -> None:
    """Les clés sont triées : deux rendus du même pack sont octet pour octet égaux."""
    assert rendre_json_pack(pack) == rendre_json_pack(jeu.construire_pack())
    premier_niveau = list(json.loads(rendre_json_pack(pack)))
    assert premier_niveau == sorted(premier_niveau)


# --- Vue Markdown ------------------------------------------------------------


def test_markdown_contient_toutes_les_sections(pack: ProofPack) -> None:
    """Le document est structuré pour une lecture linéaire, en anglais."""
    markdown = rendre_markdown_pack(pack)
    for titre in TITRES_ATTENDUS:
        assert titre in markdown


def test_markdown_rappelle_les_chiffres_cles(pack: ProofPack) -> None:
    """Un non-technicien retrouve les chiffres décisifs sans lire le JSON."""
    markdown = rendre_markdown_pack(pack)
    assert jeu.CAMPAGNE_ID in markdown
    assert jeu.PETIT_MODELE in markdown
    assert jeu.GRAND_MODELE in markdown
    assert "95.00%" in markdown  # taux d'équivalence du candidat
    assert "93.33%" in markdown  # accord juge/humain (28 paires sur 30)
    assert f"{jeu.VOLUME_MENSUEL:,}" in markdown
    assert pack.empreinte_pack in markdown
    assert jeu.APPROBATEUR in markdown
    assert jeu.HORODATAGE_APPROBATION in markdown


def test_markdown_et_json_disent_la_meme_chose(pack: ProofPack) -> None:
    """Chaque valeur mise en avant dans le Markdown vient du JSON scellé."""
    markdown = rendre_markdown_pack(pack)
    document = json.loads(rendre_json_pack(pack))
    economie = document["sections"]["economie"]
    echantillon = document["sections"]["echantillon"]
    assert f"{economie['economie_mensuelle_attendue_usd']:,.2f}" in markdown
    assert f"{economie['part_msp_mensuelle_usd']:,.2f}" in markdown
    assert str(economie["baisse_pourcentage"]) in markdown
    assert f"**{echantillon['taille']}**" in markdown
    for maillon in document["scellement"]["sections"]:
        assert maillon["empreinte"] in markdown
        assert maillon["empreinte_cumulee"] in markdown


def test_markdown_sans_traces_de_python(pack: ProofPack) -> None:
    """Aucun `None`, aucun dictionnaire brut : le rendu est destiné à un humain."""
    markdown = rendre_markdown_pack(pack)
    assert "None" not in markdown
    assert "{'" not in markdown
    assert '{"' not in markdown


def test_markdown_sans_relecture_affiche_l_absence(pack: ProofPack) -> None:
    """Sans relecture humaine, le document le dit au lieu d'inventer un accord."""
    markdown = rendre_markdown_pack(jeu.construire_pack(relecture=None))
    assert "Judge/human agreement: **n/a**" in markdown
    assert "Pairs reviewed: 0" in markdown


def test_markdown_avec_verifications_rejouees(pack: ProofPack) -> None:
    """Le résultat du rejeu est publié avec le pack : « ne me croyez pas »."""
    resultat = rejouer_verifications(pack)
    markdown = rendre_markdown_pack(pack, resultat)
    assert "## 12. Replayed verifications" in markdown
    assert f"**{len(resultat.verifications)}**" in markdown
    assert "| OK |" in markdown or "OK" in markdown
    assert "FAIL" not in markdown


def test_markdown_signale_un_pack_incoherent(pack: ProofPack) -> None:
    """Un pack falsifié est rendu avec ses vérifications en échec."""
    pack.sections["economie"]["economie_mensuelle_attendue_usd"] = 9_999.99
    markdown = rendre_markdown_pack(pack, rejouer_verifications(pack))
    assert "INCONSISTENT PACK" in markdown
    assert "FAIL" in markdown
    assert "economie.attendue" in markdown


def test_markdown_sans_verifications_reste_valide(pack: ProofPack) -> None:
    """Sans résultat de rejeu, la section 12 n'apparaît pas."""
    assert "## 12." not in rendre_markdown_pack(pack)


# --- Confidentialité des rendus (ENF-CONF-01) --------------------------------


def test_aucun_texte_du_plan_de_donnees_dans_les_rendus(pack: ProofPack) -> None:
    """Ni le JSON ni le Markdown ne laissent passer un fragment de requête."""
    rendu_json = rendre_json_pack(pack)
    rendu_markdown = rendre_markdown_pack(pack, rejouer_verifications(pack))
    for texte in list(jeu.TEXTES_PLAN_DE_DONNEES.values())[:5]:
        assert texte not in rendu_json
        assert texte not in rendu_markdown
        assert texte[:20] not in rendu_json
        assert texte[:20] not in rendu_markdown
    assert "CONFIDENTIEL-CUSTOMER" not in rendu_json + rendu_markdown
    assert "REPONSE-INTERNE" not in rendu_json + rendu_markdown
