"""Entrées typées du dossier de preuve : ce que le pack sait d'une campagne.

Ces structures sont la **seule** porte d'entrée du contenu d'un « proof pack ».
Elles portent des identifiants, des empreintes et des nombres déjà calculés par
le socle — jamais un texte de requête ni de réponse (ENF-CONF-01), jamais un
nombre produit par un modèle (ENF-IA-01).

Elles vivent dans leur propre module pour que `proof_pack_sections` (les
constructeurs de sections) et `proof_pack` (le modèle et l'assemblage) restent
chacun sous la limite de taille du dépôt, sans import circulaire.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from jauge.common.errors import ValidationError
from jauge.models.campaign import EchantillonRequete, ResultatCandidat
from jauge.replay.executor import MesuresCandidat

#: Champs du profil moyen de jetons d'un candidat (base du calcul Annexe D).
CHAMPS_PROFIL_JETONS = (
    "jetons_entree_moyens",
    "jetons_sortie_moyens",
    "jetons_cache_ecriture_moyens",
    "jetons_cache_lecture_moyens",
)

#: Correspondance profil moyen (publié dans le pack) → `MetriquesRequete` (socle).
CHAMPS_METRIQUES = {
    "jetons_entree_moyens": "jetons_entree",
    "jetons_sortie_moyens": "jetons_sortie",
    "jetons_cache_ecriture_moyens": "jetons_cache_ecriture",
    "jetons_cache_lecture_moyens": "jetons_cache_lecture",
}


@dataclass(frozen=True)
class EntreeEchantillon:
    """Une requête de l'échantillon figé : identifiant et empreinte, jamais le texte.

    `empreinte_texte` est calculée **dans le plan de données** (seul endroit où
    le texte existe) puis transmise telle quelle : ce module ne voit jamais le
    texte, donc ne peut pas le faire fuiter (ENF-CONF-01). À défaut, l'empreinte
    de l'entrée reste calculable depuis ses seules métadonnées.
    """

    requete_id: str
    type_requete: str = "general"
    longueur_caracteres: int = 0
    jetons_entree_origine: int = 0
    strate: str = "court"
    empreinte_texte: str | None = None

    @classmethod
    def depuis(
        cls, requete: EchantillonRequete, empreinte_texte: str | None = None
    ) -> EntreeEchantillon:
        """Convertit une `EchantillonRequete` du socle (§13.1) en entrée de pack."""
        return cls(
            requete_id=requete.requete_id,
            type_requete=requete.type_requete,
            longueur_caracteres=requete.longueur_caracteres,
            jetons_entree_origine=requete.jetons_entree_origine,
            strate=requete.stratum,
            empreinte_texte=empreinte_texte,
        )

    def vers_dict(self) -> dict[str, Any]:
        """Forme sérialisable de l'entrée (aucun texte, uniquement des empreintes)."""
        return {
            "requete_id": self.requete_id,
            "empreinte": self.empreinte_texte or "",
            "type_requete": self.type_requete,
            "longueur_caracteres": self.longueur_caracteres,
            "jetons_entree_origine": self.jetons_entree_origine,
            "strate": self.strate,
        }


@dataclass(frozen=True)
class ResumeControle:
    """Résultat agrégé d'un contrôle déterministe sur tout l'échantillon (§13.3).

    `executions` est le nombre de paires sur lesquelles le contrôle a tourné,
    `echecs` le nombre de paires en échec. Aucun motif d'échec n'est conservé
    ici : les motifs peuvent citer des fragments de réponse (§13.3), donc ils
    restent dans le plan de données (ENF-CONF-01).
    """

    nom: str
    executions: int
    echecs: int
    critique: bool = True

    def vers_dict(self) -> dict[str, Any]:
        """Forme sérialisable du résumé de contrôle."""
        return {
            "nom": self.nom,
            "executions": self.executions,
            "echecs": self.echecs,
            "critique": self.critique,
        }


@dataclass(frozen=True)
class EntreeCandidat:
    """Tout ce que le pack sait d'un candidat : résultat consolidé + mesures.

    - `resultat` : `ResultatCandidat` du socle (taux, intervalle, latences…) ;
    - `mesures` : `MesuresCandidat` du rejeu (totaux de jetons, coût mesuré) —
      c'est ce qui rend le coût par requête **recalculable** par un tiers ;
    - `controles` : résumés des contrôles déterministes (§13.3) ;
    - `succes` : nombre de paires équivalentes, quand l'appelant le connaît (sinon
      il est retrouvé depuis le taux déclaré, voir `succes_declares`).
    """

    modele_id: str
    resultat: ResultatCandidat
    mesures: MesuresCandidat | None = None
    controles: Sequence[ResumeControle] = field(default_factory=tuple)
    succes: int | None = None
    jetons_cache_ecriture_total: int = 0
    jetons_cache_lecture_total: int = 0

    def __post_init__(self) -> None:
        """Refuse un candidat dont le résultat porte un autre modèle."""
        if self.resultat.modele_id != self.modele_id:
            raise ValidationError(
                "modèle du résultat différent du modèle du candidat",
                candidat=self.modele_id,
                resultat=self.resultat.modele_id,
            )

    def profil_jetons(self) -> dict[str, int] | None:
        """Profil moyen de jetons par **invocation**, en entiers (Annexe D).

        `None` si le rejeu n'a pas fourni ses mesures : le coût par requête du
        pack reste alors celui déclaré par le socle, mais il n'est plus
        recalculable par un tiers — le rejeu des vérifications le signale.
        """
        if self.mesures is None:
            return None
        invocations = self.mesures.executions or self.mesures.nombre_requetes_rejouees
        if invocations <= 0:
            return None
        totaux = (
            self.mesures.jetons_entree_total,
            self.mesures.jetons_sortie_total,
            self.jetons_cache_ecriture_total,
            self.jetons_cache_lecture_total,
        )
        return dict(
            zip(CHAMPS_PROFIL_JETONS, (round(total / invocations) for total in totaux), strict=True)
        )

    def succes_declares(self) -> int:
        """Nombre de paires jugées équivalentes, retrouvé si besoin.

        `ResultatCandidat` ne porte que le taux arrondi à 6 décimales ; le compte
        entier se retrouve par `round(taux × n)`, exact tant que `n × 5·10⁻⁷ < 0,5`
        soit `n < 1 000 000` — très au-delà des échantillons visés (§13.1 : 200).
        """
        if self.succes is not None:
            return self.succes
        return round(self.resultat.taux_equivalence * self.resultat.nombre_requetes_rejouees)


@dataclass(frozen=True)
class ResumeRelecture:
    """Relecture humaine d'un sous-échantillon (§13.5, ENF-CONF-02).

    Seuls des comptes sont publiés : le texte relu reste dans le compte du
    client, et le relecteur n'est identifié que par un identifiant.
    """

    taille_sous_echantillon: int = 30
    paires_relues: int = 0
    paires_concordantes: int = 0
    relecteur_id: str = ""
    accord: float | None = None

    def vers_dict(self, autorisee: bool) -> dict[str, Any]:
        """Section `relecture` : comptes + accord déclaré (recalculable)."""
        return {
            "autorisee": autorisee,
            "taille_sous_echantillon": self.taille_sous_echantillon,
            "paires_relues": self.paires_relues,
            "paires_concordantes": self.paires_concordantes,
            "accord_juge_humain": self.accord,
            "relecteur_id": self.relecteur_id,
            "note": "human review happens inside the client account (ENF-CONF-02); counts only",
        }


@dataclass(frozen=True)
class Approbation:
    """Qui a approuvé, et quand (EF-REC-02, ENF-IA-02, §16 traçabilité)."""

    statut: str
    auteur: str | None = None
    horodatage: str | None = None
    notes: str = ""


def normaliser_protocole(source: object) -> dict[str, Any] | None:
    """Accepte un protocole pré-enregistré sous une forme **souple**.

    Le lot « protocole scellé » (`jauge.evaluation.protocole`) n'est pas encore
    sur `main` : ce module ne dépend donc pas de son existence. Sont acceptés
    `None`, un `Mapping` portant une clé `empreinte`, ou tout objet exposant un
    attribut `empreinte` (plus, au choix, `vers_dict()`, `model_dump()` ou
    `metadata`). L'empreinte est exigée : un protocole sans empreinte n'est pas
    un protocole pré-enregistré.
    """
    if source is None:
        return None
    protocole = dict(source) if isinstance(source, Mapping) else _protocole_depuis_objet(source)
    if not protocole.get("empreinte"):
        raise ValidationError("protocole pré-enregistré sans empreinte")
    return protocole


def _protocole_depuis_objet(source: object) -> dict[str, Any]:
    """Extrait empreinte et métadonnées d'un objet protocole quelconque."""
    empreinte_declaree = getattr(source, "empreinte", None)
    if not isinstance(empreinte_declaree, str):
        raise ValidationError(
            "protocole pré-enregistré illisible : attribut `empreinte` absent ou non textuel"
        )
    for methode in ("vers_dict", "model_dump"):
        fabrique = getattr(source, methode, None)
        if callable(fabrique):
            donnees = dict(fabrique())
            donnees["empreinte"] = empreinte_declaree
            return donnees
    metadata = getattr(source, "metadata", None)
    return {
        "empreinte": empreinte_declaree,
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
    }
