"""Garde-fous de confidentialité des agrégats publiés : k-anonymité et bruit DP.

Exigences : EF-RAP-02 (tableau de bord du parc MSP), EF-RAP-03 (rapport),
EF-ADM-01 (seuils par client), ENF-CONF-01 (le texte reste chez le client).

Tout agrégat qui **quitte le compte du client** passe par ici :

1. **k-anonymité** — une cellule publiée doit reposer sur au moins `k` entités
   (requêtes, applications, jours ou clients selon le niveau). En dessous, elle
   est supprimée ou regroupée dans « autres », et le **nombre de suppressions
   est rendu visible** : jamais de cellule à un élément publiée en silence.
2. **Bruit DP** — les comparaisons **inter-clients** (benchmark de parc) portent
   un bruit de Laplace calibré sur une sensibilité bornée, et `epsilon`
   accompagne toujours le chiffre publié : un chiffre sans epsilon n'a pas de sens.

Aucun chiffre n'est produit par un modèle (ENF-IA-01) ; le bruit n'utilise que
`random` (graine fixée → reproductible) et `math` de la bibliothèque standard.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

from pydantic import BaseModel, Field

from jauge.common.errors import ValidationError
from jauge.common.ids import deterministic, graine_de, horodatage_utc, jour_compact
from jauge.models.cost import AgregatQuotidien
from jauge.store.protocol import Store

#: k par défaut : une cellule publiée repose sur au moins 5 entités.
K_DEFAUT = 5
#: Plancher absolu de k : en dessous, l'agrégat redevient identifiable.
K_PLANCHER = 2
#: epsilon par défaut du mécanisme de Laplace (benchmark inter-clients).
EPSILON_DEFAUT = 1.0
#: Libellé de la catégorie de regroupement des cellules sous le seuil.
LIBELLE_AUTRES = "autres"
#: Stratégies de traitement des cellules sous le seuil.
STRATEGIE_REGROUPER = "regrouper"
STRATEGIE_SUPPRIMER = "supprimer"
STRATEGIES = (STRATEGIE_REGROUPER, STRATEGIE_SUPPRIMER)
#: Dimensions d'attribution disponibles pour former des cellules (EF-ATT-03).
DIMENSIONS = ("application", "equipe", "modele", "jour")


def _valider_k(k: int) -> int:
    """Contrôle le seuil k : jamais sous le plancher (refus explicite, 422)."""
    if k < K_PLANCHER:
        raise ValidationError("k must not go below the floor", k=k, plancher=K_PLANCHER)
    return k


class PolitiqueAnonymite(BaseModel):
    """Politique d'un client : k et epsilon ; `plancher_k` est rendu, non réglable."""

    k: int = Field(default=K_DEFAUT, ge=K_PLANCHER)
    epsilon: float = Field(default=EPSILON_DEFAUT, gt=0.0)
    plancher_k: int = Field(default=K_PLANCHER, ge=1)


@dataclass(frozen=True)
class ModificationSeuil:
    """Trace d'un changement de seuil : qui, quand, quelle valeur."""

    tenant_id: str
    k: int
    epsilon: float
    auteur: str
    quand: str


class SeuilsAnonymiteStore(Protocol):
    """Contrat de persistance des seuils de confidentialité, par client."""

    def obtenir_politique(self, tenant_id: str) -> PolitiqueAnonymite:
        """Rend la politique du client (défaut documenté si rien n'est réglé)."""
        ...

    def definir_seuil(
        self, tenant_id: str, k: int, epsilon: float | None = None, auteur: str = ""
    ) -> PolitiqueAnonymite:
        """Règle k (et optionnellement epsilon), en refusant de passer sous le plancher."""
        ...

    def journal(self, tenant_id: str | None = None) -> list[ModificationSeuil]:
        """Rend les modifications de seuil, dans l'ordre chronologique."""
        ...


class MemorySeuilsAnonymiteStore:
    """Seuils en mémoire (tests, démo, plan de contrôle) ; chaque modification
    est journalisée en mémoire, en plus du journal d'audit de l'API.
    """

    def __init__(self, k_defaut: int = K_DEFAUT, epsilon_defaut: float = EPSILON_DEFAUT) -> None:
        _valider_k(k_defaut)
        self._k_defaut = k_defaut
        self._epsilon_defaut = epsilon_defaut
        self._politiques: dict[str, PolitiqueAnonymite] = {}
        self._journal: list[ModificationSeuil] = []

    def obtenir_politique(self, tenant_id: str) -> PolitiqueAnonymite:
        """Rend la politique du client, ou le défaut documenté."""
        return self._politiques.get(tenant_id) or PolitiqueAnonymite(
            k=self._k_defaut, epsilon=self._epsilon_defaut
        )

    def definir_seuil(
        self, tenant_id: str, k: int, epsilon: float | None = None, auteur: str = ""
    ) -> PolitiqueAnonymite:
        """Règle le seuil du client ; refuse de descendre sous le plancher."""
        _valider_k(k)
        if epsilon is not None and epsilon <= 0:
            raise ValidationError("epsilon must be strictly positive", epsilon=epsilon)
        courante = self.obtenir_politique(tenant_id)
        nouvelle = PolitiqueAnonymite(k=k, epsilon=epsilon or courante.epsilon)
        self._politiques[tenant_id] = nouvelle
        self._journal.append(
            ModificationSeuil(
                tenant_id=tenant_id,
                k=nouvelle.k,
                epsilon=nouvelle.epsilon,
                auteur=auteur,
                quand=horodatage_utc(datetime.now(UTC)),
            )
        )
        return nouvelle

    def journal(self, tenant_id: str | None = None) -> list[ModificationSeuil]:
        """Historique des modifications, filtré par client si demandé."""
        if tenant_id is None:
            return list(self._journal)
        return [ligne for ligne in self._journal if ligne.tenant_id == tenant_id]


@dataclass(frozen=True)
class CelluleAgregat:
    """Cellule d'un agrégat : une valeur et l'effectif qui la fonde.

    `entites` justifie la publication : requêtes (ou jours, applications) pour
    un client, clients contributeurs pour une comparaison inter-clients.
    """

    cle: str
    valeur: float
    entites: int
    libelle: str | None = None


@dataclass(frozen=True)
class CellulePubliee:
    """Cellule autorisée à la publication (effectif >= k)."""

    cle: str
    libelle: str
    valeur: float
    entites: int
    regroupee: bool = False


@dataclass(frozen=True)
class ResultatAnonymisation:
    """Ce qui est publié, et ce qui a été retiré — les deux sont visibles."""

    cellules: tuple[CellulePubliee, ...]
    k_applique: int
    strategie: str
    nombre_supprimees: int = 0
    nombre_regroupees: int = 0
    entites_supprimees: int = 0


@dataclass(frozen=True)
class ValeurPubliee:
    """Chiffre publié, avec le epsilon qui le protège (jamais l'un sans l'autre)."""

    cle: str
    libelle: str
    valeur: float
    entites: int
    epsilon: float
    sensibilite: float
    k: int
    regroupee: bool = False

    def vers_dict(self) -> dict[str, object]:
        """Contrat JSON de la valeur publiée : epsilon inclus, systématiquement."""
        return {
            "cle": self.cle,
            "libelle": self.libelle,
            "valeur": self.valeur,
            "entites": self.entites,
            "epsilon": self.epsilon,
            "sensibilite": self.sensibilite,
            "k": self.k,
            "regroupee": self.regroupee,
        }


@dataclass(frozen=True)
class PublicationBenchmark:
    """Publication inter-clients : valeurs bruitées et compte des suppressions."""

    valeurs: tuple[ValeurPubliee, ...]
    k_applique: int
    epsilon: float
    sensibilite: float
    nombre_supprimees: int = 0
    nombre_regroupees: int = 0

    def vers_dict(self) -> dict[str, object]:
        """Contrat JSON du benchmark : k, epsilon et suppressions au premier niveau."""
        return {
            "k_applique": self.k_applique,
            "epsilon": self.epsilon,
            "sensibilite": self.sensibilite,
            "nombre_supprimees": self.nombre_supprimees,
            "nombre_regroupees": self.nombre_regroupees,
            "valeurs": [valeur.vers_dict() for valeur in self.valeurs],
        }


def _valider_parametres(k: int, strategie: str) -> None:
    """Contrôle le seuil k (jamais sous le plancher) et la stratégie retenue."""
    _valider_k(k)
    if strategie not in STRATEGIES:
        raise ValidationError(
            "Unknown anonymisation strategy", strategie=strategie, strategies=list(STRATEGIES)
        )


def _cellule_autres(petites: Sequence[CelluleAgregat]) -> CellulePubliee | None:
    """Regroupe les cellules sous le seuil dans « autres » (effectif sommé)."""
    if not petites:
        return None
    return CellulePubliee(
        cle=LIBELLE_AUTRES,
        libelle="Autres",
        valeur=round(sum(cellule.valeur for cellule in petites), 6),
        entites=sum(cellule.entites for cellule in petites),
        regroupee=True,
    )


def anonymiser_cellules(
    cellules: Iterable[CelluleAgregat],
    k: int = K_DEFAUT,
    strategie: str = STRATEGIE_REGROUPER,
) -> ResultatAnonymisation:
    """Applique la k-anonymité à un jeu de cellules, sans publication silencieuse.

    `supprimer` retire toute cellule sous le seuil ; `regrouper` (défaut) les
    fusionne dans « autres », publiée seulement si son effectif atteint k —
    sinon supprimée à son tour. Les comptes sont rendus avec le résultat.
    """
    _valider_parametres(k, strategie)
    publiables: list[CellulePubliee] = []
    petites: list[CelluleAgregat] = []
    for cellule in cellules:
        if cellule.entites >= k:
            publiables.append(
                CellulePubliee(
                    cle=cellule.cle,
                    libelle=cellule.libelle or cellule.cle,
                    valeur=round(cellule.valeur, 6),
                    entites=cellule.entites,
                )
            )
        else:
            petites.append(cellule)
    autres = _cellule_autres(petites) if strategie == STRATEGIE_REGROUPER else None
    if autres is not None and autres.entites >= k:
        publiables.append(autres)
        return ResultatAnonymisation(
            cellules=_trier(publiables),
            k_applique=k,
            strategie=strategie,
            nombre_regroupees=len(petites),
        )
    return ResultatAnonymisation(
        cellules=_trier(publiables),
        k_applique=k,
        strategie=strategie,
        nombre_supprimees=len(petites),
        entites_supprimees=sum(cellule.entites for cellule in petites),
    )


def _trier(cellules: list[CellulePubliee]) -> tuple[CellulePubliee, ...]:
    """Tri stable : les plus grosses valeurs d'abord, « autres » en dernier."""
    return tuple(
        sorted(cellules, key=lambda cellule: (cellule.regroupee, -cellule.valeur, cellule.cle))
    )


def compte_rendu_suppressions(resultat: ResultatAnonymisation) -> str:
    """Phrase qui rend les suppressions visibles (rapport, portail, export)."""
    if resultat.nombre_supprimees == 0 and resultat.nombre_regroupees == 0:
        return f"Toutes les cellules reposent sur au moins k={resultat.k_applique} entités."
    morceaux = [f"seuil k={resultat.k_applique}"]
    if resultat.nombre_supprimees:
        morceaux.append(
            f"{resultat.nombre_supprimees} cellule(s) supprimée(s) "
            f"({resultat.entites_supprimees} entité(s) concernée(s))"
        )
    if resultat.nombre_regroupees:
        morceaux.append(f"{resultat.nombre_regroupees} cellule(s) regroupée(s) dans « autres »")
    return " ; ".join(morceaux) + "."


def echelle_laplace(sensibilite: float, epsilon: float) -> float:
    """Échelle du mécanisme de Laplace : sensibilité bornée / epsilon."""
    if epsilon <= 0:
        raise ValidationError("epsilon must be strictly positive", epsilon=epsilon)
    if sensibilite <= 0:
        raise ValidationError("Sensitivity must be strictly positive", sensibilite=sensibilite)
    return sensibilite / epsilon


def bruit_laplace(
    valeur: float, sensibilite: float, epsilon: float, graine: int | None = None
) -> float:
    """Ajoute un bruit de Laplace calibré (mécanisme epsilon-DP).

    Tirage par inversion de la fonction de répartition (`random` de la
    bibliothèque standard) : à graine fixée, le bruit est reproductible.
    """
    echelle = echelle_laplace(sensibilite, epsilon)
    tirage = random.Random(graine)
    u = tirage.random() - 0.5
    signe = 1.0 if u >= 0 else -1.0
    bruit = -signe * echelle * math.log(1.0 - 2.0 * abs(u))
    return round(valeur + bruit, 6)


def graine_cellule(graine: int | None, cle: str) -> int | None:
    """Dérive une graine par cellule : reproductible et non corrélé entre cellules."""
    if graine is None:
        return None
    return graine_de(graine, "benchmark", cle)


def publier_benchmark(
    cellules: Iterable[CelluleAgregat],
    *,
    k: int = K_DEFAUT,
    epsilon: float = EPSILON_DEFAUT,
    sensibilite: float = 1.0,
    graine: int | None = None,
    strategie: str = STRATEGIE_REGROUPER,
) -> PublicationBenchmark:
    """Publie des cellules **inter-clients** : k-anonymité puis bruit DP.

    Le bruit n'est ajouté qu'ici, jamais sur les agrégats que le client consulte
    sur son propre compte : un chiffre bruité ne se réconcilie pas avec sa
    facture. Chaque valeur publiée emporte son epsilon et sa sensibilité.
    """
    resultat = anonymiser_cellules(cellules, k=k, strategie=strategie)
    valeurs = tuple(
        ValeurPubliee(
            cle=cellule.cle,
            libelle=cellule.libelle,
            valeur=bruit_laplace(
                cellule.valeur,
                sensibilite,
                epsilon,
                graine=graine_cellule(graine, cellule.cle),
            ),
            entites=cellule.entites,
            epsilon=epsilon,
            sensibilite=sensibilite,
            k=k,
            regroupee=cellule.regroupee,
        )
        for cellule in resultat.cellules
    )
    return PublicationBenchmark(
        valeurs=valeurs,
        k_applique=resultat.k_applique,
        epsilon=epsilon,
        sensibilite=sensibilite,
        nombre_supprimees=resultat.nombre_supprimees,
        nombre_regroupees=resultat.nombre_regroupees,
    )


def identifiant_publique(identifiant: str) -> str:
    """Empreinte stable d'un identifiant : un identifiant de requête publié tel
    quel permettrait de retrouver sa requête, donc son texte (ENF-CONF-01).
    """
    return deterministic("anonymisation", identifiant)


def cle_dimension(agregat: AgregatQuotidien, dimension: str) -> str:
    """Rend la clé d'agrégation d'un agrégat pour une dimension d'attribution."""
    if dimension == "application":
        return agregat.application_id
    if dimension == "equipe":
        return agregat.equipe
    if dimension == "modele":
        return agregat.modele_id
    if dimension == "jour":
        return jour_compact(agregat.jour)
    raise ValidationError(
        "Unknown aggregation dimension", dimension=dimension, choix=list(DIMENSIONS)
    )


def cellules_par_dimension(
    store: Store,
    tenant_id: str,
    dimension: str = "modele",
    debut: date | None = None,
    fin: date | None = None,
) -> list[CelluleAgregat]:
    """Cellules de coût d'**un** client : l'effectif est le nombre de requêtes.
    Aucun bruit n'est ajouté : seule la k-anonymité s'applique à la publication.
    """
    totaux: dict[str, float] = {}
    effectifs: dict[str, int] = {}
    for agregat in store.lister_agregats(tenant_id, debut=debut, fin=fin):
        cle = cle_dimension(agregat, dimension)
        totaux[cle] = totaux.get(cle, 0.0) + agregat.cout_recalcule_usd
        effectifs[cle] = effectifs.get(cle, 0) + agregat.nombre_requetes
    return [
        CelluleAgregat(cle=cle, valeur=round(totaux[cle], 6), entites=effectifs[cle])
        for cle in sorted(totaux)
    ]


def cellules_par_client(
    store: Store,
    tenant_ids: Sequence[str],
    dimension: str = "modele",
    debut: date | None = None,
    fin: date | None = None,
) -> list[CelluleAgregat]:
    """Cellules **inter-clients** : l'effectif est le nombre de clients contributeurs.

    Une cellule qui ne repose que sur un ou deux clients révélerait leur
    dépense individuelle ; aucun identifiant de client n'entre dans la cellule.
    """
    totaux: dict[str, float] = {}
    contributeurs: dict[str, set[str]] = {}
    for tenant_id in tenant_ids:
        for cellule in cellules_par_dimension(store, tenant_id, dimension, debut, fin):
            totaux[cellule.cle] = totaux.get(cellule.cle, 0.0) + cellule.valeur
            contributeurs.setdefault(cellule.cle, set()).add(tenant_id)
    return [
        CelluleAgregat(cle=cle, valeur=round(totaux[cle], 6), entites=len(contributeurs[cle]))
        for cle in sorted(totaux)
    ]


def publier_agregats_client(
    store: Store,
    tenant_id: str,
    k: int = K_DEFAUT,
    dimension: str = "modele",
    debut: date | None = None,
    fin: date | None = None,
    strategie: str = STRATEGIE_REGROUPER,
) -> ResultatAnonymisation:
    """Agrégats d'un client destinés à être publiés : k-anonymité obligatoire."""
    cellules = cellules_par_dimension(store, tenant_id, dimension, debut, fin)
    return anonymiser_cellules(cellules, k=k, strategie=strategie)


def publier_benchmark_parc(
    store: Store,
    tenant_ids: Sequence[str],
    politique: PolitiqueAnonymite,
    dimension: str = "modele",
    debut: date | None = None,
    fin: date | None = None,
    sensibilite: float = 1.0,
    graine: int | None = None,
) -> PublicationBenchmark:
    """Benchmark de parc (EF-RAP-02) : k-anonymité puis bruit DP, epsilon rendu."""
    cellules = cellules_par_client(store, tenant_ids, dimension, debut, fin)
    return publier_benchmark(
        cellules,
        k=politique.k,
        epsilon=politique.epsilon,
        sensibilite=sensibilite,
        graine=graine,
    )
