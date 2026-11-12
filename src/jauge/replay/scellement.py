"""Scellement : empreinte canonique et échantillon figé (§13.1, piste n° 1).

Le scellement est ce qui rend la preuve **infalsifiable par construction** :
plutôt que de demander de faire confiance au producteur d'une mesure, on lui
attache une empreinte calculée **avant** la mesure, et toute divergence avec
cette empreinte est refusée explicitement.

Deux responsabilités, dans l'ordre de la chaîne de preuve :

1. **Empreinte canonique** (`serialiser_canonique`, `empreinte_canonique`) :
   SHA-256 d'une sérialisation JSON canonique — clés triées à tous les niveaux,
   séparateurs sans espace, `ensure_ascii=False`, `allow_nan=False`. Sans
   canonicité, deux dictionnaires au même contenu mais dans un ordre différent
   produisent deux empreintes, et la preuve n'est plus vérifiable par un tiers.
   La même brique est réutilisée par le protocole d'évaluation
   (`jauge.evaluation.protocole`) : une seule implémentation, aucun double.

2. **Échantillon figé** (`sceller_echantillon`, `verifier_echantillon`) : une
   fois tiré, l'échantillon ne peut plus être re-sélectionné. Sans cela, un
   évaluateur pressé pourrait re-tirer jusqu'à obtenir un échantillon favorable
   au candidat — le « cherry-picking » que le §13.1 veut rendre impossible.

ENF-CONF-01 : le sceau ne porte que des **métadonnées** (identifiant de requête,
type, longueur, jetons d'entrée, strate, compte d'exclusions). Aucun texte de
requête ni de réponse n'entre ici, donc aucun ne peut en sortir.

ENF-IA-01 : aucune valeur n'est produite par un modèle ; l'empreinte est un
hachage déterministe de la bibliothèque standard (`hashlib`, `json`).

Note d'architecture — la forme canonique existe aussi dans
`jauge.reports.scellement` (brique du dossier de preuve). Elle n'est **pas**
importée ici, et ce n'est pas un oubli : `jauge.reports.__init__` importe
`proof_pack` → `proof_pack_sections` → `jauge.evaluation` → `decision` →
`protocole` → `jauge.replay.scellement`, donc un import de
`jauge.reports.scellement` depuis ce module boucle (`ImportError: partially
initialized module`, vérifié). Les deux implémentations sont volontairement
identiques au caractère près (mêmes séparateurs, mêmes options `json`), donc
elles produisent les mêmes empreintes. La déduplication propre passe par un
déplacement de la brique dans `jauge/common/scellement.py` (paquet verrouillé :
à faire par la PR d'intégration), les deux modules ne faisant alors que
l'importer.

Références : §13.1 (tirage reproductible, stratification, exclusions), §16
(traçabilité), piste n° 1 du top 10.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from jauge.common.errors import EtatIncompatibleError
from jauge.common.ids import horodatage_utc
from jauge.models.pricing import TarifModele
from jauge.replay.sampling import Echantillon

#: Algorithme de l'empreinte (standard, reproductible partout).
ALGORITHME = "sha256"
#: Séparateurs JSON canoniques : aucun espace, donc aucun écart de formatage.
SEPARATEURS = (",", ":")
#: Auteur par défaut d'un scellement automatique (aucune identité humaine).
SCELLE_PAR_DEFAUT = "harnais-jauge"


def _repli_serialisation(objet: Any) -> Any:
    """Convertit les types usuels non-JSON en forme canonique stable."""
    if isinstance(objet, datetime | date):
        return objet.isoformat()
    if isinstance(objet, Enum):
        return objet.value
    if isinstance(objet, tuple | set | frozenset):
        return list(objet)
    raise EtatIncompatibleError(
        "Type non sérialisable dans une empreinte de preuve", type_objet=type(objet).__name__
    )


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


def empreinte_canonique(donnees: object) -> str:
    """Empreinte SHA-256 (hexadécimal) de la forme canonique de `donnees`.

    Déterministe : deux structures au même contenu, quel que soit l'ordre de
    leurs clés, rendent la même empreinte. C'est cette propriété qui permet à un
    tiers de revérifier seul un protocole ou un échantillon scellé.
    """
    return hashlib.sha256(octets_canoniques(donnees)).hexdigest()


def empreinte_table_tarifs(tarifs: Sequence[TarifModele]) -> str:
    """Empreinte de la table des tarifs datée, triée pour rester reproductible.

    Le tri (modèle, niveau, date d'effet, région) rend l'empreinte indépendante
    de l'ordre de chargement du fichier de tarifs : la même table, chargée dans
    un autre ordre, donne la même empreinte (§16 traçabilité, Annexe D).
    """
    lignes = [
        tarif.model_dump(mode="json")
        for tarif in sorted(
            tarifs,
            key=lambda ligne: (
                ligne.modele_id,
                str(ligne.niveau),
                ligne.date_effet.isoformat(),
                ligne.region,
            ),
        )
    ]
    return empreinte_canonique({"algorithme": ALGORITHME, "lignes": lignes})


class EchantillonScelle(BaseModel):
    """Sceau d'un échantillon figé : son empreinte, quand et par qui (§13.1).

    Aucune donnée de requête n'y figure (ENF-CONF-01) : uniquement l'empreinte
    et les paramètres de tirage, qui suffisent à un tiers pour rejouer la
    vérification.
    """

    empreinte: str
    scelle_le: datetime
    scelle_par: str = SCELLE_PAR_DEFAUT
    application_id: str = ""
    graine: int = 0
    taille: int = Field(default=0, ge=0)

    def vers_dict(self) -> dict[str, Any]:
        """Vue sérialisable du sceau, prête pour l'audit et le rapport."""
        return {
            "algorithme": ALGORITHME,
            "empreinte": self.empreinte,
            "scelle_le": horodatage_utc(self.scelle_le),
            "scelle_par": self.scelle_par,
            "application_id": self.application_id,
            "graine": self.graine,
            "taille": self.taille,
        }


def charge_utile_echantillon(echantillon: Echantillon) -> dict[str, Any]:
    """Charge utile scellée : tout ce qui définit l'échantillon, sans texte.

    La répartition par strate et les comptes d'exclusion font partie de la
    charge utile : un échantillon dont la composition déclarée ne correspond
    plus aux requêtes listées est donc détecté lui aussi.
    """
    return {
        "algorithme": ALGORITHME,
        "application_id": echantillon.application_id,
        "graine": echantillon.graine,
        "taille": echantillon.taille,
        "exclusions_sensibles": echantillon.exclusions_sensibles,
        "hors_application": echantillon.hors_application,
        "strates": dict(sorted(echantillon.strates.items())),
        "requetes": [requete.model_dump(mode="json") for requete in echantillon.requetes],
    }


def sceller_echantillon(
    echantillon: Echantillon,
    *,
    scelle_par: str = SCELLE_PAR_DEFAUT,
    scelle_le: datetime | None = None,
) -> EchantillonScelle:
    """Scelle un échantillon dès son tirage : il ne pourra plus être re-tiré.

    Le scellement se fait **une fois**, avant le rejeu : c'est l'ordre qui rend
    l'engagement crédible (§13.1). Un échantillon scellé après coup ne prouve
    rien — d'où l'exigence que la campagne porte cette empreinte.
    """
    moment = scelle_le or datetime.now(UTC)
    return EchantillonScelle(
        empreinte=empreinte_canonique(charge_utile_echantillon(echantillon)),
        scelle_le=moment,
        scelle_par=scelle_par,
        application_id=echantillon.application_id,
        graine=echantillon.graine,
        taille=echantillon.taille,
    )


def verifier_echantillon(echantillon: Echantillon, scelle: EchantillonScelle) -> None:
    """Vérifie que l'échantillon présenté est bien celui qui a été scellé.

    Lève `EtatIncompatibleError` à la moindre divergence : une requête
    remplacée, retirée, ajoutée, réordonnée, une strate recomptée, une graine ou
    une application différente. Jamais de verdict silencieux sur un échantillon
    recomposé (anti cherry-picking, §13.1).
    """
    calculee = empreinte_canonique(charge_utile_echantillon(echantillon))
    if calculee != scelle.empreinte:
        raise EtatIncompatibleError(
            "Échantillon retouché : il ne correspond plus à l'empreinte scellée",
            empreinte_attendue=scelle.empreinte,
            empreinte_calculee=calculee,
            taille_attendue=scelle.taille,
            taille_presentee=echantillon.taille,
        )


def echantillon_conforme(echantillon: Echantillon, scelle: EchantillonScelle) -> bool:
    """Variante booléenne de `verifier_echantillon`, pour un affichage d'audit."""
    try:
        verifier_echantillon(echantillon, scelle)
    except EtatIncompatibleError:
        return False
    return True
