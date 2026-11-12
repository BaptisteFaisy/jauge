"""Bac à sable d'exécution du code rejoué (EF-EVA-02, ENF-IA-03, §13.3).

Le code exécuté ici **vient du rejeu** : c'est une donnée, jamais une
instruction (ENF-IA-03). Il est traité comme hostile : sous-processus jetable,
borné en temps, mémoire, processus et sortie, dans un répertoire temporaire,
**sans accès réseau**, avec un environnement reconstruit à neuf (aucun secret
ni proxy de l'hôte, ENF-CONF-01).

Deux niveaux d'isolation, choisis au lancement et **annoncés dans le résultat**
(``ResultatBacASable.isolation``) — ce bac à sable ne prétend jamais être plus
sûr qu'il ne l'est :

``namespace``
    ``bwrap`` (bubblewrap) ou, à défaut, ``unshare --net`` : le noyau coupe le
    réseau. Avec ``bwrap``, la racine est de plus en lecture seule hors du
    répertoire de travail et les processus hôtes sont invisibles.
``garde-python``
    Aucun outil d'isolation disponible : réseau et exécution externe sont
    refusés **au niveau du langage** (``socket``, ``subprocess``, ``os.system``,
    ``ctypes``…) par le plugin ``jauge_garde``. Les limites POSIX et le timeout
    dur s'appliquent toujours, mais un code déterminé peut s'échapper : ce
    niveau n'est **pas** une frontière de sécurité.

Limites assumées, à lire avant d'activer la porte « code » :

- en mode ``garde-python``, l'interdiction du réseau est contournable (appel
  système direct, binaire déjà chargé) et rien n'empêche d'écrire hors du
  répertoire de travail avec les droits du compte qui exécute Jauge ;
- le verdict est produit par le sous-processus lui-même (JUnit écrit en dernier
  par le harnais, code de retour vérifié par le parent) : un code hostile
  pourrait théoriquement le falsifier ;
- ``exiger_namespace=True`` **refuse** l'exécution plutôt que de dégrader
  l'isolation : repli recommandé en production, avec ``nsjail`` ou un
  conteneur dédié quand ``bwrap`` est absent ;
- ``actif=False`` désactive l'exécution : la porte « code » devient alors
  **non applicable** — elle ne bloque rien et ne prétend rien prouver.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jauge.common.errors import ValidationError
from jauge.evaluation.harnais_sable import SCRIPT_GARDE, SCRIPT_HARNAIS

#: Isolation par le noyau (bwrap ou unshare) : réseau réellement coupé.
ISOLEMENT_NAMESPACE = "namespace"
#: Isolation applicative seulement : garde Python, contournable (documentée).
ISOLEMENT_GARDE = "garde-python"

TIMEOUT_DEFAUT_S = 10.0
MEMOIRE_DEFAUT_MO = 512
CPU_DEFAUT_S = 10
TAILLE_FICHIER_DEFAUT = 1_000_000
MAX_PROCESSUS_DEFAUT = 128
SOURCE_MAX_OCTETS = 200_000
SORTIE_MAX_OCTETS = 20_000

#: Noms de fichiers imposés dans le répertoire de travail jetable.
MODULE_CANDIDAT = "solution.py"
FICHIER_TESTS = "test_candidat.py"
FICHIER_GARDE = "jauge_garde.py"
FICHIER_HARNAIS = "_harnais.py"
FICHIER_RESULTATS = "resultats.xml"
FICHIER_SORTIE = "sortie.txt"
FICHIER_ERREUR = "erreur.txt"
FICHIER_CONFIG_PYTEST = "pytest.ini"
FICHIER_CONFIG_NSJAIL = "nsjail.cfg"


@dataclass(frozen=True)
class ParametresBacASable:
    """Bornes du bac à sable, fournies par la configuration de la tâche."""

    actif: bool = True
    timeout_secondes: float = TIMEOUT_DEFAUT_S
    memoire_mo: int = MEMOIRE_DEFAUT_MO
    cpu_secondes: int = CPU_DEFAUT_S
    taille_fichier_octets: int = TAILLE_FICHIER_DEFAUT
    max_processus: int = MAX_PROCESSUS_DEFAUT
    taille_max_source_octets: int = SOURCE_MAX_OCTETS
    taille_max_sortie_octets: int = SORTIE_MAX_OCTETS
    exiger_namespace: bool = False
    interpreteur: str | None = None

    def __post_init__(self) -> None:
        """Refuse des bornes qui ne borneraient rien (ENF-IA-01 : pas de valeur inventée)."""
        for nom, valeur, minimum in (
            ("timeout_secondes", self.timeout_secondes, 0.1),
            ("memoire_mo", self.memoire_mo, 64),
            ("cpu_secondes", self.cpu_secondes, 1),
            ("taille_fichier_octets", self.taille_fichier_octets, 4_096),
            ("max_processus", self.max_processus, 1),
            ("taille_max_source_octets", self.taille_max_source_octets, 1),
            ("taille_max_sortie_octets", self.taille_max_sortie_octets, 1),
        ):
            if valeur < minimum:
                raise ValidationError(
                    f"bac à sable : {nom} sous le minimum", parametre=nom, valeur=valeur
                )


@dataclass(frozen=True)
class ResultatBacASable:
    """Verdict d'une exécution. `sortie` reste dans le plan de données."""

    execute: bool
    ok: bool
    motif: str
    code_retour: int = 0
    duree_ms: int = 0
    isolation: str = ""
    delai_depasse: bool = False
    tests_total: int = 0
    tests_echoues: tuple[str, ...] = ()
    sortie: str = ""


def _non_execute(motif: str, isolation: str = "") -> ResultatBacASable:
    """Résultat d'un bac à sable qui n'a pas pu trancher (aucune preuve produite)."""
    return ResultatBacASable(execute=False, ok=False, motif=motif, isolation=isolation)


def _sonde(commande: list[str]) -> bool:
    """Vrai si `commande` s'exécute avec succès (sonde d'isolation, sans effet)."""
    try:
        retour = subprocess.run(  # noqa: S603 - commande construite par Jauge
            commande, capture_output=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return retour.returncode == 0


@lru_cache(maxsize=1)
def _isolation_disponible(interpreteur: str | None = None) -> tuple[str, str]:
    """Niveau d'isolation obtenu sur cette machine, et l'outil qui le tient.

    `bwrap` d'abord (racine en lecture seule + réseau coupé), puis
    `unshare --net` (réseau coupé seulement), sinon garde Python seule.

    La sonde utilise **la commande réelle** (mêmes options que l'exécution) :
    une sonde plus permissive que la commande réelle annoncerait un niveau
    d'isolation que la machine ne sait pas tenir — c'est exactement ce qui
    s'est produit sur un runner dont `unshare` ne connaissait pas `--chdir`.
    """
    python = interpreteur or sys.executable
    for outil in ("nsjail", "bwrap", "unshare"):
        if shutil.which(outil) and _sonde_outil(outil, python):
            return ISOLEMENT_NAMESPACE, outil
    return ISOLEMENT_GARDE, ""


def niveau_isolation(interpreteur: str | None = None) -> str:
    """Niveau d'isolation réellement obtenu sur cette machine."""
    return _isolation_disponible(interpreteur)[0]


def _commande_isolee(
    interpreteur: str, dossier: Path, isolation: str, reglages: ParametresBacASable
) -> list[str]:
    """Commande complète : harnais Python, éventuellement sous namespace."""
    harnais = [interpreteur, "-B", "-E", "-s", FICHIER_HARNAIS]
    if isolation != ISOLEMENT_NAMESPACE:
        return harnais
    outil = _isolation_disponible()[1] or "unshare"
    if outil == "nsjail":
        return _commande_nsjail(harnais, dossier, reglages)
    return _commande_namespace(harnais, dossier, outil)


def _commande_namespace(charge: list[str], dossier: Path, outil: str) -> list[str]:
    """Commande d'isolation par le noyau, pour l'outil donné.

    `unshare` ne reçoit **pas** `--chdir` : le répertoire de travail est déjà
    celui du dossier jetable (le sous-processus y est lancé), et les versions
    anciennes de `util-linux` ne connaissent pas cette option — elle faisait
    échouer l'exécution sur un runner alors que la sonde, plus permissive,
    annonçait quand même une isolation noyau.
    """
    if outil == "bwrap":
        return [
            "bwrap",
            "--unshare-all",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(dossier),
            str(dossier),
            "--chdir",
            str(dossier),
            "--die-with-parent",
            "--new-session",
            *charge,
        ]
    return [
        "unshare",
        "--net",
        "--pid",
        "--fork",
        "--mount",
        "--mount-proc",
        "--map-root-user",
        *charge,
    ]


def _config_nsjail(dossier: Path, reglages: ParametresBacASable) -> str:
    """Configuration nsjail du bac à sable : namespaces, seccomp et quotas.

    `nsjail` ajoute à l'isolation par namespaces un **filtre seccomp** (liste
    blanche d'appels système) et des quotas de groupe : c'est la frontière la
    plus stricte proposée ici.

    La configuration est écrite dans le dossier jetable, donc un code candidat
    ne peut pas la modifier (elle est relue par `nsjail` avant l'exécution).
    Elle est **sondée avec la commande réelle** : si `nsjail` n'est pas
    installé, ou si sa version ne lit pas ce format, l'outil est écarté et le
    bac à sable retombe sur `bwrap` ou `unshare` — jamais sur un silence.
    """
    return "\n".join(
        [
            'name: "jauge-sandbox"',
            "mode: ONCE",
            'hostname: "jauge-sandbox"',
            "die_with_parent: true",
            "clone_newnet: true",
            "clone_newpid: true",
            "clone_newns: true",
            "clone_newuser: true",
            "mount_proc: true",
            f"time_limit: {{ soft: {int(reglages.timeout_secondes)},"
            f" hard: {int(reglages.timeout_secondes) + 1} }}",
            f"rlimit_as: {{ soft: {reglages.memoire_mo * 1024 * 1024},"
            f" hard: {reglages.memoire_mo * 1024 * 1024} }}",
            f"rlimit_cpu: {{ soft: {reglages.cpu_secondes}, hard: {reglages.cpu_secondes + 1} }}",
            f"rlimit_fsize: {{ soft: {reglages.taille_fichier_octets},"
            f" hard: {reglages.taille_fichier_octets} }}",
            f"rlimit_nproc: {{ soft: {reglages.max_processus}, hard: {reglages.max_processus} }}",
            'bindmount: ["/:/ro", f"{dossier}:{dossier}"]',
            "",
        ]
    )


def _commande_nsjail(charge: list[str], dossier: Path, reglages: ParametresBacASable) -> list[str]:
    """Commande nsjail : configuration jetable puis charge utile."""
    configuration = dossier / FICHIER_CONFIG_NSJAIL
    configuration.write_text(_config_nsjail(dossier, reglages), encoding="utf-8")
    return ["nsjail", "--config", str(configuration), "--", *charge]


def _sonde_outil(outil: str, python: str) -> bool:
    """Sonde l'outil d'isolation avec la commande réelle, dossier jetable compris."""
    with tempfile.TemporaryDirectory(prefix="jauge-sonde-") as dossier:
        chemin = Path(dossier)
        reglages = ParametresBacASable()
        if outil == "nsjail":
            return _sonde(_commande_nsjail([python, "-c", "pass"], chemin, reglages))
        return _sonde(_commande_namespace([python, "-c", "pass"], chemin, outil))


def _environnement(dossier: Path, parametres: ParametresBacASable) -> dict[str, str]:
    """Environnement reconstruit à neuf : aucun secret ni proxy de l'hôte.

    `PYTHONDONTWRITEBYTECODE` n'est pas nécessaire (`-B`), mais `no_proxy=*`
    et l'absence de `http_proxy`/`https_proxy` coupent tout repli par proxy.
    """
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(dossier),
        "TMPDIR": str(dossier),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "JAUGE_BAC_A_SABLE": "1",
        "JAUGE_MEMOIRE_MO": str(parametres.memoire_mo),
        "JAUGE_CPU_SECONDES": str(parametres.cpu_secondes),
        "JAUGE_FICHIER_OCTETS": str(parametres.taille_fichier_octets),
        "JAUGE_PROCESSUS": str(parametres.max_processus),
        "JAUGE_RESULTATS": FICHIER_RESULTATS,
        "JAUGE_FICHIER_TESTS": FICHIER_TESTS,
        "no_proxy": "*",
        "NO_PROXY": "*",
    }


def _preparer_dossier(dossier: Path, code: str, tests: str) -> None:
    """Écrit le code candidat (donnée hostile) et les tests dans le dossier jetable."""
    dossier.chmod(0o700)
    (dossier / MODULE_CANDIDAT).write_text(code, encoding="utf-8")
    (dossier / FICHIER_TESTS).write_text(tests, encoding="utf-8")
    (dossier / FICHIER_GARDE).write_text(SCRIPT_GARDE, encoding="utf-8")
    (dossier / FICHIER_HARNAIS).write_text(SCRIPT_HARNAIS, encoding="utf-8")
    # Configuration pytest vide : aucun `addopts` hérité d'un dépôt voisin.
    (dossier / FICHIER_CONFIG_PYTEST).write_text("[pytest]\n", encoding="utf-8")


def _tuer_groupe(processus: subprocess.Popen[bytes]) -> None:
    """Tue tout le groupe : le code hostile ne survit pas au timeout."""
    try:
        os.killpg(os.getpgid(processus.pid), signal.SIGKILL)
    except OSError:
        processus.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):  # défense ultime
        processus.wait(timeout=5)


def _lancer(
    commande: list[str], dossier: Path, environnement: dict[str, str], delai: float
) -> tuple[int, bool]:
    """Exécute le sous-processus ; rend (code de retour, délai dépassé).

    Sortie et erreur partent dans des fichiers du dossier jetable : leur taille
    est bornée par `RLIMIT_FSIZE`, donc une impression démesurée tue l'enfant au
    lieu d'épuiser la mémoire du parent.
    """
    with (
        (dossier / FICHIER_SORTIE).open("wb") as sortie,
        (dossier / FICHIER_ERREUR).open("wb") as erreur,
    ):
        processus = subprocess.Popen(  # noqa: S603 - commande construite par Jauge
            commande,
            cwd=dossier,
            env=environnement,
            stdin=subprocess.DEVNULL,
            stdout=sortie,
            stderr=erreur,
            start_new_session=True,
            close_fds=True,
        )
        try:
            return processus.wait(timeout=delai), False
        except subprocess.TimeoutExpired:
            _tuer_groupe(processus)
            return processus.returncode if processus.returncode is not None else -9, True


def _lire(chemin: Path, max_octets: int) -> str:
    """Lit au plus `max_octets` d'un fichier de sortie (le reste est tronqué)."""
    try:
        with chemin.open("rb") as fichier:
            return fichier.read(max_octets).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _lire_resultats(dossier: Path) -> tuple[int, tuple[str, ...]]:
    """Lit le verdict JUnit : (nombre de tests, noms des tests en échec)."""
    chemin = dossier / FICHIER_RESULTATS
    if not chemin.exists():
        return 0, ()
    try:
        racine = ElementTree.parse(chemin).getroot()  # noqa: S314 - fichier produit par pytest
    except ElementTree.ParseError:
        return 0, ()
    suite = racine if racine.tag == "testsuite" else racine.find("testsuite")
    if suite is None:
        return 0, ()
    echoues = tuple(
        cas.get("name", "?")
        for cas in suite.iter("testcase")
        if cas.find("failure") is not None or cas.find("error") is not None
    )
    return int(suite.get("tests", "0") or 0), echoues


def _raison_arret(code_retour: int) -> str:
    """Traduit un arrêt par signal en motif lisible (aucun texte candidat)."""
    raisons = {
        -signal.SIGKILL: "processus tué (délai ou mémoire)",
        -signal.SIGXCPU: "limite de temps CPU atteinte",
        -signal.SIGXFSZ: "sortie trop volumineuse (limite atteinte)",
        -signal.SIGSEGV: "segmentation fault",
    }
    return raisons.get(code_retour, f"arrêt anormal (code {code_retour})")


def _verdict(
    dossier: Path,
    code_retour: int,
    delai_depasse: bool,
    duree_ms: int,
    isolation: str,
    reglages: ParametresBacASable,
) -> ResultatBacASable:
    """Construit le résultat : le verdict vient des tests, jamais d'une intuition."""
    sortie = _lire(dossier / FICHIER_SORTIE, reglages.taille_max_sortie_octets)
    sortie += _lire(dossier / FICHIER_ERREUR, reglages.taille_max_sortie_octets)
    total, echoues = _lire_resultats(dossier)
    commun = {
        "code_retour": code_retour,
        "duree_ms": duree_ms,
        "isolation": isolation,
        "delai_depasse": delai_depasse,
        "tests_total": total,
        "tests_echoues": echoues,
        "sortie": sortie,
    }
    if delai_depasse:
        motif = f"exécution interrompue : délai de {reglages.timeout_secondes:g} s dépassé"
        return ResultatBacASable(execute=True, ok=False, motif=motif, **commun)
    if total == 0:
        motif = f"aucun test exécuté : {_raison_arret(code_retour)}"
        return ResultatBacASable(execute=True, ok=False, motif=motif, **commun)
    if not echoues:
        return ResultatBacASable(
            execute=True,
            ok=True,
            motif=f"{total} test(s) fourni(s) passent (isolation {isolation})",
            **commun,
        )
    return ResultatBacASable(
        execute=True,
        ok=False,
        motif=f"{len(echoues)}/{total} test(s) en échec : {', '.join(echoues[:5])}",
        **commun,
    )


def executer_tests(
    code: str, tests: str, parametres: ParametresBacASable | None = None
) -> ResultatBacASable:
    """Exécute le code candidat avec les tests fournis, dans un bac à sable jetable.

    `code` est le code produit par le modèle (donnée hostile, ENF-IA-03) ;
    `tests` vient de la configuration de la tâche. Le rendu dit toujours quel
    niveau d'isolation a été obtenu, et `execute=False` quand rien n'a pu être
    prouvé (bac à sable désactivé, isolation refusée, source trop volumineuse).
    """
    reglages = parametres or ParametresBacASable()
    if not reglages.actif:
        return _non_execute("bac à sable désactivé par configuration")
    if not tests.strip():
        return _non_execute("aucun test fourni : la porte code ne peut pas trancher")
    if len(code.encode("utf-8")) > reglages.taille_max_source_octets:
        return _non_execute("code candidat trop volumineux pour le bac à sable")
    isolation = niveau_isolation(reglages.interpreteur)
    if reglages.exiger_namespace and isolation != ISOLEMENT_NAMESPACE:
        return _non_execute(
            "isolation réseau par le noyau indisponible sur cet hôte : exécution refusée",
            isolation,
        )
    interpreteur = reglages.interpreteur or sys.executable
    dossier = Path(tempfile.mkdtemp(prefix="jauge-bac-"))
    try:
        _preparer_dossier(dossier, code, tests)
        debut = time.monotonic()
        code_retour, delai_depasse = _lancer(
            _commande_isolee(interpreteur, dossier, isolation, reglages),
            dossier,
            _environnement(dossier, reglages),
            reglages.timeout_secondes,
        )
        duree_ms = int((time.monotonic() - debut) * 1000)
        return _verdict(dossier, code_retour, delai_depasse, duree_ms, isolation, reglages)
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
