---
title: Dépannage
description: Les problèmes déjà rencontrés avec de vraies caméras, et ce qui les a réglés.
order: 6
---

## Une Tapo coupe le flux dès sa lecture {#tapo-stale-session}

**Ce qu'on voit** : le compte caméra est accepté, mais la Tapo met fin à la lecture aussitôt, et le journal de la copie
montre une fin de flux (`EOF`) qui revient en boucle.

**La cause** : une ancienne session tient encore `stream1` sur la Tapo, par exemple celle d'un client qui la lisait en
direct avant la copie. La Tapo n'accepte qu'une connexion par flux.

**La solution** : retirez de la Tapo tout autre client (Protect en direct, l'entité caméra de l'intégration Tapo),
puis redémarrez la caméra. Vérifiez aussi que son mode privé est coupé.

## Un mot de passe avec des caractères spéciaux {#special-characters}

**Ce qu'on voyait** : avec un mot de passe contenant `@`, `^`, `:` ou `/`, go2rtc écrivait `invalid userinfo` et
réessayait sans fin.

**La solution** : c'est réglé. Cams encode désormais le mot de passe dans l'URL de la source, et la caméra le reçoit
tel quel. Seuls `"`, `\`, les caractères de contrôle et les espaces Unicode (comme l'espace insécable) restent
refusés : la copie s'arrête avec une ligne qui nomme le fichier du secret, jamais sa valeur. Changez alors ce mot de passe.

## Une image verte sous la première bande {#green-picture}

**Ce qu'on voyait** : Protect n'affichait correctement que le haut d'un flux MJPEG réencodé, le reste de l'image
était vert.

**La solution** : c'est réglé. Chaque configuration force un encodeur H.264 en profil Main, une seule tranche par
image : Protect ne décodait que la première tranche de l'encodeur par défaut. Si l'image reste verte, relancez
`just up` pour régénérer les copies.

## Protect garde d'anciennes valeurs {#protect-cached-values}

**Ce qu'on voit** : après un changement de taille, de cadence, de débit ou de flux, Protect affiche encore les
anciennes valeurs.

**La cause** : Protect garde les valeurs d'un profil vues à l'adoption.

**La solution** : retirez la caméra de Protect et adoptez-la de nouveau. Voir
[Adopter dans UniFi Protect](unifi-protect.md).

## Plus d'aide {#more-help}

Les limites connues sont dans la section
[Known limits](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#known-limits) du README.
