"""Tests de la table de démonstration embarquée (EF-ATT-04, §23 Q3).

La démo et les tests partagent une seule source de vérité : la table JSON du
dépôt doit contenir exactement les lignes de la fixture `tarifs` du socle
(mêmes identifiants de modèles, mêmes montants), plus la maille complète
3 modèles × 2 niveaux × 1 région annoncée par `docs/MODULES.md`.

Les montants sont des **valeurs de démonstration**, jamais présentées comme les
tarifs officiels d'Amazon (voir le champ `_avertissement` du fichier).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE, REGION

from jauge.common.errors import ValidationError
from jauge.engine.calculator import calculer_cout_requete
from jauge.models.pricing import MetriquesRequete, ServiceTier, TarifModele
from jauge.pricing_data import NOM_FICHIER_DEMO, chemin_table_demo, lignes, table_demo

CHAMPS_MONTANT = (
    "prix_entree_1k",
    "prix_sortie_1k",
    "prix_cache_ecriture_1k",
    "prix_cache_lecture_1k",
)


def cle(tarif: TarifModele) -> tuple[str, str, ServiceTier, date]:
    """Clé d'unicité d'une ligne de tarif."""
    return (tarif.modele_id, tarif.region, tarif.niveau, tarif.date_effet)


def test_chemin_du_fichier_embarque() -> None:
    """Le fichier de démonstration est trouvé depuis le module (sans réseau)."""
    chemin = chemin_table_demo()
    assert chemin.is_file()
    assert chemin.name == NOM_FICHIER_DEMO


def test_avertissement_present_dans_le_fichier() -> None:
    """Le fichier dit explicitement que les montants ne sont pas officiels."""
    document = json.loads(chemin_table_demo().read_text(encoding="utf-8"))
    avertissement = document["_avertissement"]
    assert "démonstration" in avertissement
    assert "PAS les tarifs officiels" in avertissement
    assert "source" in document
    assert "Q3" in document["source"]


def test_maille_de_la_demo() -> None:
    """3 modèles × 2 niveaux × 1 région, tous en eu-west-1."""
    lignes_demo = lignes(table_demo())
    modeles = {tarif.modele_id for tarif in lignes_demo}
    assert modeles == {GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE}
    assert {tarif.region for tarif in lignes_demo} == {REGION}
    assert {tarif.niveau for tarif in lignes_demo} == {ServiceTier.STANDARD, ServiceTier.FLEX}
    assert len(lignes_demo) == 6


def test_montants_identiques_a_la_fixture_du_socle(tarifs: list[TarifModele]) -> None:
    """Chaque ligne de la fixture `tarifs` existe à l'identique dans la table JSON."""
    index = {cle(tarif): tarif for tarif in lignes(table_demo())}
    for attendu in tarifs:
        assert cle(attendu) in index, f"ligne absente de la table : {cle(attendu)}"
        trouve = index[cle(attendu)]
        for champ in CHAMPS_MONTANT:
            assert getattr(trouve, champ) == pytest.approx(getattr(attendu, champ)), (
                f"{champ} différent pour {cle(attendu)}"
            )
        assert trouve.devise == attendu.devise


def test_table_couvre_la_maille_complete(tarifs: list[TarifModele]) -> None:
    """La fixture du socle est incomplète : la table ajoute le Flex du modèle moyen."""
    cles_fixture = {cle(tarif) for tarif in tarifs}
    cles_table = {cle(tarif) for tarif in lignes(table_demo())}
    assert cles_fixture <= cles_table
    ajoutees = cles_table - cles_fixture
    assert ajoutees == {(MOYEN_MODELE, REGION, ServiceTier.FLEX, date(2026, 1, 1))}


def test_date_effet_de_la_table() -> None:
    """La table est datée : une seule date d'effet, le 1er janvier 2026."""
    assert {tarif.date_effet for tarif in lignes(table_demo())} == {date(2026, 1, 1)}


def test_cout_calcule_depuis_la_table_de_demo() -> None:
    """Le socle calcule un coût cohérent depuis la table (Annexe D, ENF-IA-01)."""
    table = table_demo()
    tarif = table.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2026, 6, 1))
    cout = calculer_cout_requete(
        tarif, MetriquesRequete(jetons_entree=1000, jetons_sortie=500, jetons_cache_lecture=2000)
    )
    assert cout.cout_entree == pytest.approx(0.015)
    assert cout.cout_sortie == pytest.approx(0.0375)
    assert cout.cout_cache_lecture == pytest.approx(0.003)
    assert cout.cout_total == pytest.approx(0.0555)


def test_flex_moins_cher_que_standard() -> None:
    """Le niveau Flex est facturé moitié prix dans la table de démonstration."""
    table = table_demo()
    for modele in (GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE):
        standard = table.trouver(modele, REGION, ServiceTier.STANDARD, date(2026, 6, 1))
        flex = table.trouver(modele, REGION, ServiceTier.FLEX, date(2026, 6, 1))
        assert flex.prix_entree_1k == pytest.approx(standard.prix_entree_1k / 2)
        assert flex.prix_sortie_1k == pytest.approx(standard.prix_sortie_1k / 2)


def test_variable_d_environnement_prime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`JAUGE_TABLE_TARIFS` permet de pointer une autre table (paramètre daté)."""
    document = {
        "region": REGION,
        "devise": "USD",
        "tarifs": [
            {
                "modele_id": PETIT_MODELE,
                "niveau": "standard",
                "date_effet": "2026-02-01",
                "prix_entree_1k": 0.0005,
                "prix_sortie_1k": 0.002,
            }
        ],
    }
    chemin = tmp_path / "autre-table.json"
    chemin.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("JAUGE_TABLE_TARIFS", str(chemin))
    assert chemin_table_demo() == chemin
    assert len(lignes(table_demo())) == 1


def test_table_introuvable_signalee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucun candidat existant : erreur explicite, jamais un silence."""
    monkeypatch.setattr(
        "jauge.pricing_data.seed.chemins_candidats",
        lambda: [tmp_path / "absent.json"],
    )
    with pytest.raises(ValidationError) as capture:
        chemin_table_demo()
    assert "introuvable" in capture.value.message
    assert "JAUGE_TABLE_TARIFS" in capture.value.message
