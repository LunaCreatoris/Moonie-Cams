---
title: Ajouter à un client
description: Ajouter une copie à un client ONVIF par son adresse IP, ce que le client voit ensuite, et lire son flux en RTSP.
order: 2
---

## Avant d'ajouter {#before-you-add}

- La copie doit avoir une sortie `onvif` et une adresse `ip` sur le même réseau local que le client.
- Gardez l'heure de l'hôte juste (NTP) : les clients ONVIF peuvent vérifier l'heure de la caméra.
- En [mode proxy](proxy-mode.md), retirez d'abord de chaque client la vraie caméra s'il la lisait en direct : la copie
  tient la seule session RTSP de chaque flux.

## Ajouter par adresse IP {#add-by-ip}

1. Dans le client, ajoutez une caméra ONVIF par son adresse : celle de la copie, ONVIF sur le port 80.
2. Donnez le compte de la copie :
    - en mode par défaut ou copie : l'utilisateur `cams` et le mot de passe de `secrets/NOM_OUTPUT_PASSWORD` ;
    - en mode proxy : le compte de la vraie caméra.

Cams ne fait pas de WS-Discovery : une copie n'apparaît pas toujours seule dans la recherche du client, l'ajout par
adresse IP marche toujours.

## Ce que le client voit {#what-the-client-sees}

- **Le nom** : en mode par défaut ou copie, la copie s'annonce `cams-NOM`, fabricant `Moonie-Cams`, modèle `Cams`,
  le même nom que `docker ps` et `just logs`. En mode proxy, le client voit le modèle et le micrologiciel de la vraie
  caméra, avec l'adresse et la MAC de la copie.
- **Les flux** : chaque flux est un profil ONVIF. Avec `source_sub`, le client a aussi un flux de basse qualité.
- **La taille, la cadence et le débit** : ceux du flux réel quand il tourne, sinon les valeurs `size`, `fps` et
  `bitrate` de la caméra. Un flux MJPEG réencodé est limité à 10 images par seconde et 600 kbit/s par défaut.
- **Hors ligne** : quand la vraie caméra tombe, la copie s'éteint aussi, et le client la voit déconnectée plutôt
  qu'en ligne sans image. Voir [Le miroir d'état](state-mirror.md).

Certains clients gardent les valeurs d'un profil vues à l'ajout. Si les flux, la taille ou le débit changent, retirez
la caméra du client et ajoutez-la de nouveau. Voir [Dépannage](troubleshooting.md#cached-profile-values).

## Lire le flux en RTSP {#read-over-rtsp}

Un client RTSP lit la copie sans passer par ONVIF :

- en mode par défaut ou copie, `rtsp://cams:MOT_DE_PASSE@198.51.100.20:554/NOM` (`NOM_sub` pour le petit flux) ;
- en mode proxy, avec le compte de la vraie caméra, ou par ONVIF pour avoir aussi le pan-tilt et les événements. Voir
  [Le mode proxy](proxy-mode.md).
