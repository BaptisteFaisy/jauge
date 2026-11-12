"""Scripts embarqués dans le bac à sable d'exécution (EF-EVA-02, ENF-IA-03).

Deux textes Python écrits dans le répertoire de travail jetable avant chaque
exécution (voir `jauge.evaluation.bac_a_sable`) :

- `SCRIPT_GARDE` : plugin pytest installé **avant** tout code candidat ; il
  refuse réseau, exécution externe et modules de contournement au niveau du
  langage. Efficace seulement en isolation ``garde-python`` — contournable par
  un code déterminé, comme documenté en tête de `bac_a_sable` ;
- `SCRIPT_HARNAIS` : applique les limites POSIX (mémoire, CPU, taille de
  fichier, processus) **avant** d'importer quoi que ce soit du candidat, puis
  lance pytest sur les tests fournis et rend le verdict JUnit.

Ces scripts sont des constantes : ils ne sont jamais rendus modifiables par le
code candidat, qui est traité comme une donnée hostile (ENF-IA-03).
"""

from __future__ import annotations

#: Plugin pytest installé avant tout code candidat : refuse réseau, exécution
#: externe et modules de contournement. Efficace au niveau du langage
#: seulement — voir les limites en tête de `bac_a_sable`.
SCRIPT_GARDE = '''"""Garde du bac à sable Jauge : réseau et exécution externe refusés."""

import os
import socket
import subprocess
import sys

RAISON_RESEAU = "accès réseau refusé par le bac à sable Jauge"
RAISON_SHELL = "exécution externe refusée par le bac à sable Jauge"
MODULES_INTERDITS = frozenset({"ctypes", "multiprocessing"})


def _refuser(raison):
    def _bloque(*args, **kwargs):
        raise PermissionError(raison)

    return _bloque


class _BlocageImports:
    """Interdit les modules permettant de contourner la garde applicative."""

    def find_spec(self, nom_complet, chemin=None, cible=None):
        if nom_complet.split(".")[0] in MODULES_INTERDITS:
            raise ImportError(f"module « {nom_complet} » indisponible dans le bac à sable Jauge")
        return None


def _installer():
    refus_reseau = _refuser(RAISON_RESEAU)
    for nom in ("connect", "connect_ex", "sendto"):
        setattr(socket.socket, nom, refus_reseau)
    for nom in ("create_connection", "getaddrinfo", "gethostbyname"):
        setattr(socket, nom, refus_reseau)
    refus_shell = _refuser(RAISON_SHELL)
    for nom in ("Popen", "run", "call", "check_output", "check_call"):
        setattr(subprocess, nom, refus_shell)
    for nom in ("system", "popen", "fork", "execv", "execve", "execvp", "execvpe"):
        if hasattr(os, nom):
            setattr(os, nom, refus_shell)
    sys.meta_path.insert(0, _BlocageImports())


_installer()
'''

#: Harnais : applique les limites POSIX **avant** d'importer quoi que ce soit
#: du candidat, puis lance pytest sur les tests fournis (verdict JUnit).
SCRIPT_HARNAIS = '''"""Harnais du bac à sable : limites POSIX puis exécution des tests fournis."""

import os
import resource
import sys


def _limiter():
    """Borne mémoire, CPU, taille de fichier et nombre de processus."""
    bornes = (
        ("RLIMIT_AS", int(os.environ.get("JAUGE_MEMOIRE_MO", "512")) * 1024 * 1024),
        ("RLIMIT_CPU", int(os.environ.get("JAUGE_CPU_SECONDES", "10"))),
        ("RLIMIT_FSIZE", int(os.environ.get("JAUGE_FICHIER_OCTETS", "1048576"))),
        ("RLIMIT_NPROC", int(os.environ.get("JAUGE_PROCESSUS", "128"))),
    )
    for nom, valeur in bornes:
        try:
            resource.setrlimit(getattr(resource, nom), (valeur, valeur))
        except (OSError, ValueError):
            pass  # limite indisponible ici : le timeout dur du parent reste actif


_limiter()

import pytest  # noqa: E402  (import volontairement postérieur aux limites)

sys.exit(
    pytest.main(
        [
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "jauge_garde",
            "--junitxml=" + os.environ.get("JAUGE_RESULTATS", "resultats.xml"),
            os.environ.get("JAUGE_FICHIER_TESTS", "test_candidat.py"),
        ]
    )
)
'''
