#!/usr/bin/env python3
"""Jauge — primitives git de résolution de conflits ([HERMES-REBASE]).

Extraites de `resolver.py` pour rester sous 500 lignes par fichier : ce module
ne fait que du **git local** (aucun réseau, aucun secret), donc il est
entièrement couvrable par un vrai rebase dans un dépôt jetable.

Rappel de sémantique pendant un rebase : l'index porte trois étapes —
1 = base (ancêtre), 2 = `ours` (la branche de base), 3 = `theirs` (la PR).
Une résolution « côté base » est donc `git checkout --ours`.

Usage : `python3 .github/hermes/rebase.py --selftest`
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

import resolve

ROUNDS_MAX = 12
PASSES_MAX = 4


@dataclass
class Bilan:
    """Résultat d'une passe de rebase + résolution."""

    ia: list[str] = field(default_factory=list)
    heuristique: list[str] = field(default_factory=list)
    echecs: list[str] = field(default_factory=list)
    regenerer: list[str] = field(default_factory=list)
    ok: bool = False

    def resume(self) -> str:
        """Synthèse lisible pour le rapport de PR."""
        if self.ok and not (self.ia or self.heuristique):
            return "Aucun conflit de contenu : rebase simple."
        parties = []
        if self.ia:
            parties.append(
                "**résolus par IA (Fireworks)** : " + ", ".join("`%s`" % f for f in self.ia)
            )
        if self.heuristique:
            parties.append(
                "**résolus sans IA (déterministe)** : "
                + ", ".join("`%s`" % f for f in self.heuristique)
            )
        if self.echecs:
            parties.append("**non résolus** : " + ", ".join("`%s`" % f for f in self.echecs))
        return "\n".join(parties) if parties else "Rebase sans conflit."


# ---------------------------------------------------------------------- git
def git(work: str, *args, timeout: int = 300):
    """`git` dans l'arbre de travail. Rend le `CompletedProcess`, ou None sur timeout."""
    commande = ["git", "-C", work] + [str(a) for a in args]
    env = {**os.environ, "GIT_EDITOR": "true", "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(
            commande, capture_output=True, text=True, errors="replace", timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired:
        print("[git] timeout %ss : %s" % (timeout, " ".join(commande[:6])), flush=True)
        return None


def _git_ok(work: str, *args, timeout: int = 300) -> bool:
    """Vrai si la commande git rend 0 (jamais de déréférencement de None)."""
    resultat = git(work, *args, timeout=timeout)
    return resultat is not None and resultat.returncode == 0


def contenu_stage(work: str, stage: int, chemin: str) -> str:
    """Contenu d'un fichier dans l'index pendant un conflit (1=base, 2=ours, 3=theirs)."""
    resultat = git(work, "show", ":%d:%s" % (stage, chemin), timeout=60)
    return resultat.stdout if resultat is not None and resultat.returncode == 0 else ""


def fichiers_en_conflit(work: str) -> list[str]:
    """Chemins non fusionnés de l'index courant."""
    resultat = git(work, "diff", "--name-only", "--diff-filter=U", timeout=60)
    if resultat is None or resultat.returncode != 0:
        return []
    return [ligne.strip() for ligne in resultat.stdout.splitlines() if ligne.strip()]


# -------------------------------------------------------------- résolution
def ecrire_union(work: str, chemin: str) -> bool:
    """Union triée des lignes des deux côtés (docs, listes : aucune perte)."""
    lignes: set[str] = set()
    for stage in (2, 3):
        lignes.update(
            ligne.rstrip("\r") for ligne in contenu_stage(work, stage, chemin).splitlines()
        )
    contenu = "\n".join(sorted(ligne for ligne in lignes if ligne.strip()))
    try:
        with open(os.path.join(work, chemin), "w", encoding="utf-8", newline="\n") as fichier:
            fichier.write(contenu + "\n")
    except OSError as exc:
        print("[resolver] écriture impossible %s (%s)" % (chemin, exc), flush=True)
        return False
    return True


def resoudre_un_fichier(work: str, chemin: str, cle: str, transport=None) -> str:
    """Résout un fichier : IA d'abord, repli déterministe ensuite. Rend le mode retenu."""
    base_t = contenu_stage(work, 1, chemin)
    ours_t = contenu_stage(work, 2, chemin)
    theirs_t = contenu_stage(work, 3, chemin)
    contenu = resolve.resoudre_fichier(chemin, base_t, ours_t, theirs_t, cle, transport=transport)
    if contenu is not None:
        try:
            with open(os.path.join(work, chemin), "w", encoding="utf-8", newline="\n") as fichier:
                fichier.write(contenu if contenu.endswith("\n") else contenu + "\n")
            return "ia"
        except OSError as exc:
            print("[resolver] écriture IA impossible %s (%s)" % (chemin, exc), flush=True)
    mode = resolve.resoudre_heuristique(chemin)
    if mode == "base" and _git_ok(work, "checkout", "--ours", "--", chemin, timeout=60):
        return "heuristique"
    if mode == "union" and ecrire_union(work, chemin):
        return "heuristique"
    return "echec"


def une_passe(work: str, base_rev: str, cle: str, transport=None) -> Bilan:
    """Rejoue `git rebase base_rev` sur la branche courante, en résolvant les conflits."""
    bilan = Bilan()
    if _git_ok(work, "rebase", "--abort", timeout=120):
        print("[resolver] rebase précédent annulé", flush=True)
    resultat = git(work, "rebase", "--empty=drop", base_rev, timeout=900)
    rounds = 0
    while resultat is not None and resultat.returncode != 0 and rounds < ROUNDS_MAX:
        rounds += 1
        conflits = fichiers_en_conflit(work)
        if not conflits:
            break
        for chemin in conflits:
            print("[resolver] conflit : %s" % chemin, flush=True)
            mode = resoudre_un_fichier(work, chemin, cle, transport=transport)
            if mode == "ia":
                bilan.ia.append(chemin)
            elif mode == "heuristique":
                bilan.heuristique.append(chemin)
                if resolve.est_derive(chemin):
                    bilan.regenerer.append(chemin)
            else:
                bilan.echecs.append(chemin)
        if bilan.echecs:
            break
        git(work, "add", "--", *conflits, timeout=120)
        resultat = git(work, "rebase", "--continue", timeout=900)
    bilan.ok = resultat is not None and resultat.returncode == 0
    return bilan


def rebaser(work: str, base_rev: str, cle: str, transport=None) -> Bilan:
    """[HERMES-LOOP] IA, puis déterministe : aucune prise de côté forcée."""
    dernier = Bilan()
    for passe in range(1, PASSES_MAX + 1):
        use_llm = passe % 2 == 1  # passes 1 et 3 : IA ; passes 2 et 4 : déterministe
        if use_llm and not cle:
            continue
        dernier = une_passe(work, base_rev, cle if use_llm else "", transport=transport)
        if dernier.ok:
            return dernier
        print("[resolver][LOOP] passe %d/%d échouée" % (passe, PASSES_MAX), flush=True)
        git(work, "rebase", "--abort", timeout=120)
    return dernier


# ------------------------------------------------------------------ selftest
def depot_de_test(racine: str, avec_source: bool = True) -> str:
    """Dépôt jetable avec de VRAIS conflits sur `docs/NOTES.md`, `uv.lock` et une source."""

    def ecrire(chemin: str, contenu: str) -> None:
        complet = os.path.join(racine, chemin)
        os.makedirs(os.path.dirname(complet) or racine, exist_ok=True)
        with open(complet, "w", encoding="utf-8") as fichier:
            fichier.write(contenu)

    def commit(message: str) -> None:
        git(racine, "add", "-A")
        git(racine, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)

    git(racine, "init", "-q", "-b", "main")
    ecrire("docs/NOTES.md", "ligne A\n")
    ecrire("uv.lock", "version = 1\n")
    if avec_source:
        ecrire("src/jauge/x.py", "VAL = 1\n")
    commit("base")
    git(racine, "checkout", "-q", "-b", "feat")
    ecrire("docs/NOTES.md", "ligne PR\n")
    ecrire("uv.lock", "version = 2\n")
    if avec_source:
        ecrire("src/jauge/x.py", "VAL = 2\n")
    commit("pr")
    git(racine, "checkout", "-q", "main")
    ecrire("docs/NOTES.md", "ligne base\n")
    ecrire("uv.lock", "version = 3\n")
    if avec_source:
        ecrire("src/jauge/x.py", "VAL = 3\n")
    commit("base avance")
    git(racine, "checkout", "-q", "feat")
    return racine


def _test_escalade(echecs: list[str]) -> None:
    """Une source en conflit n'est jamais résolue au hasard : elle escalade."""
    racine = tempfile.mkdtemp(prefix="hermes-rebase-1-")
    try:
        depot_de_test(racine, avec_source=True)
        bilan = rebaser(racine, "main", "")
        if not bilan.echecs:
            echecs.append("source en conflit résolue sans escalade")
        if "src/jauge/x.py" not in bilan.echecs:
            echecs.append("fichier source non signalé comme échec")
        if bilan.ok:
            echecs.append("rebase déclaré réussi malgré un conflit non résolu")
    finally:
        shutil.rmtree(racine, ignore_errors=True)


def _test_derives(echecs: list[str]) -> None:
    """Dérivés et docs seuls : le rebase doit aboutir, côté base pour `uv.lock`."""
    racine = tempfile.mkdtemp(prefix="hermes-rebase-2-")
    try:
        depot_de_test(racine, avec_source=False)
        bilan = une_passe(racine, "main", "")
        if not bilan.ok:
            echecs.append("rebase non abouti alors que seuls dérivés/docs sont en conflit")
        if "uv.lock" not in bilan.regenerer:
            echecs.append("uv.lock non signalé « à régénérer »")
        chemin = os.path.join(racine, "uv.lock")
        contenu = ""
        if os.path.exists(chemin):
            with open(chemin, encoding="utf-8") as fichier:
                contenu = fichier.read()
        if "version = 3" not in contenu:
            echecs.append("uv.lock non résolu côté base (attendu version = 3)")
        notes = os.path.join(racine, "docs/NOTES.md")
        if os.path.exists(notes):
            with open(notes, encoding="utf-8") as fichier:
                texte = fichier.read()
            if "ligne PR" not in texte or "ligne base" not in texte:
                echecs.append("union de documentation incomplète")
    finally:
        shutil.rmtree(racine, ignore_errors=True)


def selftest() -> int:
    """Contrôles sur un VRAI rebase git, hors réseau et hors secret."""
    echecs: list[str] = []
    for controle in (_test_escalade, _test_derives):
        controle(echecs)
    for echec in echecs:
        print("  KO  %s" % echec)
    if echecs:
        print("rebase.py --selftest : %d échec(s)" % len(echecs))
        return 1
    print("rebase.py --selftest : OK (rebase git réel, escalade et dérivés ; sans réseau)")
    return 0


if __name__ == "__main__":
    sys.exit(selftest())
