"""Tests des contrôles déterministes par type de tâche (EF-EVA-02, §13.3)."""

from __future__ import annotations

import pytest

from jauge.common.errors import ValidationError
from jauge.evaluation.checks import (
    CONTROLES_PAR_TACHE,
    ContexteControle,
    controles_pour,
    echecs_critiques,
    etiquette_de,
    executer_controles,
    resume_controles,
)

REFERENCE_CLASSEMENT = '{"etiquette": "acces", "confiance": 0.9}'
CANDIDAT_IDENTIQUE = '{"etiquette": "acces", "confiance": 0.8}'


def _par_nom(resultats: list, nom: str):  # type: ignore[no-untyped-def]
    """Retrouve le résultat d'un contrôle par son nom."""
    return next(resultat for resultat in resultats if resultat.nom == nom)


def test_json_invalide_detecte() -> None:
    """Une réponse qui n'est pas du JSON est rejetée, avec position de l'erreur."""
    resultats = executer_controles("extraction", "{}", "pas du json du tout {")
    controle = _par_nom(resultats, "json_valide")
    assert controle.ok is False
    assert "JSON invalide" in controle.motif
    assert controle.critique is True


def test_json_valide_accepte() -> None:
    """Un document JSON lisible passe le contrôle de format."""
    resultats = executer_controles("classification", REFERENCE_CLASSEMENT, CANDIDAT_IDENTIQUE)
    assert _par_nom(resultats, "json_valide").ok is True


def test_etiquette_differente_detectee() -> None:
    """Une étiquette qui change est un échec critique : le client reçoit un mauvais routage."""
    resultats = executer_controles(
        "classification", REFERENCE_CLASSEMENT, '{"etiquette": "facturation"}'
    )
    controle = _par_nom(resultats, "etiquette_identique")
    assert controle.ok is False
    assert "facturation" in controle.motif
    assert "acces" in controle.motif


def test_etiquette_identique_acceptee() -> None:
    """Même étiquette, valeurs secondaires différentes : le contrôle passe."""
    resultats = executer_controles("classification", REFERENCE_CLASSEMENT, CANDIDAT_IDENTIQUE)
    assert _par_nom(resultats, "etiquette_identique").ok is True


def test_etiquette_en_texte_libre_compare_la_premiere_ligne() -> None:
    """Hors JSON, l'étiquette est la première ligne non vide, insensible à la casse."""
    assert etiquette_de("\n  ACCES \n suite") == "acces"
    assert etiquette_de('{"label": "Acces"}') == "acces"
    assert etiquette_de("   ") is None


def test_etiquette_absente_chez_le_candidat_est_un_echec() -> None:
    """Une réponse vide ne peut pas porter la même étiquette que la référence."""
    resultats = executer_controles("classification", REFERENCE_CLASSEMENT, "   ")
    assert _par_nom(resultats, "etiquette_identique").ok is False


def test_reference_sans_etiquette_desactive_le_controle() -> None:
    """Sans étiquette de référence, le contrôle est neutralisé (non critique)."""
    resultats = executer_controles("classification", "", CANDIDAT_IDENTIQUE)
    controle = _par_nom(resultats, "etiquette_identique")
    assert controle.ok is True
    assert controle.critique is False


def test_schema_respecte_detecte_les_cles_manquantes() -> None:
    """Une clé attendue absente est signalée nommément."""
    contexte = ContexteControle(type_tache="extraction", cles_attendues=("etiquette", "confiance"))
    resultats = executer_controles(
        "extraction", REFERENCE_CLASSEMENT, '{"etiquette": "acces"}', contexte
    )
    controle = _par_nom(resultats, "schema_respecte")
    assert controle.ok is False
    assert "confiance" in controle.motif


def test_schema_respecte_detecte_les_types_inattendus() -> None:
    """Une valeur de type différent de la référence est un échec de schéma."""
    contexte = ContexteControle(type_tache="extraction", cles_attendues=("confiance",))
    resultats = executer_controles(
        "extraction", REFERENCE_CLASSEMENT, '{"confiance": "elevee"}', contexte
    )
    controle = _par_nom(resultats, "schema_respecte")
    assert controle.ok is False
    assert "str au lieu de float" in controle.motif


def test_schema_respecte_sans_cle_attendue_est_neutre() -> None:
    """Aucune clé déclarée : le contrôle ne bloque rien."""
    resultats = executer_controles("extraction", REFERENCE_CLASSEMENT, "{}")
    assert _par_nom(resultats, "schema_respecte").ok is True


def test_longueur_bornee_detecte_les_extremes() -> None:
    """Trop court est critique, trop long est un avertissement."""
    contexte = ContexteControle(longueur_min=5, longueur_max=20)
    court = executer_controles("redaction", "une réponse", "", contexte)[0]
    long = executer_controles("redaction", "une réponse", "x" * 50, contexte)[0]
    assert court.ok is False and court.critique is True
    assert long.ok is False and long.critique is False


def test_registre_des_controles_par_tache() -> None:
    """Chaque type de tâche connue a ses contrôles ; un type inconnu retombe sur `general`."""
    assert "classification" in CONTROLES_PAR_TACHE
    assert "extraction" in CONTROLES_PAR_TACHE
    assert controles_pour("type-inconnu") == CONTROLES_PAR_TACHE["general"]


def test_type_de_tache_vide_refuse() -> None:
    """Sans type de tâche, aucun contrôle n'est applicable : refus explicite."""
    with pytest.raises(ValidationError):
        executer_controles("  ", REFERENCE_CLASSEMENT, CANDIDAT_IDENTIQUE)


def test_echecs_critiques_et_resume() -> None:
    """Seuls les échecs critiques bloquent, et le résumé ne cite que leurs motifs."""
    resultats = executer_controles("classification", REFERENCE_CLASSEMENT, "pas du json {")
    critiques = echecs_critiques(resultats)
    assert {controle.nom for controle in critiques} == {"json_valide", "etiquette_identique"}
    assert "json_valide" in resume_controles(resultats)
