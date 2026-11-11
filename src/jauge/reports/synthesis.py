"""Synthèse rédigée du rapport mensuel, strictement isolée des chiffres.

EF-RAP-03 demande une synthèse rédigée par un modèle ; ENF-IA-01 interdit que
le modèle calcule un chiffre. Ce module résout la tension par un port :

- `RedacteurModele` est le port : il reçoit un `ContexteSynthese` — des chiffres
  **déjà** calculés par `jauge.reports.builder` et des phrases factuelles
  construites par le code — et ne rend qu'un texte.
- `RedacteurDeterministe` est l'implémentation de démonstration : elle assemble
  les phrases factuelles, sans réseau, et sert aussi de repli.
- `RedacteurBedrock` (boto3) et `RedacteurStrands` (agent « Rédacteur », §12.1)
  sont les implémentations réelles. Elles ne sont jamais appelées par les tests.

Deux garde-fous rendent la règle vérifiable :

1. `appliquer_synthese` recalcule l'empreinte de tous les champs chiffrés avant
   et après l'ajout du texte ; une différence lève une erreur (impossible par
   construction, donc testable comme invariant).
2. Tout nombre présent dans le texte rendu par le modèle doit figurer parmi les
   nombres fournis. Un modèle qui invente un pourcentage est rejeté et le repli
   déterministe s'applique (`degradee=True`).
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from pydantic import BaseModel, Field

from jauge.common.errors import ServiceIndisponibleError, ValidationError
from jauge.reports.schema import RapportMensuel, SyntheseRapport, TendanceMensuelle

MOTIF_NOMBRE = re.compile(r"\d+(?:\.\d+)?")
SOURCE_DETERMINISTE = "deterministe"
SOURCE_MODELE = "modele"


class ContexteSynthese(BaseModel):
    """Tout ce qu'un rédacteur a le droit de voir : des chiffres et des faits.

    Aucun texte de requête ni de réponse n'entre ici (ENF-CONF-01), aucun
    identifiant de client final non plus : le contexte est destiné à un modèle.
    """

    tenant_nom: str
    periode: str
    devise: str
    chiffres: dict[str, float] = Field(default_factory=dict)
    faits: list[str] = Field(default_factory=list)


class RedacteurModele(Protocol):
    """Port du rédacteur : il ne rend que du texte, jamais un chiffre."""

    @property
    def nom(self) -> str:
        """Identifiant du rédacteur, recopié dans la provenance du rapport."""
        ...

    def rediger(self, contexte: ContexteSynthese) -> str:
        """Rédige la synthèse à partir des seuls chiffres fournis."""
        ...


def _fmt(valeur: float) -> str:
    """Formate un chiffre sans séparateur de milliers (analysable tel quel)."""
    return f"{valeur:.2f}"


def _pluriel(nombre: int, singulier: str, pluriel: str) -> str:
    """Accorde un nom avec son nombre (« 1 lever », « 3 levers »)."""
    return f"{nombre} {singulier if nombre == 1 else pluriel}"


def _evolution(tendance: TendanceMensuelle) -> str:
    """Formule l'évolution de la dépense sans jamais changer le chiffre."""
    pct = _fmt(abs(tendance.variation_pourcentage))
    if tendance.sens == "stable":
        return f"was broadly flat ({pct} percent)"
    if tendance.sens == "baisse":
        return f"fell by {pct} percent"
    return f"rose by {pct} percent"


def _faits(rapport: RapportMensuel) -> list[str]:
    """Phrases factuelles en anglais, dérivées une à une des chiffres du rapport."""
    tendance = rapport.tendance
    ecart = rapport.ecart_facture
    economies = rapport.economies
    faits = [
        f"Total Bedrock spend for the period was {_fmt(rapport.cout_total_usd)} USD "
        f"across {rapport.nombre_requetes} requests.",
        f"Spend {_evolution(tendance)} compared with the previous month "
        f"({_fmt(tendance.cout_mois_precedent_usd)} USD).",
        f"Attribution coverage reached {_fmt(rapport.couverture_attribution_pourcentage)} "
        "percent of the spend.",
    ]
    if ecart.disponible:
        faits.append(
            f"Recomputed cost differs from the billed amount by "
            f"{_fmt(ecart.ecart_pourcentage)} percent ({_fmt(ecart.cout_facture_usd)} USD billed)."
        )
    else:
        faits.append("No bill reconciliation was available for this period.")
    if rapport.leviers:
        faits.append(
            f"{_pluriel(len(rapport.leviers), 'savings lever is', 'savings levers are')} open, "
            f"worth an estimated {_fmt(economies.restantes_usd)} USD per month."
        )
    else:
        faits.append("No open savings lever was detected in this period.")
    faits.append(
        f"Verified savings on the invoice amount to {_fmt(economies.constatees_usd)} USD, "
        f"against {_fmt(economies.attendues_usd)} USD expected from approved recommendations."
    )
    if rapport.recommandations_en_attente:
        faits.append(
            f"{_pluriel(len(rapport.recommandations_en_attente), 'recommendation is', 'recommendations are')} "
            "awaiting the application owner's decision."
        )
    else:
        faits.append("No recommendation is awaiting approval.")
    actives = sum(1 for bascule in rapport.bascules if bascule.actif)
    faits.append(
        f"{_pluriel(actives, 'staged rollout is', 'staged rollouts are')} currently in progress."
    )
    return faits


def contexte_depuis_rapport(rapport: RapportMensuel) -> ContexteSynthese:
    """Construit le contexte du rédacteur à partir d'un rapport déjà chiffré."""
    return ContexteSynthese(
        tenant_nom=rapport.tenant_nom,
        periode=f"{rapport.annee}-{rapport.mois:02d}",
        devise=rapport.devise,
        chiffres={
            "cout_total_usd": rapport.cout_total_usd,
            "nombre_requetes": float(rapport.nombre_requetes),
            "couverture_attribution_pourcentage": rapport.couverture_attribution_pourcentage,
            "variation_pourcentage": rapport.tendance.variation_pourcentage,
            "cout_mois_precedent_usd": rapport.tendance.cout_mois_precedent_usd,
            "ecart_facture_pourcentage": rapport.ecart_facture.ecart_pourcentage,
            "cout_facture_usd": rapport.ecart_facture.cout_facture_usd,
            "economies_attendues_usd": rapport.economies.attendues_usd,
            "economies_constatees_usd": rapport.economies.constatees_usd,
            "economies_restantes_usd": rapport.economies.restantes_usd,
            "leviers_detectes": float(len(rapport.leviers)),
            "recommandations_en_attente": float(len(rapport.recommandations_en_attente)),
        },
        faits=_faits(rapport),
    )


def nombres_autorises(contexte: ContexteSynthese) -> set[str]:
    """Nombres qu'un texte de synthèse a le droit de citer, et aucun autre."""
    autorises = {_fmt(valeur) for valeur in contexte.chiffres.values()}
    autorises |= set(MOTIF_NOMBRE.findall(" ".join(contexte.faits)))
    # Le client et la période font partie du contexte fourni : leurs chiffres
    # (année, mois) peuvent être cités sans être des chiffres « inventés ».
    autorises |= set(MOTIF_NOMBRE.findall(contexte.periode))
    autorises |= set(MOTIF_NOMBRE.findall(contexte.tenant_nom))
    return autorises


def nombres_cites(texte: str) -> set[str]:
    """Nombres présents dans un texte, sous la forme normalisée de `_fmt`."""
    return set(MOTIF_NOMBRE.findall(texte))


def texte_conforme(texte: str, contexte: ContexteSynthese) -> bool:
    """Vrai si le texte n'introduit aucun nombre absent du contexte."""
    return nombres_cites(texte) <= nombres_autorises(contexte)


class RedacteurDeterministe:
    """Rédacteur de démonstration : assemble les faits, sans aucun appel réseau."""

    @property
    def nom(self) -> str:
        return SOURCE_DETERMINISTE

    def rediger(self, contexte: ContexteSynthese) -> str:
        """Texte déterministe : les faits, dans l'ordre, sans ajout."""
        entete = f"Monthly cost report for {contexte.tenant_nom} — {contexte.periode}."
        return " ".join([entete, *contexte.faits])


def _invite(contexte: ContexteSynthese) -> str:
    """Invite envoyée au modèle : elle ne contient que des chiffres déjà fixés."""
    return (
        "You write the narrative summary of a monthly FinOps report for one client.\n"
        "Rules: use ONLY the numbers that appear in the FACTS below. Never compute, "
        "estimate, round or invent a number. Write two short paragraphs in English, "
        "plain business prose, no bullet points, no title. Return JSON only: "
        '{"synthesis": "<text>"}.\n\n'
        f"CLIENT: {contexte.tenant_nom}\nPERIOD: {contexte.periode}\nCURRENCY: {contexte.devise}\n"
        "FACTS:\n" + "\n".join(f"- {fait}" for fait in contexte.faits)
    )


class RedacteurBedrock:
    """Rédacteur réel : Bedrock `InvokeModel`, appelé hors tests uniquement."""

    def __init__(
        self, modele_id: str, region: str = "eu-west-1", client: object | None = None
    ) -> None:
        self.modele_id = modele_id
        self.region = region
        self._client = client

    @property
    def nom(self) -> str:
        return f"bedrock:{self.modele_id}"

    def _client_bedrock(self) -> object:
        """Client bedrock-runtime, créé à la première utilisation."""
        if self._client is None:
            import boto3  # import paresseux : les tests n'ont pas de réseau

            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def rediger(self, contexte: ContexteSynthese) -> str:
        """Rédige la synthèse via Bedrock, en échouant proprement si indisponible."""
        corps = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 800,
            "temperature": 0.2,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": _invite(contexte)}]}
            ],
        }
        try:
            reponse = self._client_bedrock().invoke_model(
                modelId=self.modele_id, body=json.dumps(corps).encode("utf-8")
            )
            charge = json.loads(reponse["body"].read().decode("utf-8"))
            texte = charge["content"][0]["text"]
            return json.loads(texte).get("synthesis", "")
        except Exception as erreur:  # toute panne du fournisseur → repli
            raise ServiceIndisponibleError(
                f"Synthèse Bedrock indisponible ({self.modele_id}) : {erreur}",
                modele=self.modele_id,
            ) from erreur


class RedacteurStrands:
    """Rédacteur réel via l'agent Strands « Rédacteur » (§12.1), hors tests.

    L'agent ne dispose que de cette invite : il n'a accès à aucun outil de
    lecture de données, donc il ne peut ni calculer ni aller chercher un chiffre.
    """

    def __init__(
        self, modele_id: str, region: str = "eu-west-1", agent: object | None = None
    ) -> None:
        self.modele_id = modele_id
        self.region = region
        self._agent = agent

    @property
    def nom(self) -> str:
        return f"strands:{self.modele_id}"

    def _obtenir_agent(self) -> object:
        """Agent Strands sans outil, construit à la première utilisation."""
        if self._agent is None:
            from strands import Agent  # import paresseux (groupe de dépendances `agents`)

            self._agent = Agent(model=self.modele_id, system_prompt="You rewrite facts as prose.")
        return self._agent

    def rediger(self, contexte: ContexteSynthese) -> str:
        """Rédige la synthèse via l'agent Strands, avec repli en cas d'échec."""
        try:
            return str(self._obtenir_agent()(_invite(contexte)))
        except Exception as erreur:  # toute panne de l'agent → repli
            raise ServiceIndisponibleError(
                f"Agent Strands indisponible ({self.modele_id}) : {erreur}", modele=self.modele_id
            ) from erreur


def empreinte_chiffres(rapport: RapportMensuel) -> str:
    """Empreinte de tous les champs chiffrés, synthèse exclue (ENF-IA-01)."""
    charge = rapport.model_dump(mode="json")
    charge.pop("synthese", None)
    return json.dumps(charge, sort_keys=True, ensure_ascii=False)


def appliquer_synthese(
    rapport: RapportMensuel, redacteur: RedacteurModele | None = None
) -> RapportMensuel:
    """Ajoute la synthèse au rapport sans toucher un seul chiffre (ENF-IA-01).

    Le rédacteur déterministe sert de repli : modèle indisponible, texte vide
    ou texte citant un nombre non fourni → le rapport reste valide, avec
    `synthese.degradee = True` si un modèle avait été demandé.
    """
    contexte = contexte_depuis_rapport(rapport)
    avant = empreinte_chiffres(rapport)
    synthese = _rediger(contexte, redacteur)
    enrichi = rapport.model_copy(update={"synthese": synthese})
    if empreinte_chiffres(enrichi) != avant:
        raise ValidationError("La synthèse a modifié un chiffre du rapport", rapport_id=rapport.id)
    return enrichi


def _rediger(contexte: ContexteSynthese, redacteur: RedacteurModele | None) -> SyntheseRapport:
    """Produit la synthèse, avec repli déterministe sur tout échec du modèle."""
    if redacteur is None:
        return SyntheseRapport(
            texte=RedacteurDeterministe().rediger(contexte), source=SOURCE_DETERMINISTE
        )
    if redacteur.nom == SOURCE_DETERMINISTE:
        # Rédacteur de démonstration demandé explicitement : rien n'est dégradé.
        return SyntheseRapport(texte=redacteur.rediger(contexte), source=SOURCE_DETERMINISTE)
    try:
        texte = redacteur.rediger(contexte)
    except ServiceIndisponibleError:
        return _repli(contexte, redacteur, degradee=True)
    if not texte or not texte.strip() or not texte_conforme(texte, contexte):
        return _repli(contexte, redacteur, degradee=True)
    return SyntheseRapport(texte=texte.strip(), source=SOURCE_MODELE, modele=redacteur.nom)


def _repli(
    contexte: ContexteSynthese, redacteur: RedacteurModele, *, degradee: bool
) -> SyntheseRapport:
    """Synthèse déterministe de secours, avec la trace du modèle écarté."""
    return SyntheseRapport(
        texte=RedacteurDeterministe().rediger(contexte),
        source=SOURCE_DETERMINISTE,
        modele=redacteur.nom,
        degradee=degradee,
    )
