"""Paramètres d'administration du plan de contrôle (EF-ADM-01).

Table des tarifs, modèles candidats autorisés par client, seuils de qualité,
taille d'échantillon par défaut, paliers de bascule et durée de conservation
des journaux (ENF-CONF-03). Le stockage est local au module API : le socle
`jauge.store.protocol.Store` ne définit pas encore de contrat de réglages
(besoin signalé dans la PR).
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field, field_validator

from jauge.common.errors import ValidationError
from jauge.models.pricing import TarifModele


class ParametresAdmin(BaseModel):
    """Réglages modifiables par l'administrateur de plateforme."""

    table_tarifs: list[TarifModele] = Field(default_factory=list)
    modeles_candidats_par_client: dict[str, list[str]] = Field(default_factory=dict)
    seuil_qualite_defaut: float = Field(default=0.95, ge=0.5, le=1.0)
    seuil_intervalle_bas_defaut: float = Field(default=0.90, ge=0.5, le=1.0)
    taille_echantillon_defaut: int = Field(default=200, ge=10, le=2000)
    paliers_bascule: list[int] = Field(default_factory=lambda: [10, 50, 100])
    duree_conservation_jours: int = Field(default=365, ge=1, le=3650)

    @field_validator("paliers_bascule")
    @classmethod
    def _paliers_valides(cls, paliers: list[int]) -> list[int]:
        """Les paliers doivent être croissants, bornés à 100 et finir à 100."""
        if not paliers:
            raise ValueError("au moins un palier est requis")
        if paliers != sorted(paliers) or len(set(paliers)) != len(paliers):
            raise ValueError("les paliers doivent être croissants et distincts")
        if paliers[-1] != 100:
            raise ValueError("le dernier palier doit valoir 100 %")
        if any(p <= 0 or p > 100 for p in paliers):
            raise ValueError("un palier doit être compris entre 1 et 100")
        return paliers


class ReglagesStore(Protocol):
    """Contrat de persistance des paramètres d'administration."""

    def obtenir(self) -> ParametresAdmin:
        """Rend les paramètres courants."""
        ...

    def remplacer(self, parametres: ParametresAdmin) -> None:
        """Remplace l'ensemble des paramètres."""
        ...


class MemoryReglagesStore:
    """Réglages en mémoire, utilisés par les tests et le mode démo."""

    def __init__(self, parametres: ParametresAdmin | None = None) -> None:
        self._parametres = parametres or ParametresAdmin()

    def obtenir(self) -> ParametresAdmin:
        """Rend une copie des paramètres courants."""
        return self._parametres.model_copy(deep=True)

    def remplacer(self, parametres: ParametresAdmin) -> None:
        """Remplace les paramètres courants."""
        self._parametres = parametres


def appliquer_patch(parametres: ParametresAdmin, patch: dict[str, object]) -> ParametresAdmin:
    """Applique une mise à jour partielle et valide le résultat complet."""
    fusion = parametres.model_dump()
    for cle, valeur in patch.items():
        if valeur is None:
            continue
        if cle not in fusion:
            raise ValidationError(f"Paramètre inconnu : {cle}", parametre=cle)
        fusion[cle] = valeur
    try:
        return ParametresAdmin.model_validate(fusion)
    except ValueError as exc:
        raise ValidationError("Paramètres invalides", erreurs=str(exc)) from exc
