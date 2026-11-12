"""Protocole d'évaluation pré-enregistré, scellé avant tout rejeu (§13, piste n° 1).

Ce module répond à l'objection la plus sérieuse qu'un juge technique peut faire à
une campagne de rejeu : « avez-vous choisi la méthode **avant** de voir les
résultats ? » Sans pré-enregistrement, un évaluateur peut toujours ajuster un
seuil, une marge ou un échantillon après coup, et la mesure ne prouve plus rien.

Trois garanties, une par artefact :

1. **Pré-enregistrement** (`ProtocoleEvaluation`) : la marge de non-infériorité,
   les seuils de décision, le plan d'échantillonnage (taille, graines, strates,
   exclusions), l'identifiant et la version du prompt du juge, les contrôles
   déterministes actifs, l'empreinte de la table des tarifs et les versions du
   harnais sont fixés **avant** la première invocation de modèle (§13.1 à §13.5).
2. **Scellement** (`ProtocoleScelle`, `sceller`) : l'empreinte SHA-256 de la
   sérialisation canonique du protocole (clés triées, aucun ordre aléatoire) est
   calculée une fois. Le protocole scellé est **immuable** : toute tentative de
   modification lève `EtatIncompatibleError`, jamais un changement silencieux.
3. **Vérification à la décision** (`verifier_scelle`) : la règle de décision
   (`jauge.evaluation.decision`) refuse de conclure sur un protocole dont
   l'empreinte recalculée ne correspond plus à l'empreinte scellée. Une dérive —
   même produite par une copie falsifiée — est donc détectée au moment où elle
   compte, c'est-à-dire au verdict.

L'échantillon, lui, est scellé par `jauge.replay.scellement` : le protocole fige
le **plan** de tirage, le sceau d'échantillon fige le **tirage** réalisé.

ENF-CONF-01 : aucun texte de requête ni de réponse n'entre ici ; le protocole ne
porte que des identifiants, des paramètres et des empreintes.
ENF-IA-01 : aucune valeur n'est produite par un modèle ; l'empreinte est un
hachage déterministe et les seuils viennent du code ou du client.
ENF-SEC-02 : le protocole est rattaché à une application d'un client identifié.

Références : §12.3 (garde-fous), §13.1 (échantillon), §13.3 (mesures),
§13.4 (règle de décision), §13.5 (fiabilité du juge), §16 (traçabilité).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from jauge import __version__
from jauge.common.errors import EtatIncompatibleError
from jauge.common.ids import horodatage_utc
from jauge.evaluation.checks import controles_pour
from jauge.evaluation.judge import NOM_METRIQUE, RegistreCapacite, valider_juge
from jauge.models.campaign import Campagne
from jauge.models.tenant import Application
from jauge.replay.scellement import (
    ALGORITHME,
    SCELLE_PAR_DEFAUT,
    EchantillonScelle,
    empreinte_canonique,
)

#: Version du format du protocole (à incrémenter à chaque champ ajouté).
VERSION_PROTOCOLE = "1.0"

#: Marge de non-infériorité par défaut : 2 points, alignée sur la tolérance
#: d'auto-accord du §13.4 (`jauge.evaluation.decision.MARGE_AUTO_ACCORD`).
MARGE_NON_INFERIORITE_DEFAUT = 0.02

#: Paquets dont la version fait partie du protocole scellé (reproductibilité).
PAQUETS_HARNAIS = (
    "jauge",
    "jauge.replay.sampling",
    "jauge.replay.executor",
    "jauge.evaluation.judge",
    "jauge.evaluation.checks",
    "jauge.evaluation.scoring",
    "jauge.evaluation.decision",
)


class ProtocoleScelle(BaseModel):
    """Sceau d'un protocole : son empreinte, quand et par qui (§16 traçabilité).

    L'empreinte porte sur la **charge utile** du protocole, jamais sur le sceau
    lui-même : un sceau ne peut pas se contenir.
    """

    empreinte: str = Field(min_length=1)
    scelle_le: datetime
    scelle_par: str = Field(default=SCELLE_PAR_DEFAUT, min_length=1)

    def vers_dict(self) -> dict[str, Any]:
        """Vue sérialisable du sceau (horodatage ISO, aucune donnée de requête)."""
        return {
            "algorithme": ALGORITHME,
            "empreinte": self.empreinte,
            "scelle_le": horodatage_utc(self.scelle_le),
            "scelle_par": self.scelle_par,
        }


class SeuilsProtocole(BaseModel):
    """Seuils de décision pré-engagés (§13.4, conditions 1 et 2)."""

    seuil_equivalence: float = Field(default=0.95, gt=0.0, le=1.0)
    borne_basse_intervalle: float = Field(default=0.90, gt=0.0, le=1.0)


class PlanEchantillonnage(BaseModel):
    """Plan de tirage pré-engagé (§13.1) : taille, graines, strates, exclusions.

    Les listes sont triées et dédupliquées à la validation : l'empreinte ne
    dépend donc pas de l'ordre de saisie, seulement du contenu.
    """

    taille: int = Field(default=200, ge=10, le=2000)
    fenetre_jours: int = Field(default=14, ge=1, le=365)
    graines: list[int] = Field(default_factory=list)
    strates: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    stratification_par_longueur: bool = True
    stratification_par_type: bool = True

    @field_validator("graines")
    @classmethod
    def _graines_triees(cls, valeur: list[int]) -> list[int]:
        return sorted(set(valeur))

    @field_validator("strates", "exclusions")
    @classmethod
    def _listes_triees(cls, valeur: list[str]) -> list[str]:
        return sorted({element for element in valeur if element.strip()})


class ProtocoleEvaluation(BaseModel):
    """Protocole d'évaluation figé avant le rejeu, puis scellé.

    Un protocole non scellé reste modifiable (il est encore en cours de
    rédaction) ; dès que `scelle` est posé, l'objet devient immuable : ni
    affectation d'attribut, ni `model_copy(update=…)`, ni `modifier()` ne
    passent, et tous lèvent `EtatIncompatibleError`.
    """

    version: str = VERSION_PROTOCOLE
    application_id: str = Field(min_length=1)
    modele_juge_id: str = Field(min_length=1)
    prompt_juge_id: str = Field(default=NOM_METRIQUE, min_length=1)
    prompt_juge_version: str = Field(default=VERSION_PROTOCOLE, min_length=1)
    modeles_candidats: list[str] = Field(default_factory=list)
    plan: PlanEchantillonnage = Field(default_factory=PlanEchantillonnage)
    seuils: SeuilsProtocole = Field(default_factory=SeuilsProtocole)
    marge_non_inferiorite: float = Field(default=MARGE_NON_INFERIORITE_DEFAUT, ge=0.0, le=1.0)
    controles_deterministes: list[str] = Field(default_factory=list)
    table_tarifs_empreinte: str = ""
    versions_harnais: dict[str, str] = Field(default_factory=dict)
    scelle: ProtocoleScelle | None = None

    @field_validator("modeles_candidats", "controles_deterministes")
    @classmethod
    def _listes_triees(cls, valeur: list[str]) -> list[str]:
        """Ordre indépendant de la saisie : l'empreinte ne dépend que du contenu."""
        return sorted({element for element in valeur if element.strip()})

    # --- Empreinte et scellement ------------------------------------------
    def charge_utile(self) -> dict[str, Any]:
        """Contenu scellé du protocole : tout, sauf le sceau lui-même."""
        donnees = self.model_dump(mode="json")
        donnees.pop("scelle", None)
        return {"algorithme": ALGORITHME, "donnees": donnees}

    @property
    def empreinte(self) -> str:
        """Empreinte SHA-256 canonique du protocole (recalculable par un tiers).

        Exposée comme un **attribut** et non comme une méthode : le dossier de
        preuve (`jauge.reports.proof_pack_entrees.normaliser_protocole`) lit
        l'empreinte d'un protocole sans connaître sa classe.
        """
        return empreinte_canonique(self.charge_utile())

    @property
    def est_scelle(self) -> bool:
        """Vrai si le protocole porte son sceau."""
        return self.scelle is not None

    def avec_scelle(self, sceau: ProtocoleScelle) -> ProtocoleEvaluation:
        """Rend le protocole scellé : un objet neuf, immuable dès lors."""
        self._refuser_si_scelle("sceller une seconde fois")
        donnees = self.model_dump(mode="json")
        donnees["scelle"] = sceau.model_dump(mode="json")
        return ProtocoleEvaluation.model_validate(donnees)

    def modifier(self, **champs: object) -> ProtocoleEvaluation:
        """Rend une version modifiée du protocole — refusée s'il est scellé."""
        self._refuser_si_scelle("modifier un protocole scellé")
        donnees = self.model_dump(mode="json")
        donnees.update(champs)
        return ProtocoleEvaluation.model_validate(donnees)

    # --- Garde d'immuabilité ----------------------------------------------
    def _refuser_si_scelle(self, action: str) -> None:
        """Lève l'erreur métier explicite si le protocole est déjà scellé."""
        if self.scelle is not None:
            raise EtatIncompatibleError(
                f"Protocole scellé : {action} est interdit",
                application_id=self.application_id,
                empreinte=self.scelle.empreinte,
                scelle_le=horodatage_utc(self.scelle.scelle_le),
            )

    def __setattr__(self, nom: str, valeur: object) -> None:
        """Interdit toute affectation une fois le sceau posé."""
        self._refuser_si_scelle(f"affecter `{nom}`")
        super().__setattr__(nom, valeur)

    def model_copy(self, *, update: Any = None, deep: bool = False) -> ProtocoleEvaluation:
        """Interdit la copie modifiée d'un protocole scellé (contournement)."""
        if update:
            self._refuser_si_scelle("copier un protocole scellé en le modifiant")
        return super().model_copy(update=update, deep=deep)

    def vers_dict(self) -> dict[str, Any]:
        """Vue sérialisable complète (protocole + sceau), pour l'audit."""
        vue = self.model_dump(mode="json")
        if self.scelle is not None:
            vue["scelle"] = self.scelle.vers_dict()
        return vue


def sceller(
    protocole: ProtocoleEvaluation,
    *,
    scelle_par: str = SCELLE_PAR_DEFAUT,
    scelle_le: datetime | None = None,
) -> ProtocoleEvaluation:
    """Scelle un protocole : calcule son empreinte et rend l'objet immuable.

    À appeler **avant** le premier rejeu : c'est l'ordre qui rend l'engagement
    opposable. Le protocole rendu porte son sceau et ne peut plus être modifié.
    """
    sceau = ProtocoleScelle(
        empreinte=protocole.empreinte,
        scelle_le=scelle_le or datetime.now(UTC),
        scelle_par=scelle_par,
    )
    return protocole.avec_scelle(sceau)


def verifier_scelle(protocole: ProtocoleEvaluation) -> None:
    """Vérifie que le protocole présenté est bien celui qui a été scellé.

    Trois refus explicites, jamais un verdict silencieux : protocole absent de
    scellement, empreinte recalculée différente (dérive, contrefaçon, rejeu sur
    un protocole retouché), sceau illisible.
    """
    sceau = protocole.scelle
    if sceau is None:
        raise EtatIncompatibleError(
            "Protocole non scellé : impossible de conclure sur une méthode non engagée",
            application_id=protocole.application_id,
        )
    calculee = protocole.empreinte
    if calculee != sceau.empreinte:
        raise EtatIncompatibleError(
            "Dérive du protocole scellé : le protocole présenté ne correspond plus "
            "à l'empreinte engagée avant le rejeu",
            application_id=protocole.application_id,
            empreinte_attendue=sceau.empreinte,
            empreinte_calculee=calculee,
        )


def verifier_protocole_present(protocole: ProtocoleEvaluation | None) -> ProtocoleEvaluation:
    """Rend le protocole après vérification ; refuse une campagne sans protocole."""
    if protocole is None:
        raise EtatIncompatibleError(
            "Aucun protocole pré-enregistré présenté : la décision n'est pas concluable"
        )
    verifier_scelle(protocole)
    return protocole


def versions_harnais_defaut() -> dict[str, str]:
    """Versions du harnais au moment du scellement (reproductibilité, §16)."""
    return {paquet: __version__ for paquet in PAQUETS_HARNAIS}


def controles_deterministes_actifs(types_tache: Sequence[str]) -> list[str]:
    """Noms des contrôles déterministes actifs pour des types de tâche donnés.

    Le protocole pré-enregistre la **liste des contrôles** (§13.3, condition 3 du
    §13.4) : cette liste est lue dans le registre existant
    `jauge.evaluation.checks.CONTROLES_PAR_TACHE`, jamais recopiée.
    """
    noms = {
        controle.__name__ for type_tache in types_tache for controle in controles_pour(type_tache)
    }
    return sorted(noms)


def construire_protocole(
    *,
    application: Application,
    modeles_candidats: Sequence[str],
    modele_juge_id: str,
    plan: PlanEchantillonnage | None = None,
    marge_non_inferiorite: float = MARGE_NON_INFERIORITE_DEFAUT,
    controles_deterministes: Sequence[str] = (),
    table_tarifs_empreinte: str = "",
    versions_harnais: Mapping[str, str] | None = None,
    registre: RegistreCapacite | None = None,
    prompt_juge_id: str = NOM_METRIQUE,
    prompt_juge_version: str = VERSION_PROTOCOLE,
) -> ProtocoleEvaluation:
    """Construit le protocole d'une campagne depuis l'application (§13.4, §13.5).

    Les seuils viennent de l'application (`seuil_qualite`,
    `seuil_intervalle_bas`) : ce sont ceux du client, pas ceux de l'évaluateur.
    L'invite du juge est identifiée et versionnée (`prompt_juge_id`,
    `prompt_juge_version`) : changer de prompt, c'est changer de méthode, donc
    changer d'empreinte. Si un `registre` de capacité est fourni, la contrainte
    §13.5 sur le juge est vérifiée **ici**, au pré-enregistrement
    (`jauge.evaluation.judge.valider_juge`).
    """
    if registre is not None:
        valider_juge(modele_juge_id, application.modele_actuel, modeles_candidats, registre)
    return ProtocoleEvaluation(
        application_id=application.id,
        modele_juge_id=modele_juge_id,
        prompt_juge_id=prompt_juge_id,
        prompt_juge_version=prompt_juge_version,
        modeles_candidats=list(modeles_candidats),
        plan=plan or PlanEchantillonnage(),
        seuils=SeuilsProtocole(
            seuil_equivalence=application.seuil_qualite,
            borne_basse_intervalle=application.seuil_intervalle_bas,
        ),
        marge_non_inferiorite=marge_non_inferiorite,
        controles_deterministes=list(controles_deterministes),
        table_tarifs_empreinte=table_tarifs_empreinte,
        versions_harnais=dict(versions_harnais or versions_harnais_defaut()),
    )


def porter_empreintes(
    campagne: Campagne,
    protocole: ProtocoleEvaluation | None = None,
    echantillon_scelle: EchantillonScelle | None = None,
) -> Campagne:
    """Rend la campagne portant les empreintes de preuve (traçabilité, §16).

    Refuse un protocole non scellé : une campagne ne cite que des engagements
    effectivement pris. Les deux empreintes sont optionnelles pour rester
    rétrocompatible avec les campagnes déjà enregistrées.
    """
    update: dict[str, Any] = {}
    if protocole is not None:
        verifier_scelle(protocole)
        update["protocole_empreinte"] = protocole.scelle.empreinte if protocole.scelle else None
    if echantillon_scelle is not None:
        update["echantillon_empreinte"] = echantillon_scelle.empreinte
    return campagne.model_copy(update=update) if update else campagne


def resume_pre_enregistrement(
    campagne: Campagne,
    protocole: ProtocoleEvaluation | None = None,
) -> dict[str, Any]:
    """Ce que le rapport et la recommandation peuvent citer (aucun texte).

    `src/jauge/reports/` n'est pas modifié par ce lot : cette vue est ce qu'il
    faut passer au dossier de preuve et à l'Annexe B pour citer l'engagement.
    """
    resume: dict[str, Any] = {
        "protocole_empreinte": campagne.protocole_empreinte,
        "echantillon_empreinte": campagne.echantillon_empreinte,
    }
    if protocole is not None:
        resume.update(
            {
                "protocole_version": protocole.version,
                "marge_non_inferiorite": protocole.marge_non_inferiorite,
                "seuils": protocole.seuils.model_dump(mode="json"),
                "modele_juge_id": protocole.modele_juge_id,
                "prompt_juge_id": protocole.prompt_juge_id,
                "prompt_juge_version": protocole.prompt_juge_version,
                "scelle": protocole.scelle.vers_dict() if protocole.scelle else None,
            }
        )
    return resume
