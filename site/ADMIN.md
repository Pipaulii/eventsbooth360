# Administration EventsBooth360

L’espace privé est disponible à `/admin`, sans lien dans la navigation publique.

## Créer le premier compte

Dans Render → Environment, ajouter `ADMIN_EMAIL` (adresse du propriétaire) et
`ADMIN_PASSWORD` (mot de passe unique d’au moins 16 caractères), puis enregistrer
et déployer. Ouvrir `/admin` initialise le seul compte propriétaire dans PostgreSQL.
Le mot de passe est enregistré avec un hash scrypt, jamais en clair dans la base.
Après la première connexion réussie, supprimer `ADMIN_PASSWORD` de Render : le
compte continue à fonctionner avec le hash conservé dans PostgreSQL.
Les variables d’initialisation ne remplacent pas un compte existant.

La session expire après 30 minutes. Cookie HttpOnly, Secure en HTTPS, SameSite
Strict, vérification d’origine et jeton CSRF pour les actions. Les tentatives sont
limitées par adresse et globalement sur dix minutes. Aucun compte ne peut être
créé depuis un formulaire public. Les pages et API privées sont no-store et noindex.

## Annuler et rembourser

La confirmation rembourse le paiement de la session Checkout : acompte ou total,
pas le solde encaissé séparément. Stripe doit autoriser la lecture des sessions,
PaymentIntents et charges ainsi que la création et lecture des remboursements.
Une clé restreinte sans ces permissions laisse l’opération à vérifier, sans
libérer le créneau sur une erreur Stripe.

Une intention durable est enregistrée avant l’appel Stripe. Une clé
d’idempotence stable et la vérification des montants déjà remboursés protègent
les reprises. Les remboursements partiels externes nécessitent une intervention
dans Stripe. Un remboursement accepté mais en attente est affiché comme tel ;
son statut est relu lors de l’actualisation de l’administration. Un échec ultérieur
doit être traité dans Stripe, et reste visible dans l’administration.

L’annulation conserve la réservation et les horaires, libère le créneau en base
et supprime l’événement Apple identifié par son UID. Les erreurs Apple sont
réessayées lors d’une consultation de l’administration ; tant que l’événement
Apple existe encore, il peut continuer à bloquer la disponibilité publique.
Les webhooks tardifs ne peuvent pas réactiver une réservation annulée.

Les tests utilisent une base temporaire et des doubles Stripe/iCloud. Aucun
remboursement réel n’est exécuté pour valider le développement.
