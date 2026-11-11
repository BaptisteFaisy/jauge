"""Questions en langage naturel (`POST /v1/chat`) — EF-RAP-04, garde-fou ENF-IA-01.

Le port `ChatPort` sera branché sur les agents Strands (M7). L'implémentation
de démonstration répond avec des chiffres déjà calculés par le code : aucun
nombre n'est produit par un modèle, et aucun texte de requête ne sort
(ENF-CONF-01).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from jauge.api.audit import journaliser
from jauge.api.deps import DepAudit, DepPortChat, DepUtilisateur
from jauge.api.tenancy import verifier_acces_tenant
from jauge.common.errors import ValidationError

router = APIRouter(tags=["chat"])


class DemandeChat(BaseModel):
    """Corps de `POST /v1/chat`."""

    question: str = Field(min_length=1, max_length=2000)
    tenant_id: str | None = Field(
        default=None, description="Client visé ; par défaut celui de l'appelant"
    )

    @field_validator("question")
    @classmethod
    def _question_non_vide(cls, question: str) -> str:
        """Refuse une question faite uniquement d'espaces."""
        if not question.strip():
            raise ValueError("la question ne peut pas être vide")
        return question.strip()


@router.post("/v1/chat", response_model=dict[str, Any])
def poser_question(
    demande: DemandeChat,
    utilisateur: DepUtilisateur,
    port: DepPortChat,
    audit: DepAudit,
) -> dict[str, Any]:
    """Répond à une question de coût en langage naturel (agents M7 ou démo)."""
    tenant_id = demande.tenant_id or utilisateur.tenant_id
    if not tenant_id:
        raise ValidationError("Aucun client de rattachement pour cette question")
    verifier_acces_tenant(utilisateur, tenant_id, audit, "question_chat", "tenant")
    reponse = port.repondre(tenant_id, demande.question)
    journaliser(
        audit,
        utilisateur,
        "question_chat",
        "tenant",
        tenant_id,
        {"longueur_question": len(demande.question)},
        tenant_id=tenant_id,
    )
    return reponse
