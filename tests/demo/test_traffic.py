"""Tests du générateur de trafic synthétique (§18.1).

Vérifient le déterminisme (même graine ⇒ mêmes enregistrements), le format des
journaux d'invocation, les profils réalistes et les signaux de leviers
attendus.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from demo.profils import PROFILS
from demo.traffic import generer_trafic, lignes_cloudwatch

JOURS_TEST = 3


@pytest.fixture(scope="module")
def records() -> list:
    """Trafic de trois jours, graine par défaut (généré une seule fois)."""
    return generer_trafic(jours=JOURS_TEST)


def _par_application(records: list, application_id: str) -> list:
    """Enregistrements d'une application."""
    return [r for r in records if r.application_id == application_id]


def _mediane(valeurs: list[float]) -> float:
    """Médiane simple d'une liste non vide."""
    triees = sorted(valeurs)
    return triees[len(triees) // 2]


def test_meme_graine_meme_trafic() -> None:
    """Deux générations avec la même graine donnent des lignes identiques."""
    premier = lignes_cloudwatch(generer_trafic(graine=1234, jours=JOURS_TEST))
    second = lignes_cloudwatch(generer_trafic(graine=1234, jours=JOURS_TEST))
    assert premier == second


def test_graine_differente_trafic_different() -> None:
    """Une autre graine produit un trafic différent, de volume comparable."""
    premier = lignes_cloudwatch(generer_trafic(graine=1234, jours=JOURS_TEST))
    second = lignes_cloudwatch(generer_trafic(graine=5678, jours=JOURS_TEST))
    assert premier != second
    assert 0.8 * len(premier) <= len(second) <= 1.2 * len(premier)


def test_application_isolee_reproductible(records: list) -> None:
    """Générer une seule application donne les mêmes enregistrements."""
    seul = generer_trafic(
        jours=JOURS_TEST, profils={"ticket-classifier": PROFILS["ticket-classifier"]}
    )
    reference = _par_application(records, "ticket-classifier")
    assert [r.request_id for r in seul] == [r.request_id for r in reference]


def test_format_journal_invocation(records: list) -> None:
    """Chaque ligne porte les champs du journal d'invocation Bedrock."""
    ligne = lignes_cloudwatch(records)[0]
    attendus = {
        "requestId",
        "eventTime",
        "input",
        "output",
        "inputTokenCount",
        "outputTokenCount",
        "totalTokenCount",
        "amazon-bedrock-invocationMetrics",
        "requestMetadata",
        "errorCode",
        "errorMessage",
    }
    assert attendus <= set(ligne)
    assert ligne["totalTokenCount"] == ligne["inputTokenCount"] + ligne["outputTokenCount"]
    assert ligne["eventTime"].endswith("Z")
    entree = json.loads(ligne["input"])
    assert entree["messages"][0]["role"] == "user"
    assert "inferenceConfig" in entree
    sortie = json.loads(ligne["output"])
    assert sortie["output"]["message"]["role"] == "assistant"
    metriques = json.loads(ligne["amazon-bedrock-invocationMetrics"])
    assert metriques["serviceTier"] in {"Standard", "Flex", "Priority", "Reserved"}
    etiquettes = json.loads(ligne["requestMetadata"])
    assert set(etiquettes) == {"application", "team", "environment", "typeRequete"}


def test_jetons_coherents_avec_les_corps(records: list) -> None:
    """Les jetons annoncés correspondent au contenu des corps JSON."""
    for record in records[:200]:
        if record.code_erreur is not None:
            continue
        sortie = json.loads(record.corps_sortie)
        assert sortie["usage"]["outputTokens"] == record.jetons_sortie
        assert sortie["usage"]["inputTokens"] == record.jetons_entree
        assert record.jetons_sortie == len(
            sortie["output"]["message"]["content"][0]["text"]
        ) // 4 + (len(sortie["output"]["message"]["content"][0]["text"]) % 4 > 0)


def test_classement_bien_plus_court_que_assistant(records: list) -> None:
    """Le classement produit des sorties beaucoup plus courtes que l'assistant."""
    classement = _par_application(records, "ticket-classifier")
    assistant = _par_application(records, "support-assistant")
    mediane_classement = _mediane([r.jetons_sortie for r in classement if r.code_erreur is None])
    mediane_assistant = _mediane([r.jetons_sortie for r in assistant if r.code_erreur is None])
    assert mediane_classement * 5 < mediane_assistant
    assert _mediane([r.jetons_entree for r in classement]) < 300
    assert _mediane([r.jetons_entree for r in assistant]) > 1000


def test_resume_nocturne(records: list) -> None:
    """Le résumé de documents ne tourne que la nuit, en niveau Standard."""
    resume = _par_application(records, "doc-summarizer")
    heures = {r.event_time.hour for r in resume}
    assert heures <= {0, 1, 2, 3, 4, 5}
    assert all(r.niveau.value == "standard" for r in resume)
    assert _mediane([r.jetons_sortie for r in resume if r.code_erreur is None]) > 1000


def test_prefixe_long_sans_jeton_de_cache(records: list) -> None:
    """L'assistant répète un préfixe long sans jamais lire de cache."""
    assistant = _par_application(records, "support-assistant")
    prefixes = {json.loads(r.corps_entree)["system"][0]["text"] for r in assistant}
    assert len(prefixes) == 1
    prefixe = prefixes.pop()
    assert len(prefixe) // 4 > 500
    assert all(r.jetons_cache_lecture == 0 for r in assistant)
    assert all(r.jetons_cache_ecriture == 0 for r in assistant)


def test_requetes_identiques_repetees(records: list) -> None:
    """Une part des requêtes du classement est identique à une requête déjà vue."""
    classement = _par_application(records, "ticket-classifier")
    signatures = [r.signature_requete() for r in classement]
    assert len(set(signatures)) < len(signatures)


def test_erreurs_suivies_de_relance(records: list) -> None:
    """Chaque erreur est suivie d'une relance identique dans la minute."""
    erreurs = [r for r in records if r.code_erreur is not None]
    assert erreurs, "le trafic doit contenir des erreurs"
    succes_par_corps: dict[str, list] = {}
    for record in records:
        if record.code_erreur is None:
            succes_par_corps.setdefault(record.corps_entree, []).append(record.event_time)
    for erreur in erreurs[:50]:
        relances = [
            moment
            for moment in succes_par_corps.get(erreur.corps_entree, [])
            if timedelta(0) < moment - erreur.event_time <= timedelta(minutes=1)
        ]
        assert relances, "aucune relance trouvée pour une erreur"


def test_volumes_realistes(records: list) -> None:
    """Les volumes quotidiens respectent l'ordre de grandeur des profils."""
    for application_id, profil in PROFILS.items():
        lignes = _par_application(records, application_id)
        moyenne_jour = len(lignes) / JOURS_TEST
        assert 0.5 * profil.requetes_par_jour <= moyenne_jour <= 1.5 * profil.requetes_par_jour
