"""Dossier de preuve scellé d'une campagne de rejeu (« proof pack », piste 6 du top 10).

Un « proof pack » est le dossier qu'un tiers — auditeur, juge du hackathon,
client — peut **revérifier seul**, sans faire confiance au MSP qui l'a produit.
C'est l'artefact qui rend opposable la part des économies vérifiées (§7.2) :
sans lui, le montant facturé est négociable à l'infini ; avec lui, chaque chiffre
est soit scellé, soit recalculable.

Le pack est découpé en neuf sections, dans un ordre fixé une fois pour toutes
(`ORDRE_SECTIONS`) : identité de la campagne, protocole et versions, échantillon
figé, résultats par candidat, contrôles déterministes, relecture humaine,
économie attendue et son mode de calcul, recommandation au format de l'Annexe B,
approbation (auteur et horodatage).

Trois propriétés, quatre modules :

- `jauge.reports.scellement` — sérialisation canonique, chaîne de hachage
  SHA-256 (chaque maillon inclut le précédent), signature HMAC, constat détaché ;
- `jauge.reports.proof_pack_verification` — `rejouer_verifications(pack)` :
  recalcule intervalles de Wilson, coûts par requête (Annexe D sur la table des
  tarifs scellée), économie et accord juge/humain **depuis le pack seul**, et
  compare aux valeurs déclarées ;
- `jauge.reports.proof_pack_rendu` — les deux vues du même contenu : JSON et
  Markdown en anglais, lisible par un non-technicien ;
- `jauge.reports.proof_pack_depot` — port d'archivage en écriture seule
  (S3 Object Lock) et son double hors ligne pour les tests.

Exigences visées : ENF-CONF-01 (aucun texte de requête ni de réponse ne figure
dans le pack, uniquement des empreintes et des agrégats), ENF-IA-01 (aucun
chiffre produit par un modèle : tout vient du socle), ENF-SEC-02 (le pack est
construit pour un client identifié ; tout mélange de clients est refusé),
§16 (traçabilité : protocole, versions, table des tarifs datée, approbation).

Aucune dépendance nouvelle : `hashlib`, `hmac` et `json` de la bibliothèque
standard suffisent au scellement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import import_module
from typing import Any

from pydantic import BaseModel, ConfigDict

from jauge.common.errors import ValidationError
from jauge.common.ids import deterministic, horodatage_utc
from jauge.engine.calculator import TableTarifs
from jauge.models.campaign import Campagne
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.models.recommendation import Recommandation, VerificationEconomie
from jauge.models.tenant import Application, Tenant
from jauge.replay.executor import NIVEAU_REJEU
from jauge.reports.proof_pack_entrees import (
    Approbation,
    EntreeCandidat,
    EntreeEchantillon,
    ResumeRelecture,
)
from jauge.reports.proof_pack_sections import (
    section_approbation,
    section_controles,
    section_echantillon,
    section_economie,
    section_identite,
    section_protocole,
    section_recommandation,
    section_relecture,
    section_resultats,
)
from jauge.reports.scellement import Signataire, sceller_et_signer

#: Version du format du dossier de preuve (à incrémenter à chaque changement).
VERSION_PROOF_PACK = "1.0"

#: Ordre de la chaîne de hachage : il fait partie du scellement.
ORDRE_SECTIONS = (
    "identite",
    "protocole",
    "echantillon",
    "resultats",
    "controles",
    "relecture",
    "economie",
    "recommandation",
    "approbation",
)

#: Exports différés (PEP 562) : les modules satellites importent celui-ci.
_EXPORTS_DIFFERES = {
    "rejouer_verifications": "proof_pack_verification",
    "ResultatRejeu": "proof_pack_verification",
    "Verification": "proof_pack_verification",
    "TOLERANCE_TAUX": "proof_pack_verification",
    "rendre_json_pack": "proof_pack_rendu",
    "rendre_markdown_pack": "proof_pack_rendu",
    "DepotMemoireScelle": "proof_pack_depot",
    "DepotProofPack": "proof_pack_depot",
    "DepotS3Scelle": "proof_pack_depot",
    "cle_pack": "proof_pack_depot",
    "publier_pack": "proof_pack_depot",
}

__all__ = [
    "ORDRE_SECTIONS",
    "VERSION_PROOF_PACK",
    "Approbation",
    "EntreeCandidat",
    "EntreeEchantillon",
    "ProofPack",
    "ResumeRelecture",
    "construire_proof_pack",
    *_EXPORTS_DIFFERES,
]


def __getattr__(nom: str) -> Any:
    """Export différé des modules satellites, sans import circulaire."""
    module = _EXPORTS_DIFFERES.get(nom)
    if module is None:
        raise AttributeError(f"le module {__name__!r} n'a pas d'attribut {nom!r}")
    return getattr(import_module(f"jauge.reports.{module}"), nom)


class ProofPack(BaseModel):
    """Dossier de preuve scellé : neuf sections et leur sceau.

    `sections` associe le nom de chaque section à son contenu ; l'ordre de la
    chaîne est porté par `scellement.sections` et rappelé par `ORDRE_SECTIONS`.
    `scellement` contient l'empreinte de chaque section, l'empreinte cumulative
    de chaque maillon, l'empreinte finale du pack et, le cas échéant, sa
    signature HMAC.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = VERSION_PROOF_PACK
    id: str
    sections: dict[str, dict[str, Any]]
    scellement: dict[str, Any]

    @property
    def empreinte_pack(self) -> str:
        """Empreinte finale de la chaîne : la valeur à publier séparément."""
        return str(self.scellement.get("empreinte_pack", ""))

    def section(self, nom: str) -> dict[str, Any]:
        """Contenu d'une section ; erreur métier si elle est absente."""
        try:
            return self.sections[nom]
        except KeyError as erreur:
            raise ValidationError("section absente du dossier de preuve", section=nom) from erreur

    def sections_ordonnees(self) -> list[tuple[str, Any]]:
        """Sections dans l'ordre de la chaîne de hachage."""
        return [(nom, self.sections[nom]) for nom in ORDRE_SECTIONS if nom in self.sections]

    def vers_dict(self) -> dict[str, Any]:
        """Document JSON du pack (mêmes valeurs que la forme canonique)."""
        return self.model_dump(mode="json")

    @classmethod
    def depuis_document(cls, document: object) -> ProofPack:
        """Relit un pack depuis son document JSON (ce que reçoit un tiers)."""
        try:
            return cls.model_validate(document)
        except ValueError as erreur:
            raise ValidationError(f"dossier de preuve illisible : {erreur}") from erreur


def _valider_coherence(
    campagne: Campagne,
    application: Application,
    recommandation: Recommandation,
    tenant: Tenant | None,
) -> None:
    """Refuse un pack qui mélangerait deux clients ou deux campagnes (ENF-SEC-02)."""
    if campagne.tenant_id != application.tenant_id:
        raise ValidationError(
            "campagne et application de clients différents",
            campagne=campagne.tenant_id,
            application=application.tenant_id,
        )
    if campagne.application_id != application.id:
        raise ValidationError(
            "la campagne ne porte pas sur cette application",
            campagne=campagne.application_id,
            application=application.id,
        )
    if recommandation.campaignId != campagne.id:
        raise ValidationError(
            "la recommandation ne provient pas de cette campagne",
            recommandation=recommandation.campaignId,
            campagne=campagne.id,
        )
    if tenant is not None and tenant.id != campagne.tenant_id:
        raise ValidationError(
            "client du dossier différent du client de la campagne",
            dossier=tenant.id,
            campagne=campagne.tenant_id,
        )


def _valider_candidats(candidats: Sequence[EntreeCandidat]) -> None:
    """Un modèle apparaît une fois, et au plus une fois comme référence."""
    modeles = [entree.modele_id for entree in candidats]
    doublons = {modele for modele in modeles if modeles.count(modele) > 1}
    if doublons:
        raise ValidationError(
            "candidats en double dans le dossier de preuve", modeles=sorted(doublons)
        )
    if sum(1 for entree in candidats if entree.resultat.est_reference) > 1:
        raise ValidationError(
            "plusieurs références : une seule comparaison de référence est admise"
        )


def _region(campagne: Campagne, tenant: Tenant | None, region: str | None) -> str:
    """Région du pack : explicite, sinon celle du client, sinon erreur."""
    retenue = region or (tenant.region_principale if tenant else None)
    if not retenue:
        raise ValidationError(
            "région absente : fournir `region` ou le `tenant` de la campagne",
            campagne_id=campagne.id,
        )
    return retenue


@dataclass(frozen=True)
class EntreesPreuve:
    """Objet-paramètre de la construction d'un pack : tout le contexte, figé.

    Regrouper les entrées évite de propager dix-sept arguments à chaque
    constructeur de section et garantit que toutes les sections voient le même
    contexte (même région, même date de tarifs). Les valeurs dérivées (table des
    tarifs, région retenue, date de référence, horodatage) sont des propriétés :
    l'appelant ne fournit que ce qu'il sait.
    """

    campagne: Campagne
    application: Application
    recommandation: Recommandation
    candidats: Sequence[EntreeCandidat]
    echantillon: Sequence[EntreeEchantillon]
    tarifs: Sequence[TarifModele]
    tenant: Tenant | None = None
    region: str | None = None
    niveau: ServiceTier = NIVEAU_REJEU
    a_la_date: date | None = None
    relecture: ResumeRelecture | None = None
    approbation: Approbation | None = None
    verification: VerificationEconomie | None = None
    protocole_pre_enregistre: object | None = None
    part_msp_pourcentage: float = 0.0
    genere_le: str | None = None

    @property
    def table(self) -> TableTarifs:
        """Table des tarifs datée, reconstruite depuis les lignes fournies."""
        return TableTarifs(list(self.tarifs))

    @property
    def region_retenue(self) -> str:
        """Région du pack : explicite, sinon celle du client."""
        return _region(self.campagne, self.tenant, self.region)

    @property
    def date_reference(self) -> date:
        """Date d'effet retenue pour les tarifs (Annexe D)."""
        return self.a_la_date or date.today()

    @property
    def horodatage(self) -> str:
        """Horodatage de génération du pack."""
        return self.genere_le or horodatage_utc(datetime.now(UTC))


def construire_proof_pack(
    entrees: EntreesPreuve, *, signataire: Signataire | None = None
) -> ProofPack:
    """Assemble et scelle le dossier de preuve d'une campagne de rejeu.

    `entrees` porte tout le contexte (voir `EntreesPreuve`) ; `signataire` signe
    l'empreinte finale en HMAC — sans lui le pack reste scellé par la chaîne,
    mais son origine n'est pas prouvée. La construction refuse tout mélange de
    clients (ENF-SEC-02) et tout candidat en double.
    """
    _valider_coherence(
        entrees.campagne, entrees.application, entrees.recommandation, entrees.tenant
    )
    _valider_candidats(entrees.candidats)
    if not entrees.echantillon:
        raise ValidationError("échantillon vide : rien à prouver", campagne_id=entrees.campagne.id)
    sections = _construire_sections(entrees)
    sceau = sceller_et_signer(
        [(nom, sections[nom]) for nom in ORDRE_SECTIONS], signataire=signataire
    )
    identifiant = deterministic(
        "proof-pack", entrees.campagne.tenant_id, entrees.campagne.id, entrees.recommandation.id
    )
    return ProofPack(id=f"pp-{identifiant}", sections=sections, scellement=sceau)


def _sections_mesurees(entrees: EntreesPreuve) -> dict[str, dict[str, Any]]:
    """Sections descriptives : identité, protocole, échantillon, résultats."""
    commun = {
        "region": entrees.region_retenue,
        "niveau": entrees.niveau,
        "a_la_date": entrees.date_reference,
    }
    return {
        "identite": section_identite(
            campagne=entrees.campagne,
            application=entrees.application,
            tenant=entrees.tenant,
            region=entrees.region_retenue,
            a_la_date=entrees.date_reference,
        ),
        "protocole": section_protocole(
            campagne=entrees.campagne,
            application=entrees.application,
            tarifs=entrees.tarifs,
            protocole_pre_enregistre=entrees.protocole_pre_enregistre,
            **commun,
        ),
        "echantillon": section_echantillon(entrees=entrees.echantillon, campagne=entrees.campagne),
        "resultats": section_resultats(candidats=entrees.candidats, table=entrees.table, **commun),
    }


def _sections_decidees(entrees: EntreesPreuve) -> dict[str, dict[str, Any]]:
    """Sections de décision : contrôles, relecture, économie, recommandation, approbation."""
    return {
        "controles": section_controles(entrees.candidats),
        "relecture": section_relecture(entrees.relecture, entrees.application),
        "economie": section_economie(
            recommandation=entrees.recommandation,
            part_msp_pourcentage=entrees.part_msp_pourcentage,
            verification=entrees.verification,
        ),
        "recommandation": section_recommandation(entrees.recommandation),
        "approbation": section_approbation(
            approbation=entrees.approbation,
            recommandation=entrees.recommandation,
            genere_le=entrees.horodatage,
        ),
    }


def _construire_sections(entrees: EntreesPreuve) -> dict[str, dict[str, Any]]:
    """Construit les neuf sections, dans l'ordre de la chaîne et sans en oublier."""
    construites: dict[str, dict[str, Any]] = {}
    construites.update(_sections_mesurees(entrees))
    construites.update(_sections_decidees(entrees))
    manquantes = [nom for nom in ORDRE_SECTIONS if nom not in construites]
    if manquantes:
        raise ValidationError("sections manquantes au dossier de preuve", sections=manquantes)
    return {nom: construites[nom] for nom in ORDRE_SECTIONS}
