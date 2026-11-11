"""Tests du parseur de journaux d'invocation (§3.2, EF-ATT-05, ENF-PERF-01).

Ce que ces tests protègent : la **tolérance aux noms de champs** (§23 Q2 non
tranchée), le fait qu'un enregistrement illisible soit signalé sans faire tomber
la lecture, et la distinction point d'accès / niveau de service.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from tests.attribution.conftest import JOUR, JOURNAUX, MODELE_GRAND, MODELE_PETIT

from jauge.attribution.logs import (
    CHAMPS_ACCEPTES,
    EnregistrementInexploitable,
    RapportLecture,
    analyser_enregistrement,
    determiner_niveau_service,
    determiner_point_acces,
    iterer_enregistrements,
    iterer_rejouables,
    lire_journaux,
)
from jauge.attribution.tags import PointAcces
from jauge.models.pricing import ServiceTier

#: Enregistrement de référence : la forme « attendue » d'un journal Bedrock.
ENREGISTREMENT_TYPIQUE = {
    "schemaType": "ModelInvocationLog",
    "timestamp": "2026-11-09T08:12:00Z",
    "requestId": "req-0001",
    "modelId": MODELE_GRAND,
    "operation": "Converse",
    "input": {"inputTokenCount": 1200},
    "output": {"outputTokenCount": 80},
    "latencyMs": 620,
    "status": "success",
    "requestMetadata": {"application": "ticket-classifier", "team": "support"},
}


def test_lecture_du_journal_de_reference() -> None:
    """Le journal de référence est lu en entier, sans rejet ni champ inconnu."""
    rapport = RapportLecture()
    enregistrements = list(lire_journaux(JOURNAUX, rapport))

    assert rapport.lus == 6
    assert rapport.exploitables == 6
    assert rapport.nb_rejetes == 0
    assert len(enregistrements) == 6
    # Un seul champ inconnu dans la fixture : le contrôle de dérive fonctionne.
    assert rapport.champs_inconnus == {"champ_inconnu_du_futur": 1}


def test_enregistrement_typique_est_converti() -> None:
    """Modèle, jetons, latence, étiquettes et niveau de service sont extraits."""
    enregistrement = analyser_enregistrement(ENREGISTREMENT_TYPIQUE)

    assert enregistrement.request_id == "req-0001"
    assert enregistrement.model_id == MODELE_GRAND
    assert enregistrement.jour == JOUR
    assert enregistrement.jetons_entree == 1200
    assert enregistrement.jetons_sortie == 80
    assert enregistrement.latence_ms == 620
    assert enregistrement.niveau_service is ServiceTier.STANDARD
    assert enregistrement.point_acces is PointAcces.RUNTIME
    assert enregistrement.etiquettes.application == "ticket-classifier"
    assert enregistrement.etiquettes.equipe == "support"
    assert enregistrement.reussie is True


@pytest.mark.parametrize(
    ("variante", "attendu"),
    [
        ({"requestId": "r"}, "r"),
        ({"request_id": "r"}, "r"),
        ({"invocationId": "r"}, "r"),
        ({"x-amzn-RequestId": "r"}, "r"),
        ({"traceId": "r"}, "r"),
    ],
)
def test_variantes_du_nom_d_identifiant(variante: dict, attendu: str) -> None:
    """Les orthographes plausibles de l'identifiant de requête sont acceptées."""
    sans_id = {cle: valeur for cle, valeur in ENREGISTREMENT_TYPIQUE.items() if cle != "requestId"}
    enregistrement = analyser_enregistrement({**sans_id, **variante})

    assert enregistrement.request_id == attendu


@pytest.mark.parametrize(
    "variante",
    [
        {"inputTokenCount": 300, "outputTokenCount": 30},
        {"inputTokens": 300, "outputTokens": 30},
        {"promptTokenCount": 300, "completionTokenCount": 30},
        {"input_token_count": 300, "output_token_count": 30},
        {"usage": {"inputTokens": 300, "outputTokens": 30}},
        {"input": {"inputTokenCount": 300}, "output": {"outputTokenCount": 30}},
    ],
)
def test_variantes_des_compteurs_de_jetons(variante: dict) -> None:
    """Plates, en snake_case ou imbriquées, les deux compteurs se lisent pareil."""
    brut = {
        cle: valeur
        for cle, valeur in ENREGISTREMENT_TYPIQUE.items()
        if cle not in ("input", "output")
    }
    enregistrement = analyser_enregistrement({**brut, **variante})

    assert enregistrement.jetons_entree == 300
    assert enregistrement.jetons_sortie == 30


def test_jetons_de_cache_lus_dans_un_objet_imbrique() -> None:
    """Les jetons de cache (écriture et lecture) sont distingués (§8.2)."""
    enregistrement = analyser_enregistrement(
        {
            **ENREGISTREMENT_TYPIQUE,
            "usage": {
                "inputTokens": 900,
                "outputTokens": 60,
                "cacheReadInputTokens": 300,
                "cacheWriteInputTokens": 100,
            },
        }
    )

    assert enregistrement.jetons_cache_lecture == 300
    assert enregistrement.jetons_cache_ecriture == 100


def test_champs_inconnus_ignores_mais_comptes() -> None:
    """Un champ inconnu ne casse rien : il est ignoré et consigné (§23 Q2)."""
    rapport = RapportLecture()
    brut = {**ENREGISTREMENT_TYPIQUE, "nouveauChampDuFutur": {"sous": "structure"}}

    enregistrements = list(iterer_enregistrements([brut], rapport))

    assert len(enregistrements) == 1
    assert rapport.champs_inconnus == {"nouveauChampDuFutur": 1}


@pytest.mark.parametrize(
    ("brut", "motif"),
    [
        ("{ceci n'est pas du json", "JSON invalide"),
        ({"timestamp": "2026-11-09T08:00:00Z", "inputTokenCount": 10}, "model_id absent"),
        ({"modelId": MODELE_GRAND, "inputTokenCount": 10}, "horodatage absent ou illisible"),
        ({"modelId": MODELE_GRAND, "timestamp": "2026-11-09T08:00:00Z"}, "jetons illisibles"),
    ],
)
def test_enregistrements_inexploitables_signales(brut: object, motif: str) -> None:
    """Un enregistrement inutilisable est rejeté avec un motif, sans exception."""
    rapport = RapportLecture()

    assert list(iterer_enregistrements([brut], rapport)) == []
    assert rapport.nb_rejetes == 1
    assert motif in rapport.rejetes[0].motif
    assert list(rapport.motifs_rejet) == [rapport.rejetes[0].motif]


def test_analyser_enregistrement_leve_pour_un_brut_inexploitable() -> None:
    """L'appel direct signale l'inexploitable par une exception dédiée."""
    with pytest.raises(EnregistrementInexploitable, match="model_id absent"):
        analyser_enregistrement({"timestamp": "2026-11-09T08:00:00Z"})


def test_lecture_en_flux_ne_materialise_pas_la_source() -> None:
    """La lecture est un itérateur : rien n'est chargé avant consommation."""
    rapport = RapportLecture()
    flux = lire_journaux(JOURNAUX, rapport)

    assert iter(flux) is flux
    assert rapport.lus == 0  # aucune ligne lue avant le premier `next`

    premier = next(flux)
    assert rapport.lus == 1
    assert premier.request_id == "req-0001"


def test_horodatage_epoch_millisecondes() -> None:
    """Un horodatage epoch en millisecondes est converti en UTC."""
    enregistrement = analyser_enregistrement({**ENREGISTREMENT_TYPIQUE, "timestamp": 1794218400000})

    assert enregistrement.horodatage == datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    assert enregistrement.jour == date(2026, 11, 9)


def test_horodatage_sans_fuseau_suppose_utc() -> None:
    """Un horodatage sans fuseau est interprété en UTC, pas en heure locale."""
    enregistrement = analyser_enregistrement(
        {**ENREGISTREMENT_TYPIQUE, "timestamp": "2026-11-09T23:30:00"}
    )

    assert enregistrement.horodatage.tzinfo is UTC
    assert enregistrement.jour == date(2026, 11, 9)


def test_enveloppe_de_journal_deballee() -> None:
    """Un enregistrement enveloppé (`message`, `detail`) est déballé."""
    enregistrement = analyser_enregistrement({"message": ENREGISTREMENT_TYPIQUE})

    assert enregistrement.request_id == "req-0001"


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        ("standard", ServiceTier.STANDARD),
        ("Standard", ServiceTier.STANDARD),
        ("flex", ServiceTier.FLEX),
        ("FLEX", ServiceTier.FLEX),
        ("priority", ServiceTier.PRIORITY),
        ("reserved", ServiceTier.RESERVED),
        ("provisioned", ServiceTier.RESERVED),
        ("niveau-inconnu", ServiceTier.STANDARD),
        (None, ServiceTier.STANDARD),
    ],
)
def test_niveaux_de_service(valeur: object, attendu: ServiceTier) -> None:
    """Les quatre niveaux du §3.2 sont reconnus ; l'inconnu retombe sur Standard."""
    assert determiner_niveau_service(valeur) is attendu


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        (None, PointAcces.RUNTIME),
        ("bedrock-runtime", PointAcces.RUNTIME),
        ("Converse", PointAcces.RUNTIME),
        ("InvokeModelWithResponseStream", PointAcces.RUNTIME),
        ("bedrock-mantle", PointAcces.MANTLE),
        ("projects/abc/workspaces/def", PointAcces.MANTLE),
        ("quelque-chose", PointAcces.INCONNU),
    ],
)
def test_points_d_acces(valeur: object, attendu: PointAcces) -> None:
    """`bedrock-runtime` est journalisé, `bedrock-mantle` ne l'est pas (§3.2)."""
    point_acces = determiner_point_acces(valeur)

    assert point_acces is attendu
    assert point_acces.journalise is (attendu is PointAcces.RUNTIME)


def test_requetes_sensibles_exclues_du_rejeu() -> None:
    """Une requête marquée sensible n'est jamais rejouée (§13.1, ENF-CONF-01)."""
    rapport = RapportLecture()
    enregistrements = list(lire_journaux(JOURNAUX, rapport))

    assert rapport.sensibles == 1
    rejouables = list(iterer_rejouables(enregistrements))
    assert len(rejouables) == len(enregistrements) - 1
    assert all(not enregistrement.sensible for enregistrement in rejouables)


@pytest.mark.parametrize("valeur", [True, "true", "oui", "1", "TRUE"])
def test_drapeau_sensible_accepte_plusieurs_formes(valeur: object) -> None:
    """`sensitive`, `pii`, `true`/`oui`… marquent tous l'appel comme sensible."""
    enregistrement = analyser_enregistrement({**ENREGISTREMENT_TYPIQUE, "sensitive": valeur})

    assert enregistrement.sensible is True


def test_dictionnaire_des_champs_est_documente_et_non_vide() -> None:
    """Chaque champ canonique déclare au moins une variante (§23 Q2)."""
    attendus = {
        "request_id",
        "horodatage",
        "model_id",
        "jetons_entree",
        "jetons_sortie",
        "jetons_cache_lecture",
        "jetons_cache_ecriture",
        "niveau_service",
        "latence",
        "statut",
        "point_acces",
        "etiquettes",
        "sensible",
    }

    assert set(CHAMPS_ACCEPTES) == attendus
    assert all(variantes for variantes in CHAMPS_ACCEPTES.values())


def test_ligne_json_avec_modele_petit_est_lue() -> None:
    """Deux modèles différents cohabitent dans le même journal."""
    rapport = RapportLecture()
    modeles = {enregistrement.model_id for enregistrement in lire_journaux(JOURNAUX, rapport)}

    assert modeles == {MODELE_GRAND, MODELE_PETIT}


def test_journal_vide_ne_produit_rien(tmp_path: Path) -> None:
    """Un fichier vide n'est pas une erreur : zéro enregistrement, zéro rejet."""
    vide = tmp_path / "vide.jsonl"
    vide.write_text("\n\n", encoding="utf-8")
    rapport = RapportLecture()

    assert list(lire_journaux(vide, rapport)) == []
    assert rapport.lus == 0


def test_lignes_vides_ignorees(tmp_path: Path) -> None:
    """Les lignes vides d'un NDJSON ne comptent ni comme lu ni comme rejeté."""
    melange = tmp_path / "melange.jsonl"
    melange.write_text("\n" + json.dumps(ENREGISTREMENT_TYPIQUE) + "\n\n", encoding="utf-8")
    rapport = RapportLecture()

    assert len(list(lire_journaux(melange, rapport))) == 1
    assert rapport.lus == 1
