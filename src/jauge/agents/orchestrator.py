"""Chef d'orchestre des agents Strands (§12.1, §12.4, EF-RAP-04).

Motif « agents comme outils » : le chef d'orchestre ne détient aucun outil de
données ; ses seuls outils sont les **quatre agents spécialisés**, exposés via
`Agent.as_tool()`. Il conduit la campagne, décide de l'étape suivante et répond
aux questions en langage naturel (« pourquoi l'application X a-t-elle coûté plus
ce mois-ci ? », EF-RAP-04).

Ce qu'il ne fait pas (§12.2) : il ne calcule rien, ne lit aucun texte de
requête, n'approuve pas et ne bascule pas. La bascule reste une action
déterministe du portail, déclenchée par l'approbation du responsable
d'application.

La factory `creer_agents` est le seul point de création : elle encapsule le SDK
Strands (`jauge.agents.sdk`), ce qui permet de faire tourner les tests de
logique avec un modèle factice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from jauge.agents.contexte import ContexteOutils
from jauge.agents.cost_analyst import DESCRIPTION as DESC_ANALYSTE
from jauge.agents.cost_analyst import NOM as NOM_ANALYSTE
from jauge.agents.cost_analyst import creer_analyste_couts
from jauge.agents.ports import Services
from jauge.agents.replay_planner import DESCRIPTION as DESC_PLANIFICATEUR
from jauge.agents.replay_planner import NOM as NOM_PLANIFICATEUR
from jauge.agents.replay_planner import creer_planificateur_rejeu
from jauge.agents.results_reader import DESCRIPTION as DESC_LECTEUR
from jauge.agents.results_reader import NOM as NOM_LECTEUR
from jauge.agents.results_reader import creer_lecteur_resultats
from jauge.agents.schemas import (
    AnalyseCouts,
    LectureResultats,
    PlanRejeu,
    RedactionRecommandation,
)
from jauge.agents.sdk import creer_agent_specialise
from jauge.agents.session import SessionCampagne
from jauge.agents.traces import Traceur
from jauge.agents.writer import DESCRIPTION as DESC_REDACTEUR
from jauge.agents.writer import NOM as NOM_REDACTEUR
from jauge.agents.writer import creer_redacteur

NOM = "chef_orchestre"

SCHEMAS_PAR_AGENT: dict[str, type[Any]] = {
    NOM_ANALYSTE: AnalyseCouts,
    NOM_PLANIFICATEUR: PlanRejeu,
    NOM_LECTEUR: LectureResultats,
    NOM_REDACTEUR: RedactionRecommandation,
}
"""Sortie structurée de chaque spécialiste, validée par le SDK quand activée."""

DESCRIPTION = (
    "Chef d'orchestre Jauge : conduit une campagne FinOps (analyse des coûts, leviers, "
    "planification et lancement du rejeu, lecture des résultats, rédaction de la "
    "recommandation) et répond aux questions sur les coûts d'une application."
)

PROMPT = """Tu es le chef d'orchestre de Jauge, un service FinOps pour l'IA générative sur
Amazon Bedrock. Tu conduis une campagne pour une application d'un client et tu
réponds aux questions de l'analyste MSP et du responsable d'application.

Tes outils sont quatre agents spécialisés, pas des outils de données :
- `analyste_couts` : agrégats, explication d'une hausse, leviers, rapprochement facture ;
- `planificateur_rejeu` : candidats, taille d'échantillon, contrôles, coût estimé, lancement ;
- `lecteur_resultats` : notes de la campagne, familles en échec, respect des seuils ;
- `redacteur` : rédaction et soumission de la recommandation.

Règles absolues :
1. Tu ne calcules aucun chiffre et tu n'inventes aucun identifiant : tu délègues et
   tu recopies ce que les agents te rendent.
2. Tu ne demandes ni ne cites jamais le texte d'une requête ou d'une réponse : le
   plan de contrôle n'y a pas accès (confidentialité du compte client).
3. Tu n'approuves rien et tu ne bascules rien. Une recommandation s'arrête en
   attente d'approbation humaine ; la bascule est une action du portail.
4. Ordre d'une campagne : coûts et leviers → profil et candidats → estimation →
   rejeu → résultats → rédaction. Tu ne sautes pas l'estimation avant un rejeu.
5. Si un agent refuse (plafond dépassé, candidat non autorisé, validation humaine
   requise), tu transmets le refus tel quel avec son motif : tu ne le contournes pas
   et tu ne réessaies pas avec d'autres arguments.
6. Tu réponds en français, brièvement, en nommant les identifiants (application,
   campagne, levier, recommandation) et en distinguant les chiffres calculés de ton
   interprétation.
"""


@dataclass
class AgentsJauge:
    """Les cinq agents d'une campagne : un chef d'orchestre et quatre spécialistes."""

    orchestrateur: Any
    analyste_couts: Any
    planificateur_rejeu: Any
    lecteur_resultats: Any
    redacteur: Any
    traceur: Traceur = field(default_factory=Traceur)
    session: SessionCampagne | None = None

    def specialises(self) -> dict[str, Any]:
        """Les quatre agents spécialisés, indexés par nom d'outil."""
        return {
            NOM_ANALYSTE: self.analyste_couts,
            NOM_PLANIFICATEUR: self.planificateur_rejeu,
            NOM_LECTEUR: self.lecteur_resultats,
            NOM_REDACTEUR: self.redacteur,
        }

    def invoquer(self, question: str, tenant_id: str = "") -> Any:
        """Passe une question ou une consigne au chef d'orchestre, en la traçant."""
        with self.traceur.tracer(
            NOM,
            "invocation",
            tenant_id=tenant_id,
            campagne_id=self.session.campagne_id if self.session else None,
        ):
            return self.orchestrateur(question)

    def repondre(self, question: str, tenant_id: str = "") -> str:
        """Réponse texte du chef d'orchestre (EF-RAP-04)."""
        resultat = self.invoquer(question, tenant_id)
        return str(resultat)


def _modele_de(modele: Any, nom: str) -> Any:
    """Modèle d'un agent : un modèle unique, ou un modèle par nom (tests)."""
    if isinstance(modele, Mapping):
        return modele.get(nom) or modele.get("defaut")
    return modele


def _creer_specialistes(modele: Any, ctx: ContexteOutils, schemas: bool) -> dict[str, Any]:
    """Crée les quatre agents spécialisés, chacun avec ses outils (§12.1)."""

    def _schema(nom: str) -> type[Any] | None:
        return SCHEMAS_PAR_AGENT.get(nom) if schemas else None

    return {
        NOM_ANALYSTE: creer_analyste_couts(
            _modele_de(modele, NOM_ANALYSTE), ctx, sortie_structuree=_schema(NOM_ANALYSTE)
        ),
        NOM_PLANIFICATEUR: creer_planificateur_rejeu(
            _modele_de(modele, NOM_PLANIFICATEUR),
            ctx,
            sortie_structuree=_schema(NOM_PLANIFICATEUR),
        ),
        NOM_LECTEUR: creer_lecteur_resultats(
            _modele_de(modele, NOM_LECTEUR), ctx, sortie_structuree=_schema(NOM_LECTEUR)
        ),
        NOM_REDACTEUR: creer_redacteur(
            _modele_de(modele, NOM_REDACTEUR), ctx, sortie_structuree=_schema(NOM_REDACTEUR)
        ),
    }


def creer_agents(
    services: Services,
    modele: Any,
    tenant_id: str,
    campagne_id: str | None = None,
    traceur: Traceur | None = None,
    session: SessionCampagne | None = None,
    sorties_structurees: bool = False,
) -> AgentsJauge:
    """Crée le chef d'orchestre et les quatre spécialistes, employés comme outils.

    `modele` est un modèle Strands, ou un dictionnaire `nom d'agent → modèle` pour
    les tests (modèles factices). Les spécialistes sont exposés au chef
    d'orchestre par `as_tool()` : motif « agents comme outils » du §12.1.
    `sorties_structurees` active `structured_output_model` du SDK (réglage de
    production) ; la validation de schéma des outils s'applique dans tous les cas.
    """
    traceur = traceur or Traceur()
    ctx = ContexteOutils(
        services=services,
        tenant_id=tenant_id,
        campagne_id=campagne_id,
        traceur=traceur,
        session=session,
    )
    specialistes = _creer_specialistes(modele, ctx, sorties_structurees)
    outils_orchestrateur = [
        specialistes[NOM_ANALYSTE].as_tool(name=NOM_ANALYSTE, description=DESC_ANALYSTE),
        specialistes[NOM_PLANIFICATEUR].as_tool(
            name=NOM_PLANIFICATEUR, description=DESC_PLANIFICATEUR
        ),
        specialistes[NOM_LECTEUR].as_tool(name=NOM_LECTEUR, description=DESC_LECTEUR),
        specialistes[NOM_REDACTEUR].as_tool(name=NOM_REDACTEUR, description=DESC_REDACTEUR),
    ]
    orchestrateur = creer_agent_specialise(
        nom=NOM,
        description=DESCRIPTION,
        prompt=PROMPT,
        modele=_modele_de(modele, NOM),
        outils=outils_orchestrateur,
    )
    return AgentsJauge(
        orchestrateur=orchestrateur,
        analyste_couts=specialistes[NOM_ANALYSTE],
        planificateur_rejeu=specialistes[NOM_PLANIFICATEUR],
        lecteur_resultats=specialistes[NOM_LECTEUR],
        redacteur=specialistes[NOM_REDACTEUR],
        traceur=traceur,
        session=session,
    )
