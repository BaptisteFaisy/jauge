"""Catalogue de tarifs historisé et variations de prix (piste n° 10, EF-ATT-04, §14).

Deux lectures, avec deux frontières différentes :

- `GET /v1/pricing/catalogue` : les prix en vigueur à une date, leur période de
  validité et l'empreinte de la table. Ce ne sont pas des données de client —
  tout rôle authentifié peut les lire ;
- `GET /v1/tenants/{id}/pricing/variations` : ce qu'un changement de prix vaut
  **pour ce client** (impact à volume constant) et quelles preuves deviennent
  caduques. Données cloisonnées, accès vérifié par `tenant_id` (ENF-SEC-02).

Rien n'est réécrit dans le store : la route **signale** ce qui est devenu caduc,
la décision de rejouer une campagne reste humaine (ENF-IA-02). Tous les chiffres
sont calculés par le code (ENF-IA-01).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.tenancy import verifier_acces_tenant
from jauge.common.errors import IntrouvableError, ValidationError
from jauge.common.temps import aujourdhui as date_du_jour
from jauge.models.pricing import ServiceTier
from jauge.pricing_data.catalogue import (
    CatalogueTarifs,
    PrixApplicatif,
    SourceTarifsLocale,
)
from jauge.pricing_data.impact_prix import (
    ImpactVariation,
    PreuveARejouer,
    impact_variation,
    preuves_a_rejouer,
)
from jauge.pricing_data.surveillance import VariationPrix, variations_entre_dates

router = APIRouter(tags=["tarifs"])


def obtenir_catalogue(request: Request) -> CatalogueTarifs:
    """Catalogue des tarifs, construit une fois par application.

    La source est la table datée du dépôt (`SourceTarifsLocale`) ; brancher
    l'adaptateur `SourceTarifsAws` ne change rien à ce qui suit (§23 Q3).
    """
    catalogue = getattr(request.app.state, "catalogue_tarifs", None)
    if catalogue is None:
        catalogue = CatalogueTarifs.depuis_source(SourceTarifsLocale())
        request.app.state.catalogue_tarifs = catalogue
    return catalogue


#: Alias de dépendance FastAPI du catalogue de tarifs.
DepCatalogue = Annotated[CatalogueTarifs, Depends(obtenir_catalogue)]


class CataloguePublie(BaseModel):
    """Réponse de `GET /v1/pricing/catalogue`."""

    a_la_date: date
    empreinte: str
    nombre_prix: int
    prix: list[PrixApplicatif] = Field(default_factory=list)


class VariationsClient(BaseModel):
    """Réponse de `GET /v1/tenants/{id}/pricing/variations`."""

    tenant_id: str
    depuis: date | None = None
    jusqu: date
    variations: list[VariationPrix] = Field(default_factory=list)
    impacts: list[ImpactVariation] = Field(default_factory=list)
    preuves_a_rejouer: list[PreuveARejouer] = Field(default_factory=list)


@router.get("/v1/pricing/catalogue", response_model=CataloguePublie)
def catalogue_tarifs(
    utilisateur: DepUtilisateur,
    catalogue: DepCatalogue,
    a_la_date: Annotated[date | None, Query()] = None,
    region: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    niveau: Annotated[ServiceTier | None, Query()] = None,
) -> CataloguePublie:
    """Prix en vigueur à une date, avec la période de validité de chaque ligne.

    L'empreinte rendue est celle de la table à cette date : c'est la même
    empreinte que scelle un proof pack, ce qui permet de vérifier qu'une preuve
    a bien été calculée sur les prix annoncés.
    """
    jour = a_la_date or date_du_jour()
    prix = catalogue.tarifs_courants(jour, region=region, niveau=niveau)
    return CataloguePublie(
        a_la_date=jour,
        empreinte=catalogue.empreinte(jour),
        nombre_prix=len(prix),
        prix=prix,
    )


@router.get("/v1/tenants/{tenant_id}/pricing/variations", response_model=VariationsClient)
def variations_prix(
    tenant_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
    catalogue: DepCatalogue,
    jusqu: Annotated[date | None, Query()] = None,
    depuis: Annotated[date | None, Query()] = None,
) -> VariationsClient:
    """Variations de prix, impact pour le client et preuves devenues caduques.

    `depuis` absent : la comparaison part de la **première** table connue, donc
    toute variation enregistrée depuis l'origine est rendue. `jusqu` absent : la
    date du jour.
    """
    verifier_acces_tenant(utilisateur, tenant_id, audit, "lecture_variations_prix", "tenant")
    if store.obtenir_tenant(tenant_id) is None:
        raise IntrouvableError("Client introuvable", tenant_id=tenant_id)
    fin = jusqu or date_du_jour()
    if depuis is not None and depuis > fin:
        raise ValidationError(
            "`depuis` must not be after `jusqu`", depuis=str(depuis), jusqu=str(fin)
        )
    variations = variations_entre_dates(catalogue, depuis, fin)
    impacts = [
        impact
        for impact in (impact_variation(store, tenant_id, v) for v in variations)
        if impact is not None
    ]
    return VariationsClient(
        tenant_id=tenant_id,
        depuis=depuis,
        jusqu=fin,
        variations=variations,
        impacts=impacts,
        preuves_a_rejouer=preuves_a_rejouer(store, tenant_id, variations),
    )
