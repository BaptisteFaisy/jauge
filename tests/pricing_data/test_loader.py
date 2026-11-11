"""Tests du chargeur de la table des tarifs (EF-ATT-04, §14).

Couvre le cas nominal, chaque refus de validation exigé (doublon, montant
négatif ou non numérique, devise absente, date mal formée) et les défauts
hérités du fichier. Aucun accès réseau : les tables sont écrites dans `tmp_path`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE, REGION

from jauge.common.errors import ValidationError
from jauge.engine.calculator import TableTarifs
from jauge.models.pricing import ServiceTier
from jauge.pricing_data import charger_table, charger_tarifs, lignes


def ligne_valide(**surcharges: Any) -> dict[str, Any]:
    """Ligne de tarif valide, surchargeable champ par champ."""
    ligne: dict[str, Any] = {
        "modele_id": GRAND_MODELE,
        "region": REGION,
        "niveau": "standard",
        "date_effet": "2026-01-01",
        "prix_entree_1k": 0.015,
        "prix_sortie_1k": 0.075,
        "prix_cache_ecriture_1k": 0.01875,
        "prix_cache_lecture_1k": 0.0015,
        "devise": "USD",
    }
    ligne.update(surcharges)
    return ligne


def ecrire(tmp_path: Path, document: dict[str, Any]) -> Path:
    """Écrit un document JSON dans un fichier temporaire."""
    chemin = tmp_path / "table.json"
    chemin.write_text(json.dumps(document), encoding="utf-8")
    return chemin


def document(*lignes_: dict[str, Any]) -> dict[str, Any]:
    """Document complet (métadonnées + lignes)."""
    return {
        "region": REGION,
        "devise": "USD",
        "tarifs": list(lignes_),
    }


def test_chargement_nominal(tmp_path: Path) -> None:
    """Deux lignes valides donnent une table consultable par le socle."""
    chemin = ecrire(tmp_path, document(ligne_valide(), ligne_valide(niveau="flex")))
    table = charger_table(chemin)
    assert isinstance(table, TableTarifs)
    assert len(lignes(table)) == 2
    tarif = table.trouver(GRAND_MODELE, REGION, ServiceTier.FLEX, date(2026, 6, 1))
    assert tarif.prix_entree_1k == pytest.approx(0.015)
    assert tarif.devise == "USD"


def test_region_et_devise_heritees_du_fichier(tmp_path: Path) -> None:
    """Une ligne sans région ni devise prend celles du document."""
    brute = ligne_valide()
    del brute["region"]
    del brute["devise"]
    tarif = charger_tarifs(ecrire(tmp_path, document(brute)))[0]
    assert tarif.region == REGION
    assert tarif.devise == "USD"


def test_cache_absent_vaut_zero(tmp_path: Path) -> None:
    """Les prix de cache sont facultatifs et valent 0 par défaut."""
    brute = ligne_valide()
    del brute["prix_cache_ecriture_1k"]
    del brute["prix_cache_lecture_1k"]
    tarif = charger_tarifs(ecrire(tmp_path, document(brute)))[0]
    assert tarif.prix_cache_ecriture_1k == 0.0
    assert tarif.prix_cache_lecture_1k == 0.0


def test_niveau_absent_vaut_standard(tmp_path: Path) -> None:
    """Sans niveau de service, la ligne est Standard."""
    brute = ligne_valide()
    del brute["niveau"]
    tarif = charger_tarifs(ecrire(tmp_path, document(brute)))[0]
    assert tarif.niveau == ServiceTier.STANDARD


def test_doublon_rejete(tmp_path: Path) -> None:
    """Deux lignes de même (modèle, région, niveau, date) sont refusées."""
    chemin = ecrire(tmp_path, document(ligne_valide(), ligne_valide(prix_entree_1k=0.02)))
    with pytest.raises(ValidationError) as capture:
        charger_table(chemin)
    assert "double" in capture.value.message
    assert "2026-01-01" in capture.value.message
    assert capture.value.details["niveau"] == "standard"
    assert capture.value.details["region"] == REGION


def test_meme_modele_dates_differentes_accepte(tmp_path: Path) -> None:
    """Un changement de tarif crée une nouvelle ligne : pas un doublon."""
    chemin = ecrire(
        tmp_path,
        document(ligne_valide(), ligne_valide(date_effet="2026-07-01", prix_entree_1k=0.02)),
    )
    assert len(charger_tarifs(chemin)) == 2


def test_montant_negatif_rejete(tmp_path: Path) -> None:
    """Un prix négatif est refusé (message désignant la ligne)."""
    chemin = ecrire(tmp_path, document(ligne_valide(prix_sortie_1k=-0.1)))
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(chemin)
    assert "négatif" in capture.value.message
    assert "prix_sortie_1k" in capture.value.message
    assert capture.value.details["ligne"] == 1


def test_montant_non_numerique_rejete(tmp_path: Path) -> None:
    """Un prix textuel est refusé."""
    chemin = ecrire(tmp_path, document(ligne_valide(prix_entree_1k="cher")))
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(chemin)
    assert "non numérique" in capture.value.message


def test_montant_booleen_rejete(tmp_path: Path) -> None:
    """`true` n'est pas un montant (booléen refusé comme non numérique)."""
    chemin = ecrire(tmp_path, document(ligne_valide(prix_entree_1k=True)))
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(chemin)
    assert "non numérique" in capture.value.message


def test_prix_obligatoire_absent_rejete(tmp_path: Path) -> None:
    """Sans prix de sortie, la ligne est refusée."""
    brute = ligne_valide()
    del brute["prix_sortie_1k"]
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(ecrire(tmp_path, document(brute)))
    assert "prix_sortie_1k" in capture.value.message


def test_devise_absente_rejetee(tmp_path: Path) -> None:
    """Ni la ligne ni le fichier ne déclarent de devise : refus."""
    brute = ligne_valide()
    del brute["devise"]
    chemin = ecrire(tmp_path, {"region": REGION, "tarifs": [brute]})
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(chemin)
    assert "devise" in capture.value.message


def test_date_mal_formee_rejetee(tmp_path: Path) -> None:
    """Une date d'effet non ISO est refusée avec le format attendu."""
    chemin = ecrire(tmp_path, document(ligne_valide(date_effet="01/01/2026")))
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(chemin)
    assert "date d'effet mal formée" in capture.value.message
    assert "AAAA-MM-JJ" in capture.value.message


def test_date_absente_rejetee(tmp_path: Path) -> None:
    """La date d'effet est obligatoire (paramètre daté, EF-ATT-04)."""
    brute = ligne_valide()
    del brute["date_effet"]
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(ecrire(tmp_path, document(brute)))
    assert "date_effet" in capture.value.message


def test_niveau_inconnu_rejete(tmp_path: Path) -> None:
    """Un niveau hors de `ServiceTier` est refusé, la liste est rappelée."""
    chemin = ecrire(tmp_path, document(ligne_valide(niveau="turbo")))
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(chemin)
    assert "turbo" in capture.value.message
    assert "standard" in capture.value.message


def test_modele_absent_rejete(tmp_path: Path) -> None:
    """Sans identifiant de modèle, la ligne est refusée."""
    brute = ligne_valide()
    del brute["modele_id"]
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(ecrire(tmp_path, document(brute)))
    assert "modele_id" in capture.value.message


def test_region_absente_partout_rejetee(tmp_path: Path) -> None:
    """Sans région ni dans le fichier ni dans la ligne, refus."""
    brute = ligne_valide()
    del brute["region"]
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(ecrire(tmp_path, {"devise": "USD", "tarifs": [brute]}))
    assert "region" in capture.value.message


def test_liste_tarifs_vide_rejetee(tmp_path: Path) -> None:
    """Une table sans ligne est une erreur de configuration, pas une table."""
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(ecrire(tmp_path, {"region": REGION, "devise": "USD", "tarifs": []}))
    assert "non vide" in capture.value.message


def test_ligne_non_objet_rejetee(tmp_path: Path) -> None:
    """Une ligne qui n'est pas un objet JSON est refusée."""
    with pytest.raises(ValidationError) as capture:
        charger_tarifs(ecrire(tmp_path, {"region": REGION, "tarifs": ["standard"]}))
    assert "objet JSON" in capture.value.message


def test_json_invalide_rejete(tmp_path: Path) -> None:
    """Un fichier non JSON lève une erreur explicite, pas un plantage de parsing."""
    chemin = tmp_path / "casse.json"
    chemin.write_text("{ pas du json", encoding="utf-8")
    with pytest.raises(ValidationError) as capture:
        charger_table(chemin)
    assert "JSON invalide" in capture.value.message


def test_fichier_absent_rejete(tmp_path: Path) -> None:
    """Un chemin inexistant est signalé comme introuvable."""
    with pytest.raises(ValidationError) as capture:
        charger_table(tmp_path / "absent.json")
    assert "introuvable" in capture.value.message


def test_lignes_rend_une_copie(tmp_path: Path) -> None:
    """`lignes()` ne donne pas la main sur la liste interne de la table."""
    table = charger_table(ecrire(tmp_path, document(ligne_valide())))
    copie = lignes(table)
    copie.clear()
    assert len(lignes(table)) == 1


def test_trois_modeles_deux_niveaux(tmp_path: Path) -> None:
    """Le format supporte la maille de la démo : 3 modèles × 2 niveaux."""
    brutes = [
        ligne_valide(modele_id=modele, niveau=niveau)
        for modele in (GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE)
        for niveau in ("standard", "flex")
    ]
    assert len(charger_tarifs(ecrire(tmp_path, document(*brutes)))) == 6
