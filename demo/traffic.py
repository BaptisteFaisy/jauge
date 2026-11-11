"""Générateur de trafic synthétique **déterministe** (§18.1).

Même graine ⇒ même trafic, à l'identique. Le générateur produit des
**journaux d'invocation de modèle Bedrock** synthétiques, au format des
journaux CloudWatch/S3 de Bedrock (« model invocation logs »).

FORMAT DES ENREGISTREMENTS (à faire adopter tel quel par le module M1,
`src/jauge/attribution/logs.py`)

Une ligne JSON par invocation :

    {
      "requestId": "<uuid v5 déterministe>",
      "eventTime": "<ISO-8601 UTC, ex. 2026-11-03T08:12:33Z>",
      "input": "<corps JSON de la requête Converse, sous forme de chaîne>",
      "output": "<corps JSON de la réponse Converse, ou null si erreur>",
      "inputTokenCount": 1533,
      "outputTokenCount": 612,
      "totalTokenCount": 2145,
      "amazon-bedrock-invocationMetrics": "<métriques d'invocation, chaîne JSON>",
      "requestMetadata": "<étiquettes d'attribution, chaîne JSON>",
      "errorCode": null,
      "errorMessage": null
    }

`input` (requête `Converse`) :

    {"messages": [{"role": "user", "content": [{"text": "..."}]}],
     "system": [{"text": "..."}],
     "inferenceConfig": {"maxTokens": 256, "temperature": 0.0, "topP": 1.0}}

`output` (réponse `Converse`) :

    {"output": {"message": {"role": "assistant", "content": [{"text": "..."}]}},
     "stopReason": "end_turn",
     "usage": {"inputTokens": 1533, "outputTokens": 612, "totalTokens": 2145}}

`amazon-bedrock-invocationMetrics` :

    {"inputTokenCount": 1533, "outputTokenCount": 612,
     "cacheReadInputTokenCount": 0, "cacheWriteInputTokenCount": 0,
     "invocationLatency": 2431, "firstByteLatency": 412,
     "serviceTier": "Standard"}

`requestMetadata` (schéma d'attribution §8.1, EF-ATT-03) :

    {"application": "support-assistant", "team": "support",
     "environment": "production", "typeRequete": "question"}

Conventions retenues :

- `inputTokenCount` compte les jetons d'entrée **hors cache** ; les jetons de
  cache figurent séparément dans `cacheReadInputTokenCount` et
  `cacheWriteInputTokenCount` ;
- `totalTokenCount` = `inputTokenCount` + `outputTokenCount` ;
- `invocationLatency` et `firstByteLatency` sont en millisecondes ;
- `serviceTier` vaut `Standard`, `Flex` ou `Priority` ;
- une invocation en erreur porte `output: null`, `errorCode` et
  `errorMessage`, et zéro jeton.

Ce que le trafic contient volontairement (§5.1, Annexe A) : un préfixe long
répété sans jeton de cache lu, des sorties anormalement longues chez une
application, des requêtes identiques répétées, des erreurs suivies de relances,
un classement sur un modèle surdimensionné, un traitement nocturne resté au
niveau Standard.

Aucune donnée réelle : tous les textes sont fabriqués par `demo.apps`.
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from demo.apps import (
    DocSummarizer,
    FakeModeleClient,
    KbSearch,
    ModeleClient,
    RequeteModele,
    SupportAssistant,
    TicketClassifier,
)
from demo.apps.doc_summarizer import PARAGRAPHES_EXEMPLES
from demo.apps.kb_search import QUESTIONS_EXEMPLES as QUESTIONS_RECHERCHE
from demo.apps.support_assistant import QUESTIONS_EXEMPLES
from demo.apps.ticket_classifier import TICKETS_EXEMPLES
from demo.profils import (
    DATE_FIN_DEFAUT,
    GRAINE_DEFAUT,
    JOURS_DEFAUT,
    PROFILS,
    ProfilApplication,
)
from jauge.common.ids import graine_de
from jauge.models.pricing import MetriquesRequete, ServiceTier

FUSEAU = ZoneInfo("Europe/Paris")
NIVEAUX = {
    ServiceTier.STANDARD: "Standard",
    ServiceTier.FLEX: "Flex",
    ServiceTier.PRIORITY: "Priority",
    ServiceTier.RESERVED: "Reserved",
}


@dataclass(frozen=True)
class JournalInvocation:
    """Un enregistrement de journal d'invocation Bedrock (voir module)."""

    request_id: str
    event_time: datetime
    modele_id: str
    niveau: ServiceTier
    tenant_id: str
    application_id: str
    equipe: str
    environnement: str
    type_requete: str
    corps_entree: str
    corps_sortie: str | None
    jetons_entree: int
    jetons_sortie: int
    jetons_cache_lecture: int
    jetons_cache_ecriture: int
    latence_invocation_ms: int
    latence_premier_octet_ms: int
    code_erreur: str | None = None
    message_erreur: str | None = None

    def metriques(self) -> MetriquesRequete:
        """Métriques de coût de la requête (modèle de prix du socle)."""
        return MetriquesRequete(
            jetons_entree=self.jetons_entree,
            jetons_sortie=self.jetons_sortie,
            jetons_cache_ecriture=self.jetons_cache_ecriture,
            jetons_cache_lecture=self.jetons_cache_lecture,
            latence_ms=self.latence_invocation_ms,
        )

    def signature_requete(self) -> str:
        """Empreinte du corps de requête : sert à repérer les doublons."""
        return str(uuid.uuid5(uuid.NAMESPACE_OID, self.corps_entree))

    def vers_ligne_cloudwatch(self) -> dict[str, Any]:
        """Ligne JSON du journal d'invocation, au format Bedrock/CloudWatch."""
        metriques = {
            "inputTokenCount": self.jetons_entree,
            "outputTokenCount": self.jetons_sortie,
            "cacheReadInputTokenCount": self.jetons_cache_lecture,
            "cacheWriteInputTokenCount": self.jetons_cache_ecriture,
            "invocationLatency": self.latence_invocation_ms,
            "firstByteLatency": self.latence_premier_octet_ms,
            "serviceTier": NIVEAUX[self.niveau],
        }
        etiquettes = {
            "application": self.application_id,
            "team": self.equipe,
            "environment": self.environnement,
            "typeRequete": self.type_requete,
        }
        return {
            "requestId": self.request_id,
            "eventTime": self.event_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "input": self.corps_entree,
            "output": self.corps_sortie,
            "inputTokenCount": self.jetons_entree,
            "outputTokenCount": self.jetons_sortie,
            "totalTokenCount": self.jetons_entree + self.jetons_sortie,
            "amazon-bedrock-invocationMetrics": json.dumps(metriques, ensure_ascii=False),
            "requestMetadata": json.dumps(etiquettes, ensure_ascii=False),
            "errorCode": self.code_erreur,
            "errorMessage": self.message_erreur,
        }


@dataclass(frozen=True)
class _Appel:
    """Appel planifié : requête + horodatage (interne au générateur)."""

    requete: RequeteModele
    moment: datetime


def generer_trafic(
    graine: int = GRAINE_DEFAUT,
    jours: int = JOURS_DEFAUT,
    date_fin: date = DATE_FIN_DEFAUT,
    profils: dict[str, ProfilApplication] | None = None,
) -> list[JournalInvocation]:
    """Génère le trafic synthétique de la période demandée.

    Le résultat est trié par horodatage ; deux appels avec la même graine et
    les mêmes paramètres donnent exactement la même liste.
    """
    catalogue = profils if profils is not None else PROFILS
    records: list[JournalInvocation] = []
    for profil in catalogue.values():
        records.extend(_trafic_application(profil, graine, jours, date_fin))
    records.sort(key=lambda r: (r.event_time, r.request_id))
    return records


def lignes_cloudwatch(records: list[JournalInvocation]) -> list[dict[str, Any]]:
    """Convertit les enregistrements en lignes JSON de journal d'invocation."""
    return [r.vers_ligne_cloudwatch() for r in records]


# --- Interne : planification d'une application --------------------------------


def _trafic_application(
    profil: ProfilApplication, graine: int, jours: int, date_fin: date
) -> list[JournalInvocation]:
    """Trafic d'une application sur la période demandée."""
    rnd = random.Random(graine_de(graine, "trafic", profil.application_id))
    client = FakeModeleClient()
    app = _application_demo(profil, client)
    records: list[JournalInvocation] = []
    for index in range(jours):
        jour = date_fin - timedelta(days=jours - 1 - index)
        records.extend(_journee(app, profil, rnd, jour, client))
    return records


def _application_demo(profil: ProfilApplication, client: ModeleClient):
    """Instancie l'application cliente correspondant au profil."""
    commun = (client, profil.modele_id, profil.application_id)
    if profil.application_id == "ticket-classifier":
        return TicketClassifier(*commun, profil.equipe, profil.environnement, profil.max_tokens)
    if profil.application_id == "doc-summarizer":
        return DocSummarizer(*commun, profil.equipe, profil.environnement, profil.max_tokens)
    if profil.application_id == "kb-search":
        return KbSearch(*commun, profil.equipe, profil.environnement, profil.max_tokens)
    return SupportAssistant(*commun, profil.equipe, profil.environnement, profil.max_tokens)


def _journee(
    app, profil: ProfilApplication, rnd: random.Random, jour: date, client: ModeleClient
) -> list[JournalInvocation]:
    """Enregistrements d'une journée pour une application."""
    appels = _appels_du_jour(app, profil, rnd, jour)
    return _enregistrements(app, profil, rnd, appels, client)


def _appels_du_jour(app, profil: ProfilApplication, rnd: random.Random, jour: date) -> list[_Appel]:
    """Planifie les appels de la journée (horaires, sujets, doublons)."""
    nombre = _nombre_requetes(profil, rnd, jour)
    if nombre <= 0:
        return []
    appels: list[_Appel] = []
    for heure, quantite in enumerate(_repartition(profil, rnd, nombre)):
        for _ in range(quantite):
            appels.append(
                _Appel(requete=_requete(app, profil, rnd), moment=_moment(jour, heure, rnd))
            )
    return _appliquer_doublons(app, profil, rnd, appels)


def _nombre_requetes(profil: ProfilApplication, rnd: random.Random, jour: date) -> int:
    """Volume du jour : week-end atténué, légère variation aléatoire."""
    facteur = 1.0 + rnd.uniform(-0.12, 0.12)
    if jour.weekday() >= 5:
        facteur *= profil.facteur_weekend
    return max(0, round(profil.requetes_par_jour * facteur))


def _repartition(profil: ProfilApplication, rnd: random.Random, nombre: int) -> list[int]:
    """Répartit le volume du jour sur les 24 heures, selon le profil."""
    poids = profil.poids_horaires
    total = profil.total_poids
    if total == 0 or nombre <= 0:
        return [0] * 24
    quotients = [nombre * poids[i] / total for i in range(24)]
    base = [int(q) for q in quotients]
    reste = nombre - sum(base)
    candidats = [i for i in range(24) if poids[i] > 0]
    for _ in range(reste):
        cible = max(candidats, key=lambda j: (quotients[j] - base[j], poids[j]))
        base[cible] += 1
    return base


def _moment(jour: date, heure: int, rnd: random.Random) -> datetime:
    """Horodatage local (Europe/Paris) d'un appel, dans l'heure demandée."""
    return datetime(
        jour.year,
        jour.month,
        jour.day,
        heure,
        rnd.randrange(60),
        rnd.randrange(60),
        tzinfo=FUSEAU,
    )


def _requete(app, profil: ProfilApplication, rnd: random.Random) -> RequeteModele:
    """Construit une requête dont la taille d'entrée suit le profil."""
    sujet = _sujet(profil, rnd)
    cible = rnd.randint(*profil.jetons_entree)
    requete = app.construire_requete(sujet, contexte_jetons=0, rnd=rnd)
    base = _jetons_requete(requete)
    return app.construire_requete(sujet, contexte_jetons=max(0, cible - base), rnd=rnd)


def _sujet(profil: ProfilApplication, rnd: random.Random) -> str:
    """Texte court adressé au modèle, propre à l'application."""
    if profil.application_id == "ticket-classifier":
        base = TICKETS_EXEMPLES[rnd.randrange(len(TICKETS_EXEMPLES))]
    elif profil.application_id == "doc-summarizer":
        base = PARAGRAPHES_EXEMPLES[rnd.randrange(len(PARAGRAPHES_EXEMPLES))]
    elif profil.application_id == "kb-search":
        base = QUESTIONS_RECHERCHE[rnd.randrange(len(QUESTIONS_RECHERCHE))]
    else:
        base = QUESTIONS_EXEMPLES[rnd.randrange(len(QUESTIONS_EXEMPLES))]
    return f"{base} (dossier {rnd.randrange(1, 999):03d})"


def _jetons_requete(requete: RequeteModele) -> int:
    """Nombre de jetons estimés du contenu transmis (système + messages)."""
    morceaux = list(requete.systeme) + [m.texte for m in requete.messages]
    return max(1, sum(max(1, len(m) // 4) for m in morceaux))


def _appliquer_doublons(
    app, profil: ProfilApplication, rnd: random.Random, appels: list[_Appel]
) -> list[_Appel]:
    """Répète des requêtes déjà vues : signal du levier « requêtes répétées »."""
    if profil.part_requetes_identiques <= 0:
        return appels
    memoire: list[RequeteModele] = []
    resultat: list[_Appel] = []
    for appel in appels:
        if memoire and rnd.random() < profil.part_requetes_identiques:
            tiree = memoire[rnd.randrange(len(memoire))]
            resultat.append(_Appel(requete=tiree, moment=appel.moment))
        else:
            resultat.append(appel)
            memoire.append(appel.requete)
    return resultat


# --- Interne : production des enregistrements ---------------------------------


def _enregistrements(
    app,
    profil: ProfilApplication,
    rnd: random.Random,
    appels: list[_Appel],
    client: ModeleClient,
) -> list[JournalInvocation]:
    """Transforme les appels planifiés en enregistrements de journal."""
    records: list[JournalInvocation] = []
    for appel in appels:
        if rnd.random() < profil.part_erreurs:
            records.append(_enregistrement(appel, profil, rnd, client, erreur=True))
            relance = _Appel(
                requete=appel.requete,
                moment=appel.moment + timedelta(seconds=rnd.randint(1, 45)),
            )
            records.append(_enregistrement(relance, profil, rnd, client, erreur=False))
        else:
            records.append(_enregistrement(appel, profil, rnd, client, erreur=False))
    return records


def _enregistrement(
    appel: _Appel,
    profil: ProfilApplication,
    rnd: random.Random,
    client: ModeleClient,
    erreur: bool,
) -> JournalInvocation:
    """Construit un enregistrement à partir d'un appel et de sa réponse."""
    requete = appel.requete
    reponse = client.converse(requete)
    corps_entree = _corps_entree(requete)
    identifiant = _request_id(requete, appel.moment)
    commun = {
        "request_id": identifiant,
        "event_time": appel.moment,
        "modele_id": profil.modele_id,
        "niveau": profil.niveau,
        "tenant_id": profil.tenant_id,
        "application_id": profil.application_id,
        "equipe": profil.equipe,
        "environnement": profil.environnement,
        "type_requete": profil.type_requete,
        "corps_entree": corps_entree,
    }
    if erreur:
        return JournalInvocation(
            **commun,
            corps_sortie=None,
            jetons_entree=0,
            jetons_sortie=0,
            jetons_cache_lecture=0,
            jetons_cache_ecriture=0,
            latence_invocation_ms=0,
            latence_premier_octet_ms=0,
            code_erreur="throttlingException",
            message_erreur="Le service a refusé l'appel pour cause de limitation de débit.",
        )
    return JournalInvocation(
        **commun,
        corps_sortie=_corps_sortie(reponse),
        jetons_entree=reponse.jetons_entree,
        jetons_sortie=reponse.jetons_sortie,
        jetons_cache_lecture=reponse.jetons_cache_lecture,
        jetons_cache_ecriture=reponse.jetons_cache_ecriture,
        latence_invocation_ms=reponse.latence_ms,
        latence_premier_octet_ms=max(1, reponse.latence_ms // 5),
    )


def _request_id(requete: RequeteModele, moment: datetime) -> str:
    """Identifiant d'invocation déterministe (UUID v5)."""
    clef = (
        f"{requete.metadonnees.application_id}|{moment.isoformat()}|"
        f"{requete.messages[0].texte[:80]}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_OID, clef))


def _corps_entree(requete: RequeteModele) -> str:
    """Corps JSON de la requête `Converse`."""
    corps = {
        "messages": [{"role": m.role, "content": [{"text": m.texte}]} for m in requete.messages],
        "system": [{"text": s} for s in requete.systeme],
        "inferenceConfig": {
            "maxTokens": requete.parametres.max_tokens,
            "temperature": requete.parametres.temperature,
            "topP": requete.parametres.top_p,
        },
    }
    return json.dumps(corps, ensure_ascii=False, sort_keys=True)


def _corps_sortie(reponse) -> str:
    """Corps JSON de la réponse `Converse`."""
    corps = {
        "output": {
            "message": {"role": "assistant", "content": [{"text": reponse.texte}]},
        },
        "stopReason": reponse.stop_reason,
        "usage": {
            "inputTokens": reponse.jetons_entree,
            "outputTokens": reponse.jetons_sortie,
            "totalTokens": reponse.jetons_entree + reponse.jetons_sortie,
        },
    }
    return json.dumps(corps, ensure_ascii=False, sort_keys=True)
