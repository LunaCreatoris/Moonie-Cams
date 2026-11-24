---
title: Adopter dans UniFi Protect
description: Adopter une copie dans UniFi Protect par son adresse IP, et ce que Protect affiche ensuite.
order: 2
---

## Avant d'adopter {#before-you-adopt}

- La copie doit avoir une sortie `onvif` et une adresse `ip` sur le même réseau local que la console.
- Gardez l'heure de l'hôte juste (NTP) : les clients ONVIF peuvent vérifier l'heure de la caméra.
- Pour une Tapo en [mode proxy](tapo-proxy.md), retirez d'abord la Tapo de Protect si elle y était adoptée en direct :
  la copie tient la seule session RTSP de chaque flux.

## Adopter par adresse IP {#adopt-by-ip}

1. Dans Protect, Réglages, Système, activez « Discover Third-Party Cameras ».
2. Choisissez « Advanced Adoption » et donnez l'adresse de la copie (ONVIF sur le port 80).
3. Donnez le compte de la copie :
    - en mode par défaut ou copie : l'utilisateur `cams` et le mot de passe de `secrets/NOM_OUTPUT_PASSWORD` ;
    - en mode proxy : le compte caméra de la Tapo.

Cams ne fait pas de WS-Discovery : une copie n'apparaît pas toujours seule dans la liste, l'adoption par adresse IP
marche toujours.

## Ce que Protect affiche {#what-protect-shows}

- **Le nom** : en mode par défaut ou copie, la copie s'annonce `cams-NOM`, fabricant `Moonie-Cams`, modèle `Cams`,
  le même nom que `docker ps` et `just logs`. En mode proxy, Protect voit le modèle et le micrologiciel de la vraie
  Tapo, avec l'adresse et la MAC de la copie.
- **Les flux** : chaque flux est un profil ONVIF. Avec `source_sub`, Protect a son flux de basse qualité.
- **La taille, la cadence et le débit** : ceux du flux réel quand il tourne, sinon les valeurs `size`, `fps` et
  `bitrate` de la caméra. Un flux MJPEG réencodé est limité à 10 images par seconde et 600 kbit/s par défaut.
- **Hors ligne** : quand la vraie caméra tombe, la copie s'éteint aussi, et Protect la montre déconnectée plutôt
  qu'en ligne sans image. Voir [Le miroir d'état](state-mirror.md).

Protect garde les valeurs d'un profil vues à l'adoption. Si les flux, la taille ou le débit changent, retirez la
caméra de Protect et adoptez-la de nouveau. Voir [Dépannage](troubleshooting.md#protect-cached-values).

## Home Assistant en même temps {#home-assistant}

Une copie sert aussi Home Assistant :

- en mode par défaut ou copie, une caméra générique ou toute intégration RTSP sur
  `rtsp://cams:MOT_DE_PASSE@198.51.100.20:554/NOM` (`NOM_sub` pour le petit flux) ;
- en mode proxy, l'intégration ONVIF pointée sur la copie. Voir [Partager une Tapo](tapo-proxy.md).
