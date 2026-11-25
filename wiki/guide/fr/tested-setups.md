---
title: Cas testés
description: Les clients et les sources réellement essayés avec Cams, avec leurs étapes concrètes.
order: 7
---

## Ce que contient cette page {#about}

Le reste du guide décrit Cams de façon générique : n'importe quelle source RTSP ou MJPEG, n'importe quel client ONVIF
ou RTSP. Cette page rassemble les clients et les sources qui ont été essayés pour de vrai avec Cams, avec les étapes
propres à chaque produit. Les adresses et les noms sont des exemples.

## UniFi Protect {#unifi-protect}

Protect ajoute une copie comme caméra ONVIF tierce. Les étapes générales sont dans
[Ajouter à un client](onvif-clients.md).

### Adopter une copie {#unifi-protect-adopt}

1. Dans Protect, Réglages, Système, activez « Discover Third-Party Cameras ».
2. Choisissez « Advanced Adoption » et donnez l'adresse de la copie (ONVIF sur le port 80).
3. Donnez le compte de la copie : l'utilisateur `cams` et le mot de passe de `secrets/NOM_OUTPUT_PASSWORD`.

Cams ne fait pas de WS-Discovery : une copie n'apparaît pas toujours seule dans la liste, l'adoption par adresse IP
marche toujours.

### Adopter une copie en mode proxy {#unifi-protect-adopt-proxied}

1. Si la vraie caméra était adoptée en direct dans Protect, retirez-la d'abord : la copie tient la seule session RTSP
   de chaque flux.
2. Adoptez la copie par son adresse, comme plus haut, avec le compte de la vraie caméra (pour une Tapo, son compte
   caméra, voir [Tapo](#tapo)).

### Ce que Protect affiche {#unifi-protect-what-it-shows}

- **Le nom** : en mode par défaut ou copie, la copie s'annonce `cams-NOM`, fabricant `Moonie-Cams`, modèle `Cams`.
  En mode proxy, Protect voit le modèle et le micrologiciel de la vraie caméra, avec l'adresse et la MAC de la copie.
- **Les flux** : chaque flux est un profil ONVIF. Avec `source_sub`, Protect a son flux de basse qualité.
- **Hors ligne** : quand la vraie caméra tombe, la copie s'éteint aussi, et Protect la montre déconnectée plutôt
  qu'en ligne sans image. Voir [Le miroir d'état](state-mirror.md).

### Les particularités connues {#unifi-protect-quirks}

- **Des valeurs gardées** : Protect garde les valeurs d'un profil vues à l'adoption. Si les flux, la taille, la
  cadence ou le débit changent, retirez la caméra de Protect et adoptez-la de nouveau. Voir
  [Dépannage](troubleshooting.md#cached-profile-values).
- **Une image verte** : Protect ne décodait que la première tranche de l'encodeur H.264 par défaut, et le bas d'un
  flux MJPEG réencodé restait vert. C'est réglé : chaque configuration force le profil Main, une seule tranche par
  image. Si l'image reste verte, relancez `just up`. Voir [Dépannage](troubleshooting.md#green-picture).

## Home Assistant {#home-assistant}

Une copie sert Home Assistant en même temps que les autres clients.

### Lire une copie en RTSP {#home-assistant-rtsp}

En mode par défaut ou copie, ajoutez une caméra générique (ou toute intégration RTSP) sur
`rtsp://cams:MOT_DE_PASSE@198.51.100.20:554/NOM`, `NOM_sub` pour le petit flux.

### Ajouter une copie en ONVIF {#home-assistant-onvif}

En mode proxy, ajoutez l'intégration ONVIF pointée sur la copie (port 80 ou 2020) avec le compte de la vraie caméra :
elle a les flux, le pan-tilt et les événements. Si un autre client bouge déjà la caméra, les commandes de pan-tilt
de Home Assistant reçoivent une erreur le temps de `ptz_hold`. Voir
[L'arbitrage du pan-tilt](proxy-mode.md#ptz-arbitration).

## Tapo {#tapo}

Les caméras Tapo sont celles pour lesquelles le [mode proxy](proxy-mode.md) a été écrit.

### Ce qu'il faut savoir {#tapo-facts}

- Une Tapo n'accepte qu'une connexion par flux : `stream1` pour le flux principal, `stream2` pour le petit.
- Son ONVIF répond sur le port 2020.
- Un seul compte sert pour RTSP et ONVIF : le compte caméra créé dans l'application Tapo, distinct du compte de
  l'application.

### Configurer une Tapo en mode proxy {#tapo-configure}

```yaml
cameras:
  - name: salon
    input: rtsp
    source: rtsp://cams:${SALON_SOURCE_PASSWORD}@192.0.2.11:554/stream1
    source_sub: rtsp://cams:${SALON_SOURCE_PASSWORD}@192.0.2.11:554/stream2
    outputs: [onvif]
    ip: 198.51.100.22
    mode: proxy
    onvif_url: http://192.0.2.11:2020
```

Les clients donnent à la copie le compte caméra de la Tapo, pour ONVIF comme pour RTSP.

### Partager une Tapo entre Protect et Home Assistant {#tapo-protect-and-home-assistant}

1. Retirez la Tapo de Protect si elle y était adoptée en direct, puis adoptez la copie. Voir
   [Adopter une copie en mode proxy](#unifi-protect-adopt-proxied).
2. Dans Home Assistant, ajoutez l'intégration ONVIF pointée sur la copie. Voir
   [Ajouter une copie en ONVIF](#home-assistant-onvif).
3. L'intégration Tapo de HACS peut garder la vraie caméra pour ses réglages en plus (mode privé, alarme, voyant),
   mais coupez son flux ou ses entités caméra : sinon elle prend la seule session de la Tapo.

### Une Tapo coupe le flux dès sa lecture {#tapo-stale-session}

**Ce qu'on voit** : le compte caméra est accepté, mais la Tapo met fin à la lecture aussitôt, et le journal de la
copie montre une fin de flux (`EOF`) qui revient en boucle.

**La cause** : une ancienne session tient encore `stream1`, par exemple celle d'un client qui lisait la Tapo en
direct avant la copie, ou de l'entité caméra de l'intégration Tapo.

**La solution** : retirez de la Tapo tout autre client, puis redémarrez-la. Vérifiez aussi que son mode privé est
coupé : il arrête le flux. Voir [Dépannage](troubleshooting.md#stale-source-session).

## OctoPrint {#octoprint}

La webcam d'une imprimante 3D sous OctoPrint sert un flux MJPEG en HTTP. Cams en fait une caméra ONVIF.

### Configurer {#octoprint-configure}

```yaml
cameras:
  - name: imprimante
    input: mjpeg
    source: http://192.0.2.30/webcam/?action=stream
    outputs: [onvif]
    ip: 198.51.100.21
```

- `source` est l'adresse du flux lui-même, `/webcam/?action=stream`, pas celle de l'interface d'OctoPrint ni celle
  d'une image fixe (`?action=snapshot`).
- Le flux MJPEG est réencodé en H.264 pour la sortie ONVIF, à 10 images par seconde et 600 kbit/s par défaut ;
  `fps` et `bitrate` changent ces valeurs.
- Une sortie `mjpeg` est refusée, sauf en [mode copie](copy-mode.md) : c'est déjà le format d'entrée.
