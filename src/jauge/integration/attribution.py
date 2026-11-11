"""Service d'attribution : journaux d'invocation → agrégats + rapprochement (M1).

Le câblage utilise le module M1 **tel quel** :

- `jauge.attribution.logs.iterer_enregistrements` lit les journaux bruts ;
- `jauge.attribution.aggregate.AgregateurQuotidien` produit les agrégats
  quotidiens (EF-ATT-05) et la part non attribuée (EF-ATT-07) ;
- `jauge.attribution.reconcile` rapproche le recalcul de la facture CUR 2.0
  (EF-ATT-06).

Aucune formule de coût n'est réécrite ici (ENF-IA-01). Deux adaptations de
**format** seulement (les noms de champs ne sont pas confirmés, §23 Q2) :

1. les colonnes de l'export CUR du client sont renommées vers les variantes que
   M1 accepte déjà ;
2. une ligne de journal qui ne porte pas d'identifiant de modèle est complétée
   depuis la fiche de l'application : le générateur de trafic de la démo (M12)
   n'écrit pas `modelId` dans ses journaux — besoin signalé dans la PR.

Exigences visées : EF-ATT-05..07, §8.2, ENF-SEC-02.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from datetime import date

from jauge.attribution.aggregate import AgregateurQuotidien, ResultatAgregation
from jauge.attribution.logs import iterer_enregistrements
from jauge.attribution.reconcile import facture_par_jour, iterer_lignes_cur, rapprocher_journee
from jauge.attribution.tags import Etiquettes, extraire_etiquettes
from jauge.engine.calculator import TableTarifs
from jauge.integration.socle import charger_tenant
from jauge.models.cost import RapprochementJour
from jauge.store.protocol import Store

#: Colonnes de l'export CUR du client → variantes acceptées par M1 (§23 Q2).
RENOMMAGES_CUR: dict[str, str] = {
    "tag_application": "allocation_tag",
    "tag_team": "resource_tags_user_team",
    "tag_environment": "resource_tags_user_environment",
}

#: Colonne CUR qui porte le compte payeur (cloisonnement par client).
COLONNE_COMPTE = "payer_account_id"

#: Clés sous lesquelles un journal peut porter l'identifiant de modèle.
CLES_MODELE = ("modelId", "model_id", "model", "modelIdentifier", "modelArn")

#: Clé sous laquelle un journal porte ses étiquettes d'attribution.
CLES_ETIQUETTES = ("requestMetadata", "request_metadata", "metadonnees", "metadata", "tags")


def adapter_ligne_cur(ligne: Mapping[str, object]) -> dict[str, object]:
    """Renomme les colonnes client vers le schéma attendu par le module M1."""
    adaptee = dict(ligne)
    for source, cible in RENOMMAGES_CUR.items():
        if source in adaptee and cible not in adaptee:
            adaptee[cible] = adaptee[source]
    return adaptee


def etiquettes_de_ligne(ligne: Mapping[str, object]) -> Etiquettes:
    """Étiquettes d'attribution d'une ligne de journal (M1 `extraire_etiquettes`)."""
    for cle in CLES_ETIQUETTES:
        brut = ligne.get(cle)
        if isinstance(brut, str):
            try:
                brut = json.loads(brut)
            except json.JSONDecodeError:
                continue
        if isinstance(brut, Mapping):
            return extraire_etiquettes(brut)
    return Etiquettes()


def ligne_porte_modele(ligne: Mapping[str, object]) -> bool:
    """Vrai si la ligne de journal porte déjà un identifiant de modèle."""
    return any(ligne.get(cle) not in (None, "") for cle in CLES_MODELE)


class ServiceAttribution:
    """Branche le module d'attribution (M1) sur le store du plan de contrôle."""

    def __init__(self, store: Store, table_tarifs: TableTarifs) -> None:
        self._store = store
        self._tarifs = table_tarifs

    def enrichir(self, tenant_id: str, lignes: Iterable[Mapping[str, object]]) -> list[dict]:
        """Complète les lignes sans modèle avec celui de leur application."""
        return [self._enrichir(tenant_id, ligne) for ligne in lignes]

    def _enrichir(self, tenant_id: str, ligne: Mapping[str, object]) -> dict:
        """Ajoute `modelId` quand le journal ne le porte pas (§23 Q2)."""
        if ligne_porte_modele(ligne):
            return dict(ligne)
        application_id = etiquettes_de_ligne(ligne).application
        if not application_id:
            return dict(ligne)
        application = self._store.obtenir_application(tenant_id, application_id)
        if application is None:
            return dict(ligne)
        enrichie = dict(ligne)
        enrichie["modelId"] = application.modele_actuel
        return enrichie

    def agreger(self, tenant_id: str, lignes: Iterable[Mapping[str, object]]) -> ResultatAgregation:
        """Agrège les journaux d'un client (EF-ATT-05), sans rien écrire."""
        tenant = charger_tenant(self._store, tenant_id)
        agregateur = AgregateurQuotidien(
            tenant,
            self._tarifs,
            applications=self._store.lister_applications(tenant_id),
            region=tenant.region_principale,
        )
        return agregateur.agreger(iterer_enregistrements(self.enrichir(tenant_id, lignes)))

    def enregistrer_agregats(
        self, tenant_id: str, lignes: Iterable[Mapping[str, object]]
    ) -> ResultatAgregation:
        """Agrège puis enregistre les agrégats dans le store."""
        resultat = self.agreger(tenant_id, lignes)
        self._store.enregistrer_agregats(resultat.agregats)
        return resultat

    def rapprocher(
        self,
        tenant_id: str,
        resultat: ResultatAgregation,
        lignes_cur: Iterable[Mapping[str, object]],
    ) -> list[RapprochementJour]:
        """Rapproche le recalcul de la facture CUR 2.0 et l'enregistre (EF-ATT-06)."""
        tenant = charger_tenant(self._store, tenant_id)
        lignes = [
            adapter_ligne_cur(ligne)
            for ligne in lignes_cur
            if str(ligne.get(COLONNE_COMPTE, "")) == tenant.compte_aws
        ]
        factures = facture_par_jour(iterer_lignes_cur(lignes))
        return self._enregistrer_rapprochements(tenant_id, resultat, factures)

    def _enregistrer_rapprochements(
        self, tenant_id: str, resultat: ResultatAgregation, factures: Mapping[date, object]
    ) -> list[RapprochementJour]:
        """Écrit un rapprochement par jour facturé."""
        rapprochements: list[RapprochementJour] = []
        for jour in sorted(factures):
            agregat_du_jour = resultat.pour_jour(jour)
            if agregat_du_jour is None:
                continue
            rapprochement = rapprocher_journee(agregat_du_jour, factures[jour], tenant_id)  # type: ignore[arg-type]
            self._store.enregistrer_rapprochement(rapprochement)
            rapprochements.append(rapprochement)
        return rapprochements

    def lignes_par_tenant(
        self, lignes: Iterable[Mapping[str, object]]
    ) -> dict[str, list[Mapping[str, object]]]:
        """Répartit des journaux bruts par client, via l'étiquette d'application.

        Une ligne dont l'application est inconnue du plan de contrôle n'est
        rattachée à aucun client : elle est écartée ici (M1 la compterait comme
        dépense non attribuée, EF-ATT-07).
        """
        par_tenant: dict[str, list[Mapping[str, object]]] = {}
        for ligne in lignes:
            tenant_id = self._tenant_de_ligne(ligne)
            if tenant_id is None:
                continue
            par_tenant.setdefault(tenant_id, []).append(ligne)
        return par_tenant

    def _tenant_de_ligne(self, ligne: Mapping[str, object]) -> str | None:
        """Client propriétaire d'une ligne de journal, via son application."""
        application_id = etiquettes_de_ligne(ligne).application
        if not application_id:
            return None
        for tenant in self._store.lister_tenants():
            if self._store.obtenir_application(tenant.id, application_id) is not None:
                return tenant.id
        return None

    def iterer_enrichies(self, tenant_id: str, lignes: Iterable[Mapping]) -> Iterator[Mapping]:
        """Flux des lignes enrichies (utilisé par le plan de données)."""
        yield from self.enrichir(tenant_id, lignes)
