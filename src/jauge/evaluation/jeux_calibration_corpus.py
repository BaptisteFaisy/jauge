"""Corpus synthétique du jeu de calibration : les textes, rien d'autre.

Ce module ne contient que des **données** : les requêtes, les réponses et la
raison de chaque vérité. Aucun calcul, aucune dépendance : la logique vit
dans `jauge.evaluation.jeux_calibration`.

Tout est **fictif et hors ligne** : aucun texte client, aucun appel réseau,
aucun modèle. Les identifiants de modèles du jeu sont eux-mêmes fictifs, et
servent à vérifier que l'aveuglement les efface avant la notation.

Références : §13.3 (équivalence fonctionnelle), §13.5 (fiabilité du juge),
§21 (risque n° 1 « le modèle juge se trompe »), ENF-IA-01.
"""

from __future__ import annotations

#: Paires **équivalentes** : même service rendu, formulation différente.
#: (identifiant, requête, réponse de référence, réponse candidate, justification)
EQUIVALENTS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "eq-01",
        "Explique au client que sa facture de mars est prête.",
        "Votre facture de mars est disponible dans votre espace client.",
        "Vous pouvez consulter votre facture de mars depuis votre espace client.",
        "Les deux réponses rendent le même service : la facture est consultable.",
    ),
    (
        "eq-02",
        "Explique au client que le remboursement a été effectué.",
        "Le remboursement de 49,90 € a été effectué sur votre compte.",
        "Nous avons remboursé 49,90 € sur votre compte.",
        "Même information, même chiffre : le remboursement a bien eu lieu.",
    ),
    (
        "eq-03",
        "Explique au client que son code promo n'est pas appliqué.",
        "Votre code promo n'a pas été appliqué à cette commande.",
        "Le code promo n'a pas été pris en compte sur cette commande.",
        "Même constat : le code promo n'a pas servi sur cette commande.",
    ),
    (
        "eq-04",
        "Explique au client que l'application est en maintenance.",
        "L'application est momentanément en maintenance.",
        "Une maintenance est actuellement en cours sur l'application.",
        "Même information : l'application n'est pas disponible.",
    ),
    (
        "eq-05",
        "Explique au client que sa livraison aura du retard.",
        "Votre livraison aura du retard.",
        "Le transporteur nous a indiqué que votre livraison serait livrée avec un "
        "retard de quelques jours ; nous vous tenons informés dès que le suivi "
        "est mis à jour.",
        "Même information malgré une longueur différente : la livraison est retardée.",
    ),
    (
        "eq-06",
        "Explique au client que son mot de passe a été réinitialisé.",
        "Votre mot de passe a été réinitialisé.",
        "Nous avons réinitialisé votre mot de passe à votre demande ; un message "
        "de confirmation vous a été envoyé à l'adresse e-mail associée à votre "
        "compte.",
        "Même information malgré une longueur différente : le mot de passe a changé.",
    ),
)

#: Paires **non équivalentes** : service rendu différent de la référence.
#: (identifiant, requête, référence, candidat, justification, type de tâche)
NON_EQUIVALENTS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "ne-01",
        "Classe ce ticket : « Je n'arrive plus à payer ma facture du mois dernier. »",
        '{"etiquette": "facturation"}',
        '{"etiquette": "technique"}',
        "L'étiquette servie est fausse : le ticket relève de la facturation.",
        "classification",
    ),
    (
        "ne-02",
        "Extrais le montant de la commande.",
        '{"montant": 49.9, "devise": "EUR"}',
        '{"montant": 499.0, "devise": "EUR"}',
        "Le montant extrait est faux : 499,00 € au lieu de 49,90 €.",
        "extraction",
    ),
    (
        "ne-03",
        "Explique au client que sa facture de mars est prête.",
        "Votre facture de mars est disponible dans votre espace client.",
        "La météo de votre région est plutôt clémente cette semaine.",
        "La réponse ne répond pas à la demande : elle est hors sujet.",
        "redaction",
    ),
    (
        "ne-04",
        "Explique au client que le remboursement a été effectué.",
        "Le remboursement de 49,90 € a été effectué sur votre compte.",
        "C'est fait.",
        "La réponse est incomplète : elle n'apporte ni le montant ni le compte crédité.",
        "redaction",
    ),
    (
        "ne-05",
        "Classe ce ticket : « Mon code promo ne marche pas à la caisse. »",
        '{"etiquette": "promotion"}',
        '{"etiquette": "technique", "justification": "Le message évoque un échec à '
        "l'étape du paiement, ce qui pourrait aussi relever d'un incident technique "
        "de l'application ; l'intention du client reste toutefois floue et "
        "l'étiquette la plus probable est technique.\"}",
        "L'étiquette servie est fausse, et la réponse candidate est la plus longue "
        "des deux : c'est ce qui en fait un piège de verbosité.",
        "classification",
    ),
)

#: **Items-pièges dorés** : un cas, une réponse correcte, une réponse fausse.
#: (identifiant, requête, bonne réponse, mauvaise réponse, type de tâche, justification)
PIEGES_DORES: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "piege-01",
        "Classe ce ticket : « Ma carte a été refusée au moment du paiement. »",
        '{"etiquette": "paiement"}',
        '{"etiquette": "facturation"}',
        "classification",
        "L'étiquette « facturation » est fausse : le motif est le paiement refusé.",
    ),
    (
        "piege-02",
        "Extrais le montant et la devise de la facture.",
        '{"montant": 129.9, "devise": "EUR"}',
        '{"montant": 129.9, "devise": "USD"}',
        "extraction",
        "La devise servie est fausse : la facture est en euros.",
    ),
    (
        "piege-03",
        "Explique au client que son abonnement se renouvelle chaque mois.",
        "Votre abonnement se renouvelle automatiquement chaque mois.",
        "Votre abonnement se renouvelle automatiquement chaque année.",
        "redaction",
        "La périodicité servie est fausse : c'est mensuel, pas annuel.",
    ),
)

#: Paires de **qualité identique**, présentées dans les deux ordres.
#: (identifiant, requête, réponse A, réponse B)
PAIRES_POSITION: tuple[tuple[str, str, str, str], ...] = (
    (
        "pos-01",
        "Explique au client que sa facture de mars est prête.",
        "Votre facture de mars est disponible dans votre espace client.",
        "Vous pouvez consulter votre facture de mars depuis votre espace client.",
    ),
    (
        "pos-02",
        "Explique au client que le remboursement a été effectué.",
        "Le remboursement de 49,90 € a été effectué sur votre compte.",
        "Nous avons remboursé 49,90 € sur votre compte.",
    ),
    (
        "pos-03",
        "Explique au client que l'application est en maintenance.",
        "L'application est momentanément en maintenance.",
        "Une maintenance est actuellement en cours sur l'application.",
    ),
    (
        "pos-04",
        "Explique au client que son mot de passe a été réinitialisé.",
        "Votre mot de passe a été réinitialisé.",
        "Nous avons réinitialisé votre mot de passe à votre demande.",
    ),
)

#: Paires de **verbosité** : la réponse candidate est la plus longue et la
#: moins bonne.
#: (identifiant, requête, référence courte et juste, candidate longue et fausse,
#:  justification, type de tâche)
PAIRES_VERBOSITE: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "vb-01",
        "Classe ce ticket : « Mon code promo ne marche pas à la caisse. »",
        '{"etiquette": "promotion"}',
        '{"etiquette": "technique", "justification": "Le client mentionne un code '
        "promo, mais l'échec survient au moment du paiement : l'étiquette la plus "
        "probable est donc technique, et la longueur de la justification ne change "
        'rien à la justesse de la réponse."}',
        "La réponse candidate est la plus longue et la moins bonne : un juge qui "
        "privilégie la longueur accepte à tort.",
        "classification",
    ),
    (
        "vb-02",
        "Classe ce ticket : « Je n'ai pas reçu ma commande. »",
        '{"etiquette": "retour"}',
        '{"etiquette": "compte", "justification": "Le client évoque une commande non '
        "reçue, ce qui pourrait relever d'un problème de compte ou d'un retard de "
        "livraison ; l'explication détaillée couvre les deux hypothèses et retient "
        'finalement le compte."}',
        "La réponse candidate est la plus longue et la moins bonne : un juge qui "
        "privilégie la longueur accepte à tort.",
        "classification",
    ),
    (
        "vb-03",
        "Explique au client que le remboursement a été effectué.",
        "Le remboursement de 49,90 € a été effectué sur votre compte.",
        "Après avoir examiné attentivement l'intégralité de votre dossier, de "
        "l'historique de vos échanges avec notre service client et des conditions "
        "générales de vente, nous devons vous informer que la demande de "
        "remboursement de 49,90 € n'a pas pu être acceptée à ce stade et qu'il "
        "convient de saisir à nouveau nos équipes par un autre canal afin "
        "d'obtenir une examination complémentaire.",
        "La réponse candidate est la plus longue et elle inverse la vérité : le "
        "remboursement a bien été effectué.",
        "redaction",
    ),
    (
        "vb-04",
        "Explique au client que sa livraison aura du retard.",
        "Votre livraison aura du retard.",
        "Le transporteur nous a transmis un avis de retard embryonnaire sur "
        "l'acheminement de votre colis, et il convient désormais de patienter "
        "avec attention, le temps que les opérations logistiques réorganisent "
        "les tournées qui ont été perturbées cette semaine.",
        "La réponse candidate est la plus longue et elle reste imprécise sur le retard annoncé.",
        "redaction",
    ),
)

#: Paires de **format** : même contenu, présentation différente.
#: (identifiant, requête, réponse A, réponse B, type de tâche, justification)
PAIRES_FORMAT: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "fmt-01",
        "Classe ce ticket : « Je n'arrive plus à payer ma facture du mois dernier. »",
        '{"etiquette": "facturation"}',
        '{\n  "etiquette": "facturation"\n}',
        "classification",
        "Même étiquette, même service : seul le nombre d'espaces change.",
    ),
    (
        "fmt-02",
        "Classe ce ticket : « Mon code promo ne marche pas à la caisse. »",
        '{"etiquette": "promotion"}',
        '{"etiquette":"promotion"}',
        "classification",
        "Même étiquette, même service : seule la mise en forme du JSON change.",
    ),
    (
        "fmt-03",
        "Explique au client que sa facture de mars est prête.",
        "Votre facture de mars est disponible dans votre espace client.",
        "**Votre facture de mars est disponible dans votre espace client.**",
        "redaction",
        "Même contenu, même service : seule la mise en forme diffère.",
    ),
    (
        "fmt-04",
        "Explique au client que le remboursement a été effectué.",
        "Le remboursement de 49,90 € a été effectué sur votre compte.",
        "- remboursement : 49,90 €\n- état : effectué\n- compte : crédité",
        "redaction",
        "Même contenu, même service : seule la présentation en liste change.",
    ),
)
