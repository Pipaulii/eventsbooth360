# EventsBooth360 — première version

Site responsive noir et doré. Calendrier public, prestations de 2 à 4 heures. Paiement différé à la demande du propriétaire.

## Démarrer

Python 3.10 ou plus, sans dépendances : `python server.py serve` depuis ce dossier. Ouvrir http://127.0.0.1:3600.

## Renseigner le planning

Avant d'activer les disponibilités, reporter toutes les réservations existantes :

`python server.py block 2026-10-17T17:00 2026-10-17T20:00`

`python server.py list`

`python server.py unblock 1`

Quand le planning est complet : `python server.py activate`.

Pour masquer les disponibilités : `python server.py deactivate`.

Les horaires sont en heure locale Europe/Paris. Horaires provisoires : 10 h–23 h, marge de 30 minutes avant/après, départs toutes les 30 minutes. À confirmer avant activation. Les blocages sont persistants dans planning.sqlite3. L'API publique ne retourne que les disponibilités, jamais les informations privées. Les modifications de planning se font uniquement en local, aucune API d'administration publique.

## Ce qui reste avant production

Connexion Google Calendar, administration authentifiée, demandes/confirmations de réservation, tarifs définitifs, contacts, médias autorisés (image actuelle : illustration extraite de la maquette), domaine/hébergement HTTPS, pages légales avec données réelles et SEO complet. Stripe et le paiement seront ajoutés plus tard. Pas de collecte de données personnelles ni d'encaissement dans cette version.

Le serveur est un aperçu local, pas un serveur de production. Le site n'est pas publié. Les outils de publication Sites ne sont pas accessibles dans cette session. Ne pas exposer directement le serveur Python sur Internet.

## Paiement Stripe — préparation en mode test

Intégration Checkout avec acompte 30 % ou total, prix recalculés côté serveur, retenue transactionnelle du créneau, facturation Stripe du montant encaissé et webhook signé/idempotent. Les plages sont libérées sur événement Stripe d'expiration/échec, jamais sur simple retour navigateur. Les réponses API et erreurs ne contiennent pas de clés.

1. Installer : `python -m pip install --target .vendor -r requirements.txt`.
2. Renseigner `.env` privé : STRIPE_SECRET_KEY (préférer une clé restreinte rk_test_ avec les permissions Checkout/Customers/Invoices nécessaires), STRIPE_WEBHOOK_SECRET et SITE_URL. Les clés live sont refusées. Checkout hébergé n'a pas besoin de clé publique.
3. Installer/authentifier la CLI officielle Stripe et transférer les notifications locales : `stripe listen --forward-to http://127.0.0.1:3600/api/stripe/webhook`. Copier le secret whsec_ affiché par la CLI dans `.env`. Garder la CLI ouverte pendant les tests. En production, utiliser un endpoint HTTPS et son propre secret ; la CLI locale n'est pas une solution de production.
4. Renseigner le planning et l'activer seulement après vérification. Le bouton dépend de Stripe configuré ET du planning activé.
5. Relancer le serveur puis actualiser. Tester avec des coordonnées fictives et une carte de test Stripe.

Tests automatisés : `python test_payments.py` depuis la racine du projet. API Stripe simulée ; signature des webhooks réellement vérifiée avec le SDK. Aucun test réel de bout en bout tant que les clés/webhooks ne sont pas configurés.

Limites avant lancement : interface administration, gestion des retenues en revue après erreur réseau ambiguë, réconciliation périodique Stripe, e-mails, Google Calendar, frais de déplacement, conditions de réservation, conformité et hébergement de production. Une facture d'acompte ne facture que l'acompte ; le solde ne fait pas encore l'objet d'un encaissement automatique. Les détails privés sont conservés en SQLite local hors dossier public. Ne pas débloquer manuellement une plage rattachée à une session Stripe encore active.

## Passage au reel prepare

La configuration active .env est maintenant STRIPE_MODE=live. Ancienne configuration test sauvegardee dans .env.test, ignoree par Git. Serveur relance sans --test avec le planning reel, non active. Le listener local de test est arrete. Les paiements restent desactives en absence de cle live, webhook live et origine publique HTTPS. Configurer un endpoint Stripe live /api/stripe/webhook sur le futur hebergement ; son secret differe de celui de la CLI de test. Cette preparation ne constitue pas une mise en production complete.

## Deploiement gratuit Render + Neon

Creer un Web Service Render lie au depot GitHub, region Frankfurt, offre Free. Root Directory : site. Build Command : pip install -r requirements.txt. Start Command : gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60. Health Check : /health.

Configurer les variables dans Render, jamais dans Git : STRIPE_MODE=live, SITE_URL (adresse publique HTTPS), DATABASE_URL (connexion PostgreSQL Neon avec SSL), STRIPE_SECRET_KEY (cle de production), STRIPE_WEBHOOK_SECRET (secret du webhook de production). Le fichier render.yaml preconfigure ces champs sans aucun secret.

Neon Free conserve les donnees independamment des redemarrages de Render. SQLite est conserve pour le developpement local uniquement. L'adaptateur PostgreSQL utilise un verrou transactionnel partage pour serialiser les retenues de creneaux ; connexion reelle et tests de concurrence PostgreSQL restent a verifier avec la base du proprietaire.

Le site peut etre publie avec calendrier non verifie et paiement desactive avant la configuration. Ne pas declarer la reservation operationnelle tant que le planning n'est pas renseigne, active, et le webhook de production verifie. Google Calendar et interface admin ne sont pas encore realises.
