"""Tests du bac à sable d'exécution du code rejoué (EF-EVA-02, ENF-IA-03, §13.3).

Deux familles de tests :

- **sans exécution** : les refus explicites (bac désactivé, aucun test, source
  trop volumineuse, isolation noyau exigée mais absente), les bornes invalides,
  et l'environnement reconstruit sans les secrets de l'hôte ;
- **avec exécution réelle** : le sous-processus est réellement lancé, sur des
  programmes minuscules et sans réseau — code juste, code faux, délai dépassé,
  tentative d'accès réseau, sortie bornée.

Aucun réseau sortant, aucun AWS : la tentative d'accès réseau vise l'adresse de
boucle locale sur un port fermé, et elle doit échouer dans tous les cas.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jauge.common.errors import ValidationError
from jauge.evaluation import bac_a_sable
from jauge.evaluation.bac_a_sable import (
    ISOLEMENT_GARDE,
    ISOLEMENT_NAMESPACE,
    ParametresBacASable,
    executer_tests,
    niveau_isolation,
)

CODE_JUSTE = "def triple(x):\n    return x * 3\n"
TEST_JUSTE = "from solution import triple\n\n\ndef test_triple():\n    assert triple(2) == 6\n"
TEST_FAUX = "from solution import triple\n\n\ndef test_triple():\n    assert triple(2) == 7\n"


def test_niveau_isolation_est_annonce() -> None:
    """Le niveau obtenu sur l'hôte est l'un des deux niveaux documentés."""
    assert niveau_isolation() in (ISOLEMENT_NAMESPACE, ISOLEMENT_GARDE)


@pytest.mark.parametrize(
    ("champ", "valeur"),
    [("timeout_secondes", 0.01), ("memoire_mo", 8), ("cpu_secondes", 0), ("max_processus", 0)],
)
def test_bornes_sous_le_minimum_refusees(champ: str, valeur: float) -> None:
    """Des bornes qui ne borneraient rien sont refusées à la construction."""
    with pytest.raises(ValidationError):
        ParametresBacASable(**{champ: valeur})


def test_bac_desactive_ne_prouve_rien() -> None:
    """Désactivé, le bac ne bloque rien et ne prétend rien prouver."""
    resultat = executer_tests(CODE_JUSTE, TEST_JUSTE, ParametresBacASable(actif=False))

    assert resultat.execute is False
    assert resultat.ok is False
    assert "désactivé" in resultat.motif


def test_sans_tests_fournis_aucun_verdict() -> None:
    """Sans tests, la porte code ne peut pas trancher : rien n'est exécuté."""
    resultat = executer_tests(CODE_JUSTE, "   ")

    assert resultat.execute is False
    assert "aucun test" in resultat.motif


def test_source_trop_volumineuse_refusee() -> None:
    """Un code au-delà de la borne n'est pas exécuté (refus explicite)."""
    code = "X = 0\n" + "# remplissage\n" * 500
    resultat = executer_tests(
        code, TEST_JUSTE, ParametresBacASable(taille_max_source_octets=len(code) - 1)
    )

    assert resultat.execute is False
    assert "volumineux" in resultat.motif


def test_isolation_noyau_exigee_mais_absente_refusee(monkeypatch: pytest.MonkeyPatch) -> None:
    """`exiger_namespace` refuse l'exécution plutôt que de dégrader l'isolation."""
    monkeypatch.setattr(bac_a_sable, "niveau_isolation", lambda interpreteur=None: ISOLEMENT_GARDE)

    resultat = executer_tests(CODE_JUSTE, TEST_JUSTE, ParametresBacASable(exiger_namespace=True))

    assert resultat.execute is False
    assert resultat.isolation == ISOLEMENT_GARDE
    assert "indisponible" in resultat.motif


def test_environnement_sans_secrets_ni_proxy_de_l_hote(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'environnement est reconstruit à neuf (ENF-CONF-01)."""
    for variable in ("AWS_SECRET_ACCESS_KEY", "HTTPS_PROXY", "OPENAI_API_KEY"):
        monkeypatch.setenv(variable, "secret-de-hote")

    # Fonction interne : c'est elle qui construit l'environnement du sous-processus.
    environnement = bac_a_sable._environnement(Path("/tmp/x"), ParametresBacASable())

    assert "secret-de-hote" not in environnement.values()
    assert "AWS_SECRET_ACCESS_KEY" not in environnement
    assert "HTTPS_PROXY" not in environnement


def test_code_juste_passe() -> None:
    """Exécution réelle : les tests fournis passent, le verdict est rendu."""
    resultat = executer_tests(CODE_JUSTE, TEST_JUSTE)

    assert resultat.execute is True
    assert resultat.ok is True
    assert resultat.tests_total == 1
    assert resultat.tests_echoues == ()
    assert resultat.isolation in (ISOLEMENT_NAMESPACE, ISOLEMENT_GARDE)
    assert resultat.duree_ms > 0


def test_code_faux_echoue_en_nommant_les_tests() -> None:
    """Exécution réelle : l'échec nomme le test, sans recopier le code candidat."""
    resultat = executer_tests(CODE_JUSTE, TEST_FAUX)

    assert resultat.execute is True
    assert resultat.ok is False
    assert resultat.tests_echoues == ("test_triple",)
    assert "triple" not in resultat.motif.replace("test_triple", "")


def test_delai_depasse_interrompt_l_execution() -> None:
    """Exécution réelle : une boucle infinie est tuée au délai imposé."""
    code = "import time\n\n\ndef attente():\n    time.sleep(30)\n"
    tests = "from solution import attente\n\n\ndef test_lent():\n    attente()\n"

    resultat = executer_tests(code, tests, ParametresBacASable(timeout_secondes=1.0))

    assert resultat.execute is True
    assert resultat.ok is False
    assert resultat.delai_depasse is True
    assert "délai" in resultat.motif


def test_acces_reseau_refuse() -> None:
    """Exécution réelle : une tentative réseau échoue, quel que soit le niveau.

    La cible est l'adresse de boucle sur un port fermé : aucun trafic sortant,
    et l'échec est attendu aussi bien en isolation noyau qu'en garde Python.
    """
    code = (
        "import socket\n"
        "\n"
        "\n"
        "def contacter():\n"
        "    with socket.create_connection(('127.0.0.1', 1), 0.5):\n"
        "        return True\n"
    )
    tests = "from solution import contacter\n\n\ndef test_reseau():\n    assert contacter()\n"

    resultat = executer_tests(code, tests, ParametresBacASable(timeout_secondes=5.0))

    assert resultat.execute is True
    assert resultat.ok is False


def test_sortie_bornee() -> None:
    """La sortie rendue est tronquée à la borne configurée (pas de fuite massive)."""
    code = "def bavard():\n    return None\n"
    tests = "def test_bavard():\n    print('x' * 5000)\n    assert True\n"

    resultat = executer_tests(
        code, tests, ParametresBacASable(taille_max_sortie_octets=200, timeout_secondes=5.0)
    )

    assert resultat.execute is True
    assert len(resultat.sortie) <= 200
