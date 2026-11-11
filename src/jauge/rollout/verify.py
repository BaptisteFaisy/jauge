"""Vérification de l'économie à volume constant, 30 jours après la bascule.

Répond aux exigences :
- EF-VER-01 : comparer le coût par requête avant et après la bascule, **à volume
  constant** (c'est le volume observé après qui sert de base commune, §4).
- EF-VER-02 : confirmer l'économie sur les dollars réellement facturés, lus par
  étiquette dans CUR 2.0 (lecture faite en amont par le module d'attribution).
- ENF-IA-01 : tous les chiffres viennent de `jauge.engine.stats` et des agrégats
  du store ; aucun n'est produit par un modèle.

Le résultat publié est `jauge.models.recommendation.VerificationEconomie` ; la
confirmation par étiquette est un complément local (`ConfirmationFacture`), le
modèle du socle ne portant pas encore ces champs (besoin signalé en PR).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from jauge.common.errors import CloisonnementError, ValidationError
from jauge.engine.stats import baisse_pourcentage, economie_mensuelle
from jauge.models.cost import AgregatQuotidien
from jauge.models.recommendation import Recommandation, VerificationEconomie
from jauge.models.tenant import Application

# Écart toléré entre économie constatée et économie facturée (points de %).
TOLERANCE_FACTURE_PAR_DEFAUT = 10.0


@dataclass(frozen=True)
class ConfirmationFacture:
    """Confirmation de l'économie sur la facture, par étiquette (EF-VER-02)."""

    tenant_id: str
    application_id: str
    economie_facturee_usd: float
    economie_attendue_usd: float
    ecart_pourcentage: float
    etiquettes: tuple[str, ...]
    conforme: bool


def cout_par_requete(agregats: Sequence[AgregatQuotidien], tenant_id: str) -> float:
    """Coût moyen d'une requête sur un ensemble d'agrégats (Annexe D, agrégé).

    Lève `ValidationError` si le volume est nul : un coût par requête calculé sur
    zéro requête serait faux en silence.
    """
    if not agregats:
        raise ValidationError("Aucun agrégat fourni pour calculer un coût par requête.")
    hors_client = [a for a in agregats if a.tenant_id != tenant_id]
    if hors_client:
        raise CloisonnementError(
            "Agrégats d'un autre client dans le calcul de vérification.",
            tenant_id=tenant_id,
            applications=[a.application_id for a in hors_client],
        )
    volume = sum(a.nombre_requetes for a in agregats)
    if volume <= 0:
        raise ValidationError(
            "Volume nul sur la période : impossible de comparer à volume constant.",
            tenant_id=tenant_id,
        )
    cout = sum(a.cout_recalcule_usd for a in agregats)
    return round(cout / volume, 8)


def volume(agregats: Sequence[AgregatQuotidien]) -> int:
    """Nombre de requêtes observées sur une période."""
    return sum(a.nombre_requetes for a in agregats)


def ecart_relatif(constatee: float, attendue: float) -> float:
    """Écart en % de l'économie constatée par rapport à l'attendue."""
    if attendue <= 0:
        return 0.0
    return round((constatee - attendue) / attendue * 100.0, 2)


def verifier_economie(
    tenant_id: str,
    application: Application,
    recommandation: Recommandation,
    agregats_avant: Sequence[AgregatQuotidien],
    agregats_apres: Sequence[AgregatQuotidien],
) -> VerificationEconomie:
    """Compare l'économie constatée à l'économie attendue, à volume constant.

    - `agregats_avant` : période de référence avant bascule (30 jours) ;
    - `agregats_apres` : même durée après la bascule ; son volume sert de base
      commune aux deux calculs (EF-VER-01, « à volume constant »).
    """
    if application.tenant_id != tenant_id or recommandation.tenantId != tenant_id:
        raise CloisonnementError(
            "Application ou recommandation hors du client demandé.",
            tenant_id=tenant_id,
            application_id=application.id,
        )
    avant = cout_par_requete(agregats_avant, tenant_id)
    apres = cout_par_requete(agregats_apres, tenant_id)
    volume_periode = volume(agregats_apres)
    constatee = economie_mensuelle(avant, apres, volume_periode)
    attendue = economie_mensuelle(
        recommandation.cost.costPerRequestBefore,
        recommandation.cost.costPerRequestAfter,
        volume_periode,
    )
    return VerificationEconomie(
        application_id=application.id,
        recommandation_id=recommandation.id,
        cout_par_requete_avant=avant,
        cout_par_requete_apres=apres,
        volume_periode=volume_periode,
        economie_constatee_usd=constatee,
        economie_attendue_usd=attendue,
        ecart_pourcentage=ecart_relatif(constatee, attendue),
    )


def baisse_observee(verification: VerificationEconomie) -> float:
    """Baisse du coût par requête en %, indicateur principal du §4."""
    return baisse_pourcentage(
        verification.cout_par_requete_avant, verification.cout_par_requete_apres
    )


def confirmer_par_etiquette(
    tenant_id: str,
    application_id: str,
    dollars_avant: Mapping[str, float],
    dollars_apres: Mapping[str, float],
    economie_attendue_usd: float,
    tolerance_pourcentage: float = TOLERANCE_FACTURE_PAR_DEFAUT,
) -> ConfirmationFacture:
    """Confirme l'économie sur les dollars facturés par étiquette (EF-VER-02).

    Les deux dictionnaires viennent de CUR 2.0, déjà restreints au client
    (`tenant_id`) : la clé est une **valeur d'étiquette d'une seule dimension**
    (par exemple `application`), la valeur le montant facturé sur la période.
    Additionner plusieurs dimensions compterait deux fois la même dépense. Une
    étiquette absente d'une des deux périodes est traitée comme 0 $.
    """
    if not dollars_avant and not dollars_apres:
        raise ValidationError(
            "Aucune ligne facturée par étiquette fournie.", application_id=application_id
        )
    for montant in list(dollars_avant.values()) + list(dollars_apres.values()):
        if montant < 0:
            raise ValidationError(
                "Montant facturé négatif : la lecture CUR 2.0 est incohérente.",
                application_id=application_id,
            )
    etiquettes = tuple(sorted(set(dollars_avant) | set(dollars_apres)))
    facturee = round(
        sum(dollars_avant.get(e, 0.0) for e in etiquettes)
        - sum(dollars_apres.get(e, 0.0) for e in etiquettes),
        2,
    )
    ecart = ecart_relatif(facturee, economie_attendue_usd)
    return ConfirmationFacture(
        tenant_id=tenant_id,
        application_id=application_id,
        economie_facturee_usd=facturee,
        economie_attendue_usd=round(economie_attendue_usd, 2),
        ecart_pourcentage=ecart,
        etiquettes=etiquettes,
        conforme=abs(ecart) <= tolerance_pourcentage,
    )
