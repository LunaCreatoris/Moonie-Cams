---
title: Moonie Cams
description: Cams traduit et copie les flux de vos caméras.
status: Version 1.0.1
github: https://github.com/LunaCreatoris/Moonie-Cams
order: 4
---

## Ce que fait Cams {#what-cams-does}

Cams traduit et copie les flux de vos caméras. Un flux entre dans un format et ressort dans ceux dont vous avez
besoin : ONVIF, RTSP, MJPEG, HLS, WebRTC ou RTMP. Une seule connexion à la vraie caméra, partagée par tous les clients.

Une caméra qui ne sait servir que du RTSP ou du MJPEG devient ainsi une caméra ONVIF, qu'un client ONVIF ajoute comme n'importe quelle autre, et son flux
reste lisible en RTSP, MJPEG, HLS ou WebRTC en même temps.

Tout le travail vidéo est fait par [go2rtc](https://github.com/AlexxIT/go2rtc), avec son ffmpeg. Chaque caméra a son
propre conteneur go2rtc et sa propre adresse sur le réseau local. Cams ne fait que transformer un fichier `cameras.yaml` en une configuration par caméra et un fichier
compose. Rien n'est enregistré ni stocké.

## Les trois modes {#modes}

- **Par défaut** : la caméra ressort dans d'autres formats (ONVIF, RTSP, MJPEG, HLS, WebRTC, RTMP). Une source MJPEG
  est réencodée en H.264 pour les sorties qui l'exigent.
- **Proxy** : pour une caméra qui a son propre ONVIF, la copie se présente comme la caméra elle-même. Plusieurs
  clients la partagent, pan-tilt et événements compris. Voir [Le mode proxy](proxy-mode.md).
- **Copie** : un simple répartiteur, une connexion à la source copiée vers chaque sortie, chacune isolée des autres.
  Voir [Le mode copie](copy-mode.md).

Dans tous les modes, une copie s'éteint tant que sa caméra est hors ligne, et revient avec elle. Voir
[Le miroir d'état](state-mirror.md).

## Les pages du guide {#pages}

- [Démarrer](getting-started.md) : décrire les caméras, ranger les mots de passe, lancer les copies avec Docker
  Compose, Portainer ou just.
- [Ajouter à un client](onvif-clients.md) : l'ajout par adresse IP, ce que le client voit, la lecture en RTSP.
- [Le mode proxy](proxy-mode.md) : partager une caméra ONVIF, l'arbitrage du pan-tilt et le journal.
- [Le mode copie](copy-mode.md) : un flux partagé par plusieurs clients.
- [Le miroir d'état](state-mirror.md) : une copie hors ligne quand sa caméra l'est.
- [Dépannage](troubleshooting.md) : les problèmes déjà rencontrés et leur solution.
- [Cas testés](tested-setups.md) : les clients et les sources essayés pour de vrai, étape par étape.

## La référence technique {#reference}

Tous les réglages, les correctifs de go2rtc, les limites et les vérifications sont décrits dans le
[README du dépôt](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md), en anglais. Ce qui est prévu
pour la suite est dans la [feuille de route](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/ROADMAP.md).
