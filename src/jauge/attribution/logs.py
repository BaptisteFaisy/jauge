"""Lecture des journaux d'invocation Amazon Bedrock (§3.2, §8.2, EF-ATT-05).

Exigences servies : EF-ATT-02 (les journaux d'invocation arrivent dans le compte
du client), EF-ATT-05 (les jetons et le niveau de service viennent d'ici) et
ENF-PERF-01 (un million d'enregistrements par jour traités en flux).

**Hypothèse structurante — les noms de champs ne sont pas confirmés** (§23 Q2).
Le module est donc écrit pour être *tolérant* :

- chaque champ canonique accepte plusieurs orthographes, listées dans
  :data:`CHAMPS_ACCEPTES` ; la comparaison se fait sur une forme normalisée
  (`inputTokenCount` == `input_token_count` == `Input-Token-Count`) ;
- les objets imbriqués (`usage.inputTokens`, `input.inputTokenCount`,
  `requestMetadata.tags`) sont aplatis avant recherche : un enregistrement plat
  et un enregistrement imbriqué se lisent pareil ;
- tout champ inconnu est **ignoré**, mais compté dans
  :attr:`RapportLecture.champs_inconnus` — c'est le signal qui dira, quand §23 Q2
  sera tranchée, quels champs méritent d'entrer dans la table ;
- un enregistrement inexploitable (JSON invalide, modèle absent, horodatage
  absent, jetons illisibles) est **signalé** dans :attr:`RapportLecture.rejetes`
  et sauté : un journal corrompu ne doit jamais faire tomber l'attribution du
  jour.

Deux distinctions de la spécification sont portées explicitement :

- le **point d'accès** (§3.2) : seuls les appels `bedrock-runtime` (`Converse`,
  `ConverseStream`, `InvokeModel`, `InvokeModelWithResponseStream`) sont
  journalisés ; un appel `bedrock-mantle` ne l'est pas, donc son coût manquera au
  recalcul et devra être expliqué au rapprochement (EF-ATT-07) ;
- le **niveau de service** (Reserved / Priority / Standard / Flex), qui change le
  tarif appliqué (EF-ATT-04, Annexe D).

Confidentialité (ENF-CONF-01) : le texte des requêtes et des réponses n'est
jamais lu ni conservé. Seuls les compteurs, l'identité et les étiquettes sortent.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from jauge.attribution.champs import (
    aplatir,
    booleen,
    entier,
    normaliser_cle,
    texte,
    valeur,
    variantes,
)
from jauge.attribution.tags import Etiquettes, PointAcces, extraire_etiquettes
from jauge.attribution.valeurs import (
    STATUTS_SUCCES,
    determiner_niveau_service,
    determiner_point_acces,
    lire_horodatage,
)
from jauge.common.ids import deterministic
from jauge.models.pricing import MetriquesRequete, ServiceTier

#: Orthographes acceptées pour chaque champ canonique d'un enregistrement.
#:
#: C'est **le** seul endroit à modifier quand §23 Q2 sera tranchée. Les noms sont
#: comparés sous forme normalisée (`normaliser_cle`), donc la casse et les
#: séparateurs (`_`, `-`, `.`) sont sans importance ; les variantes préfixées
#: (`usage.inputTokens`) couvrent les enregistrements imbriqués.
CHAMPS_ACCEPTES: dict[str, tuple[str, ...]] = {
    "request_id": (
        "requestId",
        "request_id",
        "invocationId",
        "invocation_id",
        "traceId",
        "x-amzn-requestid",
        "id",
    ),
    "horodatage": (
        "timestamp",
        "horodatage",
        "eventTime",
        "event_time",
        "@timestamp",
        "ingestionTime",
        "time",
        "date",
    ),
    "model_id": (
        "modelId",
        "model_id",
        "model",
        "modelIdentifier",
        "modelArn",
        "inferenceProfileId",
    ),
    "jetons_entree": (
        "inputTokenCount",
        "inputTokens",
        "jetons_entree",
        "promptTokenCount",
        "usage.inputTokens",
        "input.inputTokenCount",
        "usage.promptTokens",
    ),
    "jetons_sortie": (
        "outputTokenCount",
        "outputTokens",
        "jetons_sortie",
        "completionTokenCount",
        "generatedTokenCount",
        "usage.outputTokens",
        "output.outputTokenCount",
    ),
    "jetons_cache_lecture": (
        "cacheReadInputTokenCount",
        "cacheReadInputTokens",
        "cache_read_input_tokens",
        "usage.cacheReadInputTokens",
        "cacheDetails.readInputTokens",
    ),
    "jetons_cache_ecriture": (
        "cacheWriteInputTokenCount",
        "cacheWriteInputTokens",
        "cache_write_input_tokens",
        "usage.cacheWriteInputTokens",
        "cacheDetails.writeInputTokens",
    ),
    "niveau_service": (
        "serviceTier",
        "service_tier",
        "serviceTierName",
        "usage.serviceTier",
        "tier",
        "serviceLevel",
    ),
    "latence": (
        "latencyMs",
        "latency",
        "durationMs",
        "duration",
        "latency_ms",
        "totalLatencyMs",
        "invocationLatency",
        "firstByteLatency",
    ),
    "statut": ("status", "statut", "outcome", "result", "responseStatus", "errorCode"),
    "point_acces": (
        "accessPoint",
        "access_point",
        "point_acces",
        "endpoint",
        "apiEndpoint",
        "operation",
        "api",
    ),
    "etiquettes": (
        "requestMetadata",
        "request_metadata",
        "metadonnees",
        "metadata",
        "tags",
        "labels",
        "customMetadata",
    ),
    "sensible": ("sensitive", "sensible", "containsPii", "pii", "sensitiveData", "redacted"),
}

#: Noms normalisés acceptés, indexés par champ canonique (calculé une fois).
_VARIANTES: dict[str, tuple[str, ...]] = {
    champ: variantes(noms) for champ, noms in CHAMPS_ACCEPTES.items()
}

#: Clés structurelles d'un enregistrement : connues, donc pas « inconnues ».
_CLES_STRUCTURELLES = {
    normaliser_cle(cle)
    for cle in (
        "schemaType",
        "schemaVersion",
        "operation",
        "request",
        "response",
        "body",
        "input",
        "output",
        "usage",
        "cacheDetails",
        "promptTokensDetails",
        "completionTokensDetails",
        "metadata",
        "requestMetadata",
        "message",
        "detail",
        "record",
        "invocation",
        "accountId",
        "region",
        "identity",
    )
}

#: Clés connues (structurelles ou variantes) : calculé une fois pour toutes,
#: le contrôle de dérive tourne sur chaque enregistrement lu (ENF-PERF-01).
_CLES_CONNUES = _CLES_STRUCTURELLES | {
    variante for variantes in _VARIANTES.values() for variante in variantes
}


def _valeur(plat: Mapping[str, object], champ: str) -> object | None:
    """Valeur d'un champ canonique, cherchée sous ses variantes acceptées."""
    return valeur(plat, _VARIANTES[champ])


def _texte(plat: Mapping[str, object], champ: str) -> str | None:
    """Valeur textuelle non vide d'un champ canonique."""
    return texte(plat, _VARIANTES[champ])


def _entier(plat: Mapping[str, object], champ: str) -> int | None:
    """Valeur entière positive d'un champ canonique, ou `None` si illisible."""
    return entier(plat, _VARIANTES[champ])


def _booleen(plat: Mapping[str, object], champ: str) -> bool:
    """Drapeau d'un champ canonique (`sensible`, `pii`…), jamais d'exception."""
    return booleen(plat, _VARIANTES[champ])


@dataclass(slots=True)
class EnregistrementRejete:
    """Enregistrement inexploitable : signalé, jamais fatal (§23 Q2)."""

    index: int
    motif: str
    champs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RapportLecture:
    """Compteurs d'une lecture de journaux, pour l'observabilité (ENF-OBS-01)."""

    lus: int = 0
    exploitables: int = 0
    sensibles: int = 0
    rejetes: list[EnregistrementRejete] = field(default_factory=list)
    champs_inconnus: Counter[str] = field(default_factory=Counter)

    @property
    def nb_rejetes(self) -> int:
        """Nombre d'enregistrements écartés."""
        return len(self.rejetes)

    @property
    def motifs_rejet(self) -> dict[str, int]:
        """Motifs de rejet et leur fréquence, du plus fréquent au moins fréquent."""
        return dict(Counter(rejete.motif for rejete in self.rejetes).most_common())


@dataclass(frozen=True, slots=True)
class EnregistrementInvocation:
    """Un appel de modèle lu dans un journal d'invocation, en forme locale.

    Structure propre au module : elle reste dans le plan de données et n'est
    jamais renvoyée telle quelle par l'API du plan de contrôle (ENF-CONF-01).
    """

    request_id: str
    horodatage: datetime
    model_id: str
    metriques: MetriquesRequete
    niveau_service: ServiceTier = ServiceTier.STANDARD
    statut: str = "succes"
    etiquettes: Etiquettes = field(default_factory=Etiquettes)
    sensible: bool = False
    point_acces: PointAcces = PointAcces.RUNTIME

    @property
    def jour(self) -> date:
        """Jour UTC de l'appel (clé de partition des agrégats)."""
        return self.horodatage.astimezone(UTC).date()

    @property
    def jetons_entree(self) -> int:
        return self.metriques.jetons_entree

    @property
    def jetons_sortie(self) -> int:
        return self.metriques.jetons_sortie

    @property
    def jetons_cache_lecture(self) -> int:
        return self.metriques.jetons_cache_lecture

    @property
    def jetons_cache_ecriture(self) -> int:
        return self.metriques.jetons_cache_ecriture

    @property
    def latence_ms(self) -> int:
        return self.metriques.latence_ms

    @property
    def reussie(self) -> bool:
        """Vrai si l'appel est un succès.

        Statut inconnu = échec : un `throttlingException` ou un `internalError`
        ne ressemble pas à « succès ». On ne devine pas l'absence d'erreur, on
        exige un statut de succès explicite.
        """
        return self.statut in STATUTS_SUCCES


class EnregistrementInexploitable(Exception):
    """Interne : l'enregistrement ne peut pas être lu. Jamais propagée."""


def analyser_enregistrement(
    brut: object, rapport: RapportLecture | None = None
) -> EnregistrementInvocation:
    """Convertit un enregistrement brut (dict ou ligne JSON) en forme locale.

    Lève :class:`EnregistrementInexploitable` (motif en clair) si l'appel ne peut
    pas être daté, nommé ou chiffré : mieux vaut l'ignorer et le signaler que le
    facturer à un modèle inconnu.

    Si `rapport` est fourni, les clés de premier niveau qu'aucune variante ne
    couvre y sont comptées : c'est le signal qui dira, quand §23 Q2 sera
    tranchée, quels champs méritent d'entrer dans :data:`CHAMPS_ACCEPTES`. Le
    comptage se fait ici, sur l'enregistrement déjà décodé, pour ne pas analyser
    deux fois la même ligne JSON.
    """
    charge = _decompresser(_charger(brut))
    if rapport is not None:
        _compter_champs_inconnus(rapport, charge)
    plat = aplatir(charge)
    model_id = _texte(plat, "model_id")
    if not model_id:
        raise EnregistrementInexploitable("model_id absent")
    horodatage = lire_horodatage(_valeur(plat, "horodatage"))
    if horodatage is None:
        raise EnregistrementInexploitable("horodatage absent ou illisible")
    entree = _entier(plat, "jetons_entree")
    sortie = _entier(plat, "jetons_sortie")
    if entree is None and sortie is None:
        raise EnregistrementInexploitable("jetons illisibles")
    return EnregistrementInvocation(
        request_id=_texte(plat, "request_id")
        or deterministic(model_id, horodatage.isoformat(), entree, sortie),
        horodatage=horodatage,
        model_id=model_id,
        metriques=MetriquesRequete(
            jetons_entree=entree or 0,
            jetons_sortie=sortie or 0,
            jetons_cache_lecture=_entier(plat, "jetons_cache_lecture") or 0,
            jetons_cache_ecriture=_entier(plat, "jetons_cache_ecriture") or 0,
            latence_ms=_entier(plat, "latence") or 0,
        ),
        niveau_service=determiner_niveau_service(_valeur(plat, "niveau_service")),
        statut=(_texte(plat, "statut") or "succes").lower(),
        etiquettes=extraire_etiquettes(_metadonnees(charge)),
        sensible=_booleen(plat, "sensible"),
        point_acces=determiner_point_acces(_valeur(plat, "point_acces")),
    )


def iterer_enregistrements(
    source: Iterable[object], rapport: RapportLecture | None = None
) -> Iterator[EnregistrementInvocation]:
    """Parcourt des enregistrements bruts **en flux** (ENF-PERF-01).

    Rien n'est matérialisé : la source peut être un itérateur de lignes lues
    depuis S3. Les enregistrements inexploitables sont consignés dans `rapport`
    et sautés ; les champs inconnus y sont comptés.
    """
    bilan = rapport if rapport is not None else RapportLecture()
    for index, brut in enumerate(source):
        bilan.lus += 1
        try:
            enregistrement = analyser_enregistrement(brut, bilan)
        except EnregistrementInexploitable as motif:
            bilan.rejetes.append(
                EnregistrementRejete(index=index, motif=str(motif), champs=_cles(brut))
            )
            continue
        bilan.exploitables += 1
        if enregistrement.sensible:
            bilan.sensibles += 1
        yield enregistrement


def lire_journaux(
    chemin: Path | str, rapport: RapportLecture | None = None
) -> Iterator[EnregistrementInvocation]:
    """Lit un journal NDJSON sur disque, ligne à ligne, sans chargement intégral."""
    with Path(chemin).open(encoding="utf-8") as flux:
        yield from iterer_enregistrements(_lignes(flux), rapport)


def iterer_lignes(texte: Iterable[str]) -> Iterator[str]:
    """Filtre les lignes vides d'un flux texte (NDJSON)."""
    for ligne in texte:
        contenu = ligne.strip()
        if contenu:
            yield contenu


def iterer_rejouables(
    enregistrements: Iterable[EnregistrementInvocation],
) -> Iterator[EnregistrementInvocation]:
    """Écarte les requêtes marquées sensibles par le client (§13.1).

    Contrat offert au module de rejeu (M4) : une requête sensible ne sort jamais
    du plan de données pour être rejouée ni notée.
    """
    for enregistrement in enregistrements:
        if not enregistrement.sensible:
            yield enregistrement


def _decompresser(charge: dict[str, Any]) -> dict[str, Any]:
    """Remplace les valeurs JSON en chaîne par leur objet décompressé.

    Certains journaux d'invocation transportent les métadonnées de requête et
    les métriques d'invocation sous forme de **chaîne JSON** (colonne texte
    d'un export CSV, ou sérialisation du collecteur). Le parseur les accepte :
    la clé devient un objet et l'aplatissement fait le reste.
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


def _charger(brut: object) -> dict[str, Any]:
    """Décode un enregistrement brut, ou lève `EnregistrementInexploitable`."""
    if isinstance(brut, dict):
        return _deballer(brut)
    if isinstance(brut, bytes):
        brut = brut.decode("utf-8", errors="replace")
    if isinstance(brut, str):
        try:
            charge = json.loads(brut)
        except json.JSONDecodeError as erreur:
            raise EnregistrementInexploitable(f"JSON invalide ({erreur.msg})") from erreur
        if not isinstance(charge, dict):
            raise EnregistrementInexploitable("enregistrement JSON non objet")
        return _deballer(charge)
    raise EnregistrementInexploitable(f"type d'enregistrement inattendu ({type(brut).__name__})")


def _deballer(charge: dict[str, Any]) -> dict[str, Any]:
    """Descend dans une enveloppe (`message`, `detail`, `record`, `invocation`)."""
    for enveloppe in ("message", "detail", "record", "invocation"):
        interieur = charge.get(enveloppe)
        if isinstance(interieur, dict):
            return interieur
    return charge


def _lignes(flux: Iterable[str]) -> Iterator[str]:
    """Lignes non vides d'un flux texte."""
    yield from iterer_lignes(flux)


def _metadonnees(charge: dict[str, Any]) -> dict[str, Any]:
    """Métadonnées de requête brutes, cherchées sous leurs noms acceptés."""
    for nom in _VARIANTES["etiquettes"]:
        for cle, contenu in charge.items():
            if normaliser_cle(cle) == nom and isinstance(contenu, dict):
                return contenu
    return {}


def _cles(brut: object) -> list[str]:
    """Clés de premier niveau d'un enregistrement brut (pour le diagnostic)."""
    charge: object = brut
    if isinstance(brut, str):
        try:
            charge = json.loads(brut)
        except json.JSONDecodeError:
            return []
    if isinstance(charge, dict):
        return [str(cle) for cle in charge]
    return []


def _compter_champs_inconnus(rapport: RapportLecture, charge: dict[str, Any]) -> None:
    """Compte les clés de premier niveau qu'aucune variante ne couvre."""
    for cle in charge:
        if normaliser_cle(cle) not in _CLES_CONNUES:
            rapport.champs_inconnus[str(cle)] += 1
