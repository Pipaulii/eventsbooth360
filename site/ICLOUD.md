# Agenda iCloud EventsBooth360

Les secrets sont uniquement dans Render : `ICLOUD_USERNAME` (compte Apple),
`ICLOUD_APP_PASSWORD` (mot de passe pour application), `ICLOUD_CALENDAR_NAME`
(optionnel, défaut `EventsBooth360`). Activer avec `ICLOUD_SYNC_ENABLED=true`.
Ne jamais utiliser le mot de passe principal Apple ni publier les secrets.

Le calendrier doit exister dans iCloud, avec un nom unique. Il peut être partagé
avec la famille. Les événements des calendriers Travail et Perso ne sont pas lus.
Les événements occupés du calendrier choisi bloquent le site, avec les marges
d'installation existantes. Les événements transparents/libres et annulés sont
ignorés. Les récurrences sont développées par CalDAV ; une récurrence non résolue
ferme la disponibilité plutôt que de la déclarer libre.

Le calendrier public lit iCloud à la demande, avec un cache de 60 secondes.
Chaque tentative de réservation relit iCloud avant de poser son blocage dans la
base. iCloud et PostgreSQL ne partagent pas une transaction : éviter de créer
une indisponibilité dans Apple pendant qu'un client finalise ce même créneau.
Les réservations sur le site sont sérialisées dans PostgreSQL.

Les réservations confirmées sont exportées avec un UID stable. La table
`calendar_exports` conserve la file d'attente. Le webhook Stripe exporte après
la confirmation en base ; une panne iCloud renvoie 503 pour provoquer une
nouvelle livraison Stripe. Les consultations publiques reprennent également
les exports en attente. Render gratuit ne tourne pas en permanence : aucune
promesse de synchronisation en arrière-plan à fréquence fixe.

L'événement exporté contient seulement l'horaire, la durée et une référence.
Aucune adresse, identité ou information de paiement client n'est transmise.
Modifier ou supprimer cet événement dans Apple ne modifie pas une location
payée dans la base. Les annulations et déplacements de réservations payées
nécessitent une procédure séparée ; ils ne sont pas automatisés ici.

Pour désactiver l'intégration : `ICLOUD_SYNC_ENABLED=false` puis redéployer.
Le site conserve alors uniquement ses réservations internes ; les périodes
manuelles présentes seulement dans Apple ne sont plus prises en compte.

Vérification : `python site/test_apple_calendar.py` et
`python site/test_payments.py`. La validation réelle exige l'accès au calendrier
et un événement temporaire clairement identifié, à supprimer ensuite.
