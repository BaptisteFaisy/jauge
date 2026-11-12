"""Scellement cryptographique : sérialisation canonique, chaîne de hachage, signature.

C'est la brique de confiance du « proof pack » (piste 6 du top 10) : un dossier
de preuve qu'un tiers — auditeur, juge du hackathon, client — peut revérifier
seul, sans faire confiance au producteur.

Trois garanties distinctes, trois outils distincts :

1. **Sérialisation canonique** (`serialiser_canonique`) : JSON à clés triées,
   séparateurs fixes, `ensure_ascii=False`, NaN/Inf refusés. Sans canonicité, la
   même donnée produit deux octets différents selon l'ordre d'un dictionnaire et
   l'empreinte n'est plus reproductible (§16 traçabilité).
2. **Chaîne de hachage** (`sceller`, `verifier_chaine`) : chaque section est
   hachée en SHA-256 et chaque hachage inclut le précédent. Toute modification
   d'une section casse la chaîne **à partir de cette section** : `verifier_chaine`
   rend le rang exact de la première rupture.
3. **Signature** (`SignataireHMAC`) : HMAC-SHA256 de l'empreinte finale, calculé
   avec le secret de l'approbateur. Elle prouve l'origine, là où la chaîne ne
   prouve que l'intégrité.

Ce que la chaîne ne garantit **pas** : un attaquant capable de réécrire *toutes*
les sections puis de resceller produira une chaîne cohérente, mais une empreinte
finale **différente**. L'ancrage externe (`constat_detache`, compartiment en
écriture seule type S3 Object Lock) est donc ce qui rend la falsification
complète détectable : le tiers compare l'empreinte du pack à celle publiée
séparément au moment de l'approbation.

Contraintes respectées :
- **std only** : `hashlib`, `hmac`, `json` — aucune dépendance ajoutée ;
- **ENF-IA-01** : aucun chiffre produit par un modèle n'entre ici ;
- **ENF-CONF-01** : ce module ne voit que des structures déjà construites ; il ne
  transporte jamais de texte de requête ni de réponse.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Protocol

#: Algorithme de la chaîne de hachage (std only, reproductible partout).
ALGORITHME = "sha256"
#: Algorithme de la signature d'approbation.
ALGORITHME_SIGNATURE = "hmac-sha256"
#: Empreinte initiale de la chaîne : 64 zéros, comme le bloc genèse d'une chaîne
#: de blocs. Elle rend le premier maillon dépendant d'une valeur publique fixe.
GENESE = "0" * 64
#: Séparateurs JSON canoniques : aucun espace, donc aucun écart de formatage.
SEPARATEURS = (",", ":")


def _repli_serialisation(objet: Any) -> Any:
    """Convertit les types usuels non-JSON en valeur canonique (dates, enums)."""
    if isinstance(objet, datetime | date):
        return objet.isoformat()
    if isinstance(objet, Enum):
        return objet.value
    if isinstance(objet, tuple | set | frozenset):
        return list(objet)
    raise TypeError(f"Type non sérialisable dans un dossier de preuve : {type(objet).__name__}")


def serialiser_canonique(donnees: object) -> str:
    """Sérialisation canonique : même contenu → exactement les mêmes octets.

    Clés triées à tous les niveaux, séparateurs `,` et `:` sans espace,
    `ensure_ascii=False` (l'UTF-8 est conservé tel quel, donc stable d'une
    machine à l'autre), `allow_nan=False` (un NaN rendrait l'empreinte
    dépendante de la représentation flottante).
    """
    return json.dumps(
        donnees,
        sort_keys=True,
        separators=SEPARATEURS,
        ensure_ascii=False,
        allow_nan=False,
        default=_repli_serialisation,
    )


def octets_canoniques(donnees: object) -> bytes:
    """Octets UTF-8 de la forme canonique (base de toute empreinte)."""
    return serialiser_canonique(donnees).encode("utf-8")


def empreinte(donnees: object) -> str:
    """Empreinte SHA-256 (hexadécimal) de la forme canonique de `donnees`."""
    return hashlib.sha256(octets_canoniques(donnees)).hexdigest()


def empreinte_liee(empreinte_precedente: str, empreinte_section: str) -> str:
    """Maillon de la chaîne : inclut l'empreinte précédente, donc tout l'amont."""
    joint = f"{empreinte_precedente}|{empreinte_section}"
    return hashlib.sha256(joint.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MaillonChaine:
    """Un maillon : nom de section, empreinte propre, empreinte cumulative."""

    nom: str
    empreinte: str
    empreinte_cumulee: str

    def vers_dict(self) -> dict[str, str]:
        """Forme sérialisable du maillon (ordre des clés fixé par la canonicité)."""
        return {
            "nom": self.nom,
            "empreinte": self.empreinte,
            "empreinte_cumulee": self.empreinte_cumulee,
        }

    @classmethod
    def depuis_dict(cls, donnees: Mapping[str, Any]) -> MaillonChaine:
        """Relit un maillon depuis le sceau stocké ; refuse un maillon tronqué."""
        try:
            return cls(
                nom=str(donnees["nom"]),
                empreinte=str(donnees["empreinte"]),
                empreinte_cumulee=str(donnees["empreinte_cumulee"]),
            )
        except (KeyError, TypeError) as erreur:
            raise ValueError(f"Maillon de chaîne invalide : {donnees!r}") from erreur


def sceller(sections: Sequence[tuple[str, object]]) -> dict[str, Any]:
    """Scelle une liste ordonnée de sections en une chaîne de hachage.

    `sections` est une séquence de couples `(nom, contenu)` **dans l'ordre de la
    chaîne** : l'ordre fait partie du scellement, un réordonnancement change
    l'empreinte finale.
    """
    maillons: list[MaillonChaine] = []
    precedente = GENESE
    for nom, contenu in sections:
        propre = empreinte(contenu)
        cumulee = empreinte_liee(precedente, propre)
        maillons.append(MaillonChaine(nom=nom, empreinte=propre, empreinte_cumulee=cumulee))
        precedente = cumulee
    return {
        "algorithme": ALGORITHME,
        "genese": GENESE,
        "sections": [maillon.vers_dict() for maillon in maillons],
        "empreinte_pack": precedente,
    }


class Signataire(Protocol):
    """Port de signature d'un dossier de preuve (HMAC en std, ou autre)."""

    cle_id: str

    def signer(self, empreinte_pack: str) -> str: ...

    def verifier(self, empreinte_pack: str, signature: str) -> bool: ...


@dataclass(frozen=True)
class SignataireHMAC:
    """Signature HMAC-SHA256 de l'empreinte du pack (std only).

    Le secret n'est jamais écrit dans le pack : seul `cle_id` l'identifie. Un
    tiers qui détient la clé (le client, par contrat) peut vérifier l'origine ;
    un tiers qui ne l'a pas vérifie au minimum la chaîne et compare l'empreinte
    au constat publié séparément.
    """

    secret: bytes
    cle_id: str = "msp-2026"

    def signer(self, empreinte_pack: str) -> str:
        """Signature hexadécimale de l'empreinte du pack."""
        return hmac.new(self.secret, empreinte_pack.encode("utf-8"), hashlib.sha256).hexdigest()

    def verifier(self, empreinte_pack: str, signature: str) -> bool:
        """Comparaison à temps constant (aucun oracle de longueur)."""
        return hmac.compare_digest(self.signer(empreinte_pack), signature)


def sceller_et_signer(
    sections: Sequence[tuple[str, object]], signataire: Signataire | None = None
) -> dict[str, Any]:
    """Scelle la chaîne, puis signe l'empreinte finale si un signataire est fourni."""
    sceau = sceller(sections)
    if signataire is not None:
        sceau["signature"] = {
            "algorithme": ALGORITHME_SIGNATURE,
            "cle_id": signataire.cle_id,
            "valeur": signataire.signer(str(sceau["empreinte_pack"])),
        }
    return sceau


def verifier_signature(sceau: Mapping[str, Any], signataire: Signataire) -> bool:
    """Vérifie la signature du sceau ; faux si aucune signature n'y figure."""
    signature = sceau.get("signature")
    if not isinstance(signature, Mapping):
        return False
    if signature.get("cle_id") != signataire.cle_id:
        return False
    empreinte_pack = str(sceau.get("empreinte_pack", ""))
    return signataire.verifier(empreinte_pack, str(signature.get("valeur", "")))


@dataclass(frozen=True)
class ResultatChaine:
    """Verdict de la vérification de chaîne, avec la localisation de la rupture.

    `position` est le rang **1-based** de la première section en défaut dans
    l'ordre déclaré du sceau ; `None` quand la chaîne est conforme.
    """

    conforme: bool
    position: int | None = None
    section: str | None = None
    raison: str = ""
    empreinte_attendue: str = ""
    empreinte_calculee: str = ""

    def vers_dict(self) -> dict[str, Any]:
        """Forme sérialisable du verdict (utile au rapport de vérification)."""
        return {
            "conforme": self.conforme,
            "position": self.position,
            "section": self.section,
            "raison": self.raison,
            "empreinte_attendue": self.empreinte_attendue,
            "empreinte_calculee": self.empreinte_calculee,
        }


def extraire_sections_et_sceau(pack: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sections et sceau d'un pack : objet `ProofPack` ou document déjà sérialisé."""
    if isinstance(pack, Mapping):
        sections = pack.get("sections")
        sceau = pack.get("scellement")
    else:
        sections = getattr(pack, "sections", None)
        if callable(sections):  # un pack peut exposer ses sections par méthode
            sections = sections()
        sceau = getattr(pack, "scellement", None)
    if not isinstance(sections, Mapping) or not isinstance(sceau, Mapping):
        raise ValueError("Pack illisible : `sections` et `scellement` sont requis")
    return dict(sections), dict(sceau)


def _verifier_ordre(
    sections: Mapping[str, Any], maillons: Sequence[MaillonChaine]
) -> ResultatChaine | None:
    """Contrôle que le sceau couvre exactement les sections présentes, dans l'ordre."""
    noms_sceau = [maillon.nom for maillon in maillons]
    manquantes = [nom for nom in sections if nom not in noms_sceau]
    if manquantes:
        return ResultatChaine(
            conforme=False,
            position=len(maillons) + 1,
            section=manquantes[0],
            raison=f"section présente dans le pack mais absente du sceau : {manquantes[0]}",
        )
    orphelines = [nom for nom in noms_sceau if nom not in sections]
    if orphelines:
        return ResultatChaine(
            conforme=False,
            position=noms_sceau.index(orphelines[0]) + 1,
            section=orphelines[0],
            raison=f"section scellée absente du pack : {orphelines[0]}",
        )
    return None


def verifier_chaine(pack: Any) -> ResultatChaine:
    """Recalcule toute la chaîne et rend la position exacte de la première rupture.

    Un tiers appelle cette fonction sur le pack qu'il a reçu : elle ne fait
    confiance à aucune valeur déclarée, elle **recalcule** chaque empreinte et
    chaque maillon. Trois familles de rupture sont distinguées : contenu de
    section modifié, maillon recollé à tort, empreinte finale incohérente.
    """
    sections, sceau = extraire_sections_et_sceau(pack)
    try:
        maillons = [MaillonChaine.depuis_dict(item) for item in sceau.get("sections", [])]
    except ValueError as erreur:
        return ResultatChaine(conforme=False, raison=str(erreur))
    if not maillons:
        return ResultatChaine(conforme=False, raison="sceau vide : aucune section scellée")
    ecart_ordre = _verifier_ordre(sections, maillons)
    if ecart_ordre is not None:
        return ecart_ordre

    precedente = str(sceau.get("genese", GENESE))
    for rang, maillon in enumerate(maillons, start=1):
        rupture = _verifier_maillon(sections[maillon.nom], maillon, precedente, rang)
        if rupture is not None:
            return rupture
        precedente = maillon.empreinte_cumulee

    declaree = str(sceau.get("empreinte_pack", ""))
    if declaree != precedente:
        return ResultatChaine(
            conforme=False,
            position=len(maillons),
            section=maillons[-1].nom,
            raison="empreinte finale du pack incohérente avec la chaîne recalculée",
            empreinte_attendue=declaree,
            empreinte_calculee=precedente,
        )
    return ResultatChaine(conforme=True, empreinte_calculee=precedente)


def _verifier_maillon(
    contenu: Any, maillon: MaillonChaine, precedente: str, rang: int
) -> ResultatChaine | None:
    """Vérifie un maillon : empreinte propre, puis empreinte cumulative."""
    calculee = empreinte(contenu)
    if calculee != maillon.empreinte:
        return ResultatChaine(
            conforme=False,
            position=rang,
            section=maillon.nom,
            raison="contenu de la section modifié après scellement",
            empreinte_attendue=maillon.empreinte,
            empreinte_calculee=calculee,
        )
    attendue = empreinte_liee(precedente, maillon.empreinte)
    if attendue != maillon.empreinte_cumulee:
        return ResultatChaine(
            conforme=False,
            position=rang,
            section=maillon.nom,
            raison="maillon recollé : l'empreinte cumulative ne dépend pas de l'amont",
            empreinte_attendue=maillon.empreinte_cumulee,
            empreinte_calculee=attendue,
        )
    return None


def constat_detache(
    pack_id: str, sceau: Mapping[str, Any], *, horodatage: str, auteur: str
) -> dict[str, Any]:
    """Constat d'approbation détaché : l'ancre externe qui rend toute réécriture
    complète détectable.

    Il ne contient que l'empreinte finale, l'identifiant du pack, l'horodatage et
    l'auteur de l'approbation. Il est destiné à être publié **ailleurs** que le
    pack (journal d'audit du client, courriel, autre compartiment) : le tiers
    compare `empreinte_pack` du constat à celle qu'il recalcule.
    """
    constat = {
        "pack_id": pack_id,
        "algorithme": sceau.get("algorithme", ALGORITHME),
        "empreinte_pack": sceau.get("empreinte_pack", ""),
        "horodatage": horodatage,
        "auteur": auteur,
    }
    constat["empreinte_constat"] = empreinte_constat(constat)
    return constat


def empreinte_constat(constat: Mapping[str, Any]) -> str:
    """Empreinte interne d'un constat, recalculée depuis ses champs déclarés."""
    return empreinte(
        {
            "pack_id": constat.get("pack_id", ""),
            "empreinte_pack": constat.get("empreinte_pack", ""),
            "horodatage": constat.get("horodatage", ""),
            "auteur": constat.get("auteur", ""),
        }
    )


def verifier_constat(constat: Mapping[str, Any], pack: Any) -> bool:
    """Vrai si le pack reçu correspond au constat publié séparément.

    Deux conditions, toutes deux nécessaires :
    - le constat n'a pas été retouché (son empreinte interne est cohérente) ;
    - l'empreinte du pack est bien celle qui a été publiée à l'approbation.
    """
    if empreinte_constat(constat) != constat.get("empreinte_constat", ""):
        return False
    _, sceau = extraire_sections_et_sceau(pack)
    return sceau.get("empreinte_pack") == constat.get("empreinte_pack")
