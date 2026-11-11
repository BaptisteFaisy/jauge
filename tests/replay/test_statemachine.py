"""Tests de la définition Step Functions du rejeu (EF-REJ-03, ENF-CONF-01, ENF-OBS-01)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jauge.common.errors import ValidationError
from jauge.replay.statemachine import (
    ETAT_ECHANTILLONNAGE,
    ETAT_ECHEC,
    ETAT_EVALUATION,
    ETAT_REJEU,
    ETAT_REJOUER_REQUETE,
    ETAT_STOCKAGE,
    ETAT_SUCCES,
    NIVEAU_SERVICE_REJEU,
    ParametresMachine,
    definition,
    export_json,
    role_arn,
)

RACINE = Path(__file__).resolve().parents[2]


def test_les_quatre_etats_du_parcours_sont_presents() -> None:
    """§8.4 : échantillonnage → rejeu → évaluation → stockage, plus les états finaux."""
    etats = definition()["States"]
    for nom in (ETAT_ECHANTILLONNAGE, ETAT_REJEU, ETAT_EVALUATION, ETAT_STOCKAGE):
        assert nom in etats
    assert etats[ETAT_SUCCES]["Type"] == "Succeed"
    assert etats[ETAT_ECHEC]["Type"] == "Fail"


def test_chaine_des_etats() -> None:
    """Le parcours s'enchaîne dans l'ordre du cahier des charges."""
    machine = definition()
    etats = machine["States"]
    assert machine["StartAt"] == ETAT_ECHANTILLONNAGE
    assert etats[ETAT_ECHANTILLONNAGE]["Next"] == ETAT_REJEU
    assert etats[ETAT_REJEU]["Next"] == ETAT_EVALUATION
    assert etats[ETAT_EVALUATION]["Next"] == ETAT_STOCKAGE
    assert etats[ETAT_STOCKAGE]["Next"] == ETAT_SUCCES


def test_rejeu_est_une_map_au_niveau_flex() -> None:
    """EF-REJ-03 : le rejeu est un Map sur les requêtes, exécuté au niveau Flex."""
    rejeu = definition()["States"][ETAT_REJEU]
    assert rejeu["Type"] == "Map"
    assert rejeu["ItemsPath"] == "$.echantillon.requetes"
    assert rejeu["ItemProcessor"]["StartAt"] == ETAT_REJOUER_REQUETE
    assert rejeu["MaxConcurrency"] == ParametresMachine().max_concurrence
    corps = rejeu["ItemProcessor"]["States"][ETAT_REJOUER_REQUETE]
    assert corps["Parameters"]["Payload"]["niveau_service"] == NIVEAU_SERVICE_REJEU


def test_reprise_sur_erreur_sur_chaque_tache() -> None:
    """ENF-OBS-01 : chaque tâche a une reprise et un filet vers l'état d'échec."""
    etats = definition()["States"]
    taches = [
        etats[ETAT_ECHANTILLONNAGE],
        etats[ETAT_REJEU],
        etats[ETAT_EVALUATION],
        etats[ETAT_STOCKAGE],
    ]
    for tache in taches:
        assert tache["Retry"], "une reprise est nécessaire sur toute tâche réseau"
        assert tache["Catch"][0]["Next"] == ETAT_ECHEC
        assert "States.ALL" in tache["Catch"][0]["ErrorEquals"]


def test_aucun_texte_ne_remonte_du_rejeu() -> None:
    """ENF-CONF-01 : les sélecteurs de résultat ne retiennent que mesures et identifiants."""
    etats = definition()["States"]
    corps = etats[ETAT_REJEU]["ItemProcessor"]["States"][ETAT_REJOUER_REQUETE]
    selection = corps["ResultSelector"]
    assert "requete_id.$" in selection
    assert "mesures.$" in selection
    assert not any("texte" in cle for cle in selection)
    assert "texte_requete" not in json.dumps(selection)


def test_role_iam_expose() -> None:
    """ENF-SEC-01 : la machine nomme le rôle sous lequel elle s'exécute."""
    machine = ParametresMachine(role_arn="arn:aws:iam::123456789012:role/jauge-rejeu")
    assert role_arn(machine) == "arn:aws:iam::123456789012:role/jauge-rejeu"


def test_export_json_est_valide() -> None:
    """La définition s'exporte en JSON strictement valide."""
    texte = export_json()
    machine = json.loads(texte)
    assert machine["StartAt"] == ETAT_ECHANTILLONNAGE
    assert len(machine["States"]) == 6


def test_export_json_ecrit_un_fichier_hors_infra(tmp_path: Path) -> None:
    """L'export écrit un fichier si on le demande, ailleurs que sous `infra/`."""
    destination = tmp_path / "replay.asl.json"
    texte = export_json(destination=destination)
    assert destination.read_text(encoding="utf-8").strip() == texte.strip()
    assert json.loads(destination.read_text(encoding="utf-8"))["States"]


def test_export_refuse_infra(tmp_path: Path) -> None:
    """Garde-fou : un `*.asl.json` sous `infra/` casserait le job CI `iac`."""
    with pytest.raises(ValidationError):
        export_json(destination=tmp_path / "infra" / "replay.asl.json")


def test_aucune_definition_sous_infra() -> None:
    """Aucune définition Step Functions ne doit traîner sous `infra/` (job `iac`)."""
    infra = RACINE / "infra"
    if not infra.exists():
        return
    assert list(infra.rglob("*.asl.json")) == []
