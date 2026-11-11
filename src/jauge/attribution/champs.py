"""Lecture tolérante des noms de champs (§23 Q2).

Les journaux d'invocation Bedrock et les exports CUR 2.0 n'ont pas de noms de
champs confirmés par la spécification (§23 Q2). Plutôt que de parier sur une
orthographe, chaque module déclare les variantes qu'il accepte et s'appuie sur
les fonctions de ce module pour les retrouver :

- :func:`normaliser_cle` ramène un nom à sa forme canonique (minuscules, sans
  séparateur) : `inputTokenCount`, `input_token_count` et `Input-Token-Count`
  deviennent `inputtokencount` ;
- :func:`aplatir` déplie un enregistrement imbriqué en gardant le chemin complet
  (`usage.inputTokens`) **et** le nom court (`inputTokens`), si bien qu'une
  source plate et une source imbriquée se lisent avec la même table ;
- :func:`valeur`, :func:`texte`, :func:`entier` et :func:`booleen` lisent une
  valeur typée en ignorant les formes inattendues au lieu de lever.

Tout champ inconnu est simplement absent du dictionnaire aplati : l'appelant
décide s'il le signale (comptage de dérive) ou l'ignore.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import lru_cache

#: Valeurs textuelles interprétées comme « vrai ».
VRAI = {"true", "1", "oui", "yes", "y", "vrai", "sensible", "confidentiel"}


@lru_cache(maxsize=4096)
def normaliser_cle(cle: object) -> str:
    """Forme canonique d'un nom de champ : minuscules, sans séparateur.

    Le résultat est mémorisé : sur un million d'enregistrements, les mêmes
    dizaines de noms de champs reviennent en boucle (ENF-PERF-01).
    """
    return re.sub(r"[^a-z0-9]", "", str(cle).lower())


def decompresser_json(charge: Mapping[str, object]) -> dict[str, object]:
    """Remplace les valeurs JSON en chaîne par leur objet décompressé.

    Certains journaux d'invocation transportent les métadonnées de requête et
    les métriques d'invocation sous forme de **chaîne JSON** (colonne texte d'un
    export CSV, ou sérialisation du collecteur). Le parseur les accepte : la clé
    devient un objet et l'aplatissement fait le reste. Une chaîne illisible est
    laissée telle quelle : elle ne casse pas l'enregistrement.
    """
    decompresse = dict(charge)
    for cle, brut in charge.items():
        if isinstance(brut, str) and brut[:1] in ("{", "["):
            try:
                objet = json.loads(brut)
            except json.JSONDecodeError:
                continue
            if isinstance(objet, dict):
                decompresse[cle] = objet
    return decompresse


def deballer_enveloppe(charge: Mapping[str, object]) -> dict[str, object]:
    """Descend dans une enveloppe (`message`, `detail`, `record`, `invocation`).

    Certains collecteurs enveloppent l'invocation dans un objet de transport ;
    la charge utile est la première enveloppe qui contient un objet.
    """
    charge_mut = dict(charge)
    for enveloppe in ("message", "detail", "record", "invocation"):
        interieur = charge_mut.get(enveloppe)
        if isinstance(interieur, dict):
            return deballer_enveloppe(interieur)
    return charge_mut


def variantes(noms: tuple[str, ...]) -> tuple[str, ...]:
    """Formes canoniques d'une liste d'orthographes acceptées."""
    return tuple(normaliser_cle(nom) for nom in noms)


def aplatir(charge: Mapping[str, object], prefixe: str = "") -> dict[str, object]:
    """Aplatit un enregistrement : chemin complet **et** nom court pour chaque feuille.

    Les listes sont ignorées : le contenu des messages d'une requête ne doit
    jamais être lu ni conservé (ENF-CONF-01).
    """
    plat: dict[str, object] = {}
    for cle, valeur in charge.items():
        chemin = f"{prefixe}{cle}"
        if isinstance(valeur, Mapping) and valeur:
            for nom, sous_valeur in aplatir(valeur, f"{chemin}.").items():
                plat.setdefault(nom, sous_valeur)
            continue
        if isinstance(valeur, list | tuple):
            continue
        plat.setdefault(normaliser_cle(chemin), valeur)
        plat.setdefault(normaliser_cle(cle), valeur)
    return plat


def valeur(plat: Mapping[str, object], noms: tuple[str, ...]) -> object | None:
    """Première variante connue présente et non vide, ou `None`."""
    for nom in noms:
        trouvee = plat.get(normaliser_cle(nom))
        if trouvee is not None and trouvee != "":
            return trouvee
    return None


def texte(plat: Mapping[str, object], noms: tuple[str, ...]) -> str | None:
    """Valeur textuelle non vide d'un champ, ou `None`."""
    trouvee = valeur(plat, noms)
    if trouvee is None:
        return None
    contenu = str(trouvee).strip()
    return contenu or None


def entier(plat: Mapping[str, object], noms: tuple[str, ...]) -> int | None:
    """Valeur entière positive d'un champ, ou `None` si absente ou illisible."""
    trouvee = valeur(plat, noms)
    if isinstance(trouvee, bool) or trouvee is None:
        return None
    try:
        nombre = int(float(str(trouvee).strip()))
    except (TypeError, ValueError):
        return None
    return max(nombre, 0)


def booleen(plat: Mapping[str, object], noms: tuple[str, ...]) -> bool:
    """Drapeau interprété sans exception : `true`, `oui`, `1`, `sensible`…"""
    trouvee = valeur(plat, noms)
    if isinstance(trouvee, bool):
        return trouvee
    if trouvee is None:
        return False
    return str(trouvee).strip().lower() in VRAI
