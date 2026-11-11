"""Étiquettes d'attribution et part de dépense non attribuée (§8.1, §8.2, EF-ATT-03, EF-ATT-07).

Trois responsabilités, et rien d'autre :

1. **Extraire** les étiquettes d'attribution (application, équipe,
   environnement, type de requête) des métadonnées de requête attachées à
   chaque appel : elles apparaissent dans les journaux d'invocation, jamais sur
   la facture (§3.2).
2. **Compléter** ces étiquettes (EF-ATT-03) : application par défaut du schéma
   d'attribution pour les appels non étiquetés, repli sur la fiche de
   l'application pour l'équipe et l'environnement. Une dimension absente n'est
   jamais inventée en silence : `Etiquettes.est_attribuee` et
   `valeurs_hors_schema` disent ce qui manque.
3. **Chiffrer ce qui échappe à l'attribution** (EF-ATT-07) : part de dépense
   non attribuée et ses causes — appels sans étiquette, application hors
   schéma, point d'accès non journalisé (`bedrock-mantle`, §3.2).

`PointAcces` vit ici parce que « point d'accès non journalisé » est une *cause*
de non-attribution ; `logs.py` s'en sert pour qualifier chaque enregistrement.

Aucune formule de coût dans ce module : les montants arrivent déjà calculés par
`jauge.engine.calculator` (ENF-IA-01).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache

from jauge.models.tenant import Application, SchemaAttribution

#: Valeur d'équipe ou d'environnement quand aucune source ne la fournit.
VALEUR_INCONNUE = "inconnue"

#: Libellés de causes, affichés tels quels au client (EF-ATT-07).
CAUSE_SANS_ETIQUETTE = "appels sans étiquette d'application"
CAUSE_APPLICATION_HORS_SCHEMA = "application hors schéma d'attribution"
CAUSE_POINT_ACCES_NON_JOURNALISE = "point d'accès non journalisé (bedrock-mantle)"


class PointAcces(StrEnum):
    """Point d'accès Bedrock emprunté par un appel (§3.2)."""

    RUNTIME = "bedrock-runtime"
    MANTLE = "bedrock-mantle"
    INCONNU = "inconnu"

    @property
    def journalise(self) -> bool:
        """Seuls les appels `bedrock-runtime` finissent dans les journaux.

        Un point d'accès non reconnu est traité comme non journalisé : le coût
        qui lui correspond n'apparaîtra pas au recalcul, il vaut mieux le
        signaler comme cause d'écart que de le supposer journalisé.
        """
        return self is PointAcces.RUNTIME


#: Orthographes acceptées pour chaque dimension d'attribution (§23 Q2).
CHAMPS_ETIQUETTES: dict[str, tuple[str, ...]] = {
    "application": (
        "application",
        "applicationId",
        "application_id",
        "app",
        "appId",
        "inferenceProfile",
        "inferenceProfileId",
        "projectName",
    ),
    "equipe": ("team", "equipe", "équipe", "teamName", "owner", "businessUnit"),
    "environnement": ("environment", "environnement", "env", "stage", "stageName", "workspace"),
    "type_requete": (
        "requestType",
        "request_type",
        "typeRequete",
        "type_requete",
        "useCase",
        "use_case",
        "taskType",
        "task",
        "operationType",
        "intent",
    ),
}


@lru_cache(maxsize=4096)
def normaliser_cle(cle: object) -> str:
    """Forme canonique d'un nom de champ : minuscules, sans séparateur.

    `inputTokenCount`, `input_token_count` et `Input-Token-Count` donnent tous
    `inputtokencount` : c'est ce qui rend la lecture tolérante aux variantes.
    Le résultat est mémorisé : sur un million d'enregistrements, les mêmes
    dizaines de noms de champs reviennent en boucle (ENF-PERF-01).
    """
    return re.sub(r"[^a-z0-9]", "", str(cle).lower())


_VARIANTES: dict[str, tuple[str, ...]] = {
    dimension: tuple(normaliser_cle(variante) for variante in variantes)
    for dimension, variantes in CHAMPS_ETIQUETTES.items()
}

#: Préfixes de clés d'étiquettes rencontrés (`user:application`, `jauge:team`).
_PREFIXES = ("user:", "aws:", "jauge:", "tag:")


@dataclass(frozen=True, slots=True)
class Etiquettes:
    """Étiquettes d'attribution d'un appel, telles qu'extraites des métadonnées."""

    application: str | None = None
    equipe: str | None = None
    environnement: str | None = None
    type_requete: str | None = None

    @property
    def est_attribuee(self) -> bool:
        """Vrai si l'appel porte une étiquette d'application exploitable."""
        return bool(self.application)

    @property
    def completes(self) -> bool:
        """Vrai si les trois dimensions du schéma d'attribution sont présentes."""
        return all((self.application, self.equipe, self.environnement))

    def dimensions_manquantes(self) -> list[str]:
        """Dimensions absentes, dans l'ordre du schéma d'attribution (§8.1)."""
        return [
            dimension
            for dimension in ("application", "equipe", "environnement")
            if getattr(self, dimension) is None
        ]


def extraire_etiquettes(metadonnees: Mapping[str, object] | None) -> Etiquettes:
    """Lit les étiquettes d'attribution dans des métadonnées de requête.

    Les métadonnées peuvent être imbriquées (`requestMetadata`, `tags`,
    `{"jauge": {"application": …}}`) : les clés sont aplaties avant recherche,
    avec et sans préfixe (`user:application`). Une valeur vide ou non textuelle
    est ignorée plutôt que convertie en `"None"`.
    """
    if not metadonnees:
        return Etiquettes()
    aplaties = aplatir(metadonnees)
    valeurs = {
        dimension: _premiere_valeur(aplaties, variantes)
        for dimension, variantes in _VARIANTES.items()
    }
    return Etiquettes(**valeurs)


def aplatir(metadonnees: Mapping[str, object], prefixe: str = "") -> dict[str, str]:
    """Aplatit des métadonnées imbriquées en `nom normalisé` → texte.

    Le nom complet (`usage.inputTokens`) et le nom court (`inputTokens`) sont
    tous deux enregistrés : un enregistrement qui imbrique `usage` se lit donc
    comme un enregistrement plat.
    """
    aplaties: dict[str, str] = {}
    for cle, valeur in metadonnees.items():
        if isinstance(valeur, Mapping):
            for nom, texte in aplatir(valeur, f"{prefixe}{cle}.").items():
                aplaties.setdefault(nom, texte)
            continue
        texte = _texte(valeur)
        if texte is not None:
            aplaties.setdefault(normaliser_cle(_sans_prefixe(str(cle))), texte)
            if prefixe:
                aplaties.setdefault(normaliser_cle(f"{prefixe}{cle}"), texte)
    return aplaties


def _sans_prefixe(cle: str) -> str:
    """Retire un préfixe d'étiquette (`user:application` → `application`)."""
    minuscule = cle.lower()
    for prefixe in _PREFIXES:
        if minuscule.startswith(prefixe):
            return cle[len(prefixe) :]
    return cle


def _texte(valeur: object) -> str | None:
    """Texte non vide d'une valeur d'étiquette, ou `None`."""
    if isinstance(valeur, str):
        texte = valeur.strip()
        return texte or None
    if isinstance(valeur, int | float) and not isinstance(valeur, bool):
        return str(valeur)
    return None


def _premiere_valeur(aplaties: Mapping[str, str], variantes: tuple[str, ...]) -> str | None:
    """Première variante connue présente dans les métadonnées aplaties."""
    for variante in variantes:
        if variante in aplaties:
            return aplaties[variante]
    return None


def appliquer_schema(etiquettes: Etiquettes, schema: SchemaAttribution | None = None) -> Etiquettes:
    """Applique l'application par défaut aux appels non étiquetés (EF-ATT-03).

    L'application par défaut du schéma du client remplace l'étiquette absente :
    la dépense reste ainsi rattachée à une ligne connue, et
    :func:`calculer_part_non_attribuee` la compte en non-attribué pour que le
    client voie ce qu'il lui reste à étiqueter.
    """
    if etiquettes.est_attribuee:
        return etiquettes
    defaut = (schema or SchemaAttribution()).application_par_defaut or "non-attribue"
    return Etiquettes(
        application=defaut,
        equipe=etiquettes.equipe,
        environnement=etiquettes.environnement,
        type_requete=etiquettes.type_requete,
    )


def completer(etiquettes: Etiquettes, application: Application | None) -> Etiquettes:
    """Complète équipe et environnement depuis la fiche de l'application.

    Une application connue du plan de contrôle porte déjà son équipe et son
    environnement : inutile de les redemander à chaque appel. Faute de source,
    la valeur `VALEUR_INCONNUE` est posée explicitement (jamais une chaîne
    vide, qui casserait les regroupements).
    """
    return Etiquettes(
        application=etiquettes.application,
        equipe=etiquettes.equipe or (application.equipe if application else VALEUR_INCONNUE),
        environnement=etiquettes.environnement
        or (application.environnement if application else VALEUR_INCONNUE),
        type_requete=etiquettes.type_requete,
    )


def valeurs_hors_schema(etiquettes: Etiquettes, schema: SchemaAttribution) -> list[str]:
    """Dimensions dont la valeur sort des valeurs autorisées du schéma.

    Le schéma est déclaratif (EF-ATT-03) : une équipe inconnue n'est pas
    corrigée en douce, elle est signalée pour que l'analyste complète le schéma
    ou corrige l'application émettrice.
    """
    anomalies: list[str] = []
    if schema.valeurs_equipes and etiquettes.equipe not in (None, *schema.valeurs_equipes):
        anomalies.append(f"equipe={etiquettes.equipe}")
    if schema.valeurs_environnements and etiquettes.environnement not in (
        None,
        *schema.valeurs_environnements,
    ):
        anomalies.append(f"environnement={etiquettes.environnement}")
    return anomalies


@dataclass(frozen=True, slots=True)
class LigneAttribution:
    """Coût d'un appel, avec ce qu'il faut pour juger son attribution."""

    etiquettes: Etiquettes
    cout_usd: float
    etiquete: bool = True
    point_acces: PointAcces = PointAcces.RUNTIME
    hors_schema: bool = False


@dataclass(frozen=True, slots=True)
class PartNonAttribuee:
    """Part de dépense non attribuée et ventilation de ses causes (EF-ATT-07)."""

    depense_totale_usd: float = 0.0
    depense_non_attribuee_usd: float = 0.0
    part_pourcentage: float = 0.0
    causes: dict[str, float] = field(default_factory=dict)

    def libelles(self) -> list[str]:
        """Causes lisibles, avec leur montant, pour le portail et la PR."""
        return [f"{cause} : {montant:.2f} USD" for cause, montant in self.causes.items()]


@dataclass(slots=True)
class CumulNonAttribution:
    """Cumul incrémental de la dépense et de ses causes (mémoire constante).

    Permet de chiffrer la non-attribution pendant qu'on lit un flux de journaux
    (ENF-PERF-01) sans garder une ligne par appel en mémoire.
    """

    depense_totale_usd: float = 0.0
    depense_non_attribuee_usd: float = 0.0
    causes: dict[str, float] = field(default_factory=dict)

    def ajouter(self, ligne: LigneAttribution) -> None:
        """Intègre le coût d'un appel et ses causes."""
        self.depense_totale_usd += ligne.cout_usd
        for cause in _causes_de_ligne(ligne):
            self.causes[cause] = self.causes.get(cause, 0.0) + ligne.cout_usd
        if not ligne.etiquete or ligne.hors_schema or not ligne.point_acces.journalise:
            self.depense_non_attribuee_usd += ligne.cout_usd

    def bilan(self, depense_non_journalisee_usd: float = 0.0) -> PartNonAttribuee:
        """Fige le bilan, en y ajoutant la dépense non journalisée connue."""
        total = self.depense_totale_usd
        non_attribue = self.depense_non_attribuee_usd
        causes = dict(self.causes)
        if depense_non_journalisee_usd:
            total += depense_non_journalisee_usd
            non_attribue += depense_non_journalisee_usd
            causes[CAUSE_POINT_ACCES_NON_JOURNALISE] = (
                causes.get(CAUSE_POINT_ACCES_NON_JOURNALISE, 0.0) + depense_non_journalisee_usd
            )
        part = (non_attribue / total * 100.0) if total > 0 else 0.0
        return PartNonAttribuee(
            depense_totale_usd=round(total, 4),
            depense_non_attribuee_usd=round(non_attribue, 4),
            part_pourcentage=round(min(part, 100.0), 2),
            causes={cause: round(montant, 4) for cause, montant in causes.items()},
        )


def calculer_part_non_attribuee(
    lignes: Iterable[LigneAttribution],
    depense_non_journalisee_usd: float = 0.0,
) -> PartNonAttribuee:
    """Cumule la dépense connue, la dépense non attribuée et ses causes.

    Trois causes possibles : appel sans étiquette d'application, application
    hors schéma, et dépense d'un point d'accès non journalisé. Cette dernière
    n'existe pas dans les journaux — elle est fournie par l'appelant à partir de
    l'écart de facture (`reconcile.py`) et ajoutée à la dépense connue pour que
    la part ne soit pas artificiellement sous-estimée.
    """
    cumul = CumulNonAttribution()
    for ligne in lignes:
        cumul.ajouter(ligne)
    return cumul.bilan(depense_non_journalisee_usd)


def _causes_de_ligne(ligne: LigneAttribution) -> list[str]:
    """Causes de non-attribution imputables à un appel donné."""
    causes: list[str] = []
    if not ligne.etiquete:
        causes.append(CAUSE_SANS_ETIQUETTE)
    if ligne.hors_schema:
        causes.append(CAUSE_APPLICATION_HORS_SCHEMA)
    if not ligne.point_acces.journalise:
        causes.append(CAUSE_POINT_ACCES_NON_JOURNALISE)
    return causes
