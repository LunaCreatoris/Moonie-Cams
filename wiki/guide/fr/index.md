---
title: Moonie Cams
description: Fait de chaque caméra, Tapo, ESP32-CAM ou webcam OctoPrint, une caméra ONVIF qu'UniFi Protect adopte et que Home Assistant lit en même temps.
github: https://github.com/LunaCreatoris/Moonie-Cams
order: 4
---

## Ce que fait Cams {#what-cams-does}

Un flux de caméra entre dans un format et ressort dans d'autres. L'usage principal : faire passer n'importe quelle
caméra pour une caméra ONVIF qu'UniFi Protect sait adopter, tout en donnant son flux RTSP à Home Assistant.

Tout le travail vidéo est fait par [go2rtc](https://github.com/AlexxIT/go2rtc), avec son ffmpeg. Chaque caméra a son
propre conteneur go2rtc, sa propre adresse sur le réseau local, et une seule connexion à la vraie caméra, partagée par
tous les clients. Cams ne fait que transformer un fichier `cameras.yaml` en une configuration par caméra et un fichier
compose. Rien n'est enregistré ni stocké.

## Les trois modes {#modes}

- **Par défaut** : la caméra ressort dans d'autres formats (ONVIF, RTSP, MJPEG, HLS, WebRTC, RTMP). Une source MJPEG
  (ESP32-CAM, OctoPrint) est réencodée en H.264 pour Protect.
- **Proxy** : pour une Tapo, la copie se présente comme la caméra elle-même. Protect et Home Assistant la partagent,
  pan-tilt et événements compris. Voir [Partager une Tapo](tapo-proxy.md).
- **Copie** : un simple répartiteur, une connexion à la source copiée vers chaque sortie, chacune isolée des autres.
  Voir [Le mode copie](copy-mode.md).

Dans tous les modes, une copie s'éteint tant que sa caméra est hors ligne, et revient avec elle. Voir
[Le miroir d'état](state-mirror.md).

## Les pages du guide {#pages}

- [Démarrer](getting-started.md) : décrire les caméras, ranger les mots de passe, lancer les copies.
- [Adopter dans UniFi Protect](unifi-protect.md) : l'adoption par adresse IP et ce que Protect affiche.
- [Partager une Tapo](tapo-proxy.md) : le mode proxy, l'arbitrage du pan-tilt et le journal.
- [Le mode copie](copy-mode.md) : un flux partagé par plusieurs clients.
- [Le miroir d'état](state-mirror.md) : une copie hors ligne quand sa caméra l'est.
- [Dépannage](troubleshooting.md) : les problèmes déjà rencontrés et leur solution.

## La référence technique {#reference}

Tous les réglages, les correctifs de go2rtc, les limites et les vérifications sont décrits dans le
[README du dépôt](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md), en anglais. Ce qui est prévu
pour la suite est dans la [feuille de route](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/ROADMAP.md).
