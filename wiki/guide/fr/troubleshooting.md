---
title: Dépannage
description: Les problèmes déjà rencontrés avec de vraies caméras, et ce qui les a réglés.
order: 6
---

## La caméra coupe le flux dès sa lecture {#stale-source-session}

**Ce qu'on voit** : le compte de la caméra est accepté, mais elle met fin à la lecture aussitôt, et le journal de la
copie montre une fin de flux (`EOF`) qui revient en boucle.

**La cause** : une ancienne session tient encore le flux sur la caméra, par exemple celle d'un client qui la lisait
en direct avant la copie. Une caméra qui n'accepte qu'une connexion par flux refuse alors la copie.

**La solution** : retirez de la caméra tout autre client qui la lit en direct, puis redémarrez-la. Vérifiez aussi
qu'aucun mode privé de la caméra ne coupe son flux. Un cas réel est décrit dans
[Cas testés](tested-setups.md#tapo-stale-session).

## Un mot de passe avec des caractères spéciaux {#special-characters}

**Ce qu'on voyait** : avec un mot de passe contenant `@`, `^`, `:` ou `/`, go2rtc écrivait `invalid userinfo` et
réessayait sans fin.

**La solution** : c'est réglé. Cams encode désormais le mot de passe dans l'URL de la source, et la caméra le reçoit
tel quel. Seuls `"`, `\`, les caractères de contrôle et les espaces Unicode (comme l'espace insécable) restent
refusés : la copie s'arrête avec une ligne qui nomme le fichier du secret, jamais sa valeur. Changez alors ce mot de passe.

## Une image verte sous la première bande {#green-picture}

**Ce qu'on voyait** : un client n'affichait correctement que le haut d'un flux MJPEG réencodé, le reste de l'image
était vert.

**La solution** : c'est réglé. Chaque configuration force un encodeur H.264 en profil Main, une seule tranche par
image : certains décodeurs ne lisaient que la première tranche de l'encodeur par défaut. Si l'image reste verte,
relancez `just up` pour régénérer les copies. Voir aussi [Cas testés](tested-setups.md#unifi-protect-quirks).

## Le client garde d'anciennes valeurs {#cached-profile-values}

**Ce qu'on voit** : après un changement de taille, de cadence, de débit ou de flux, le client affiche encore les
anciennes valeurs.

**La cause** : certains clients ONVIF gardent les valeurs d'un profil vues à l'ajout de la caméra.

**La solution** : retirez la caméra du client et ajoutez-la de nouveau. Voir
[Ajouter à un client](onvif-clients.md).

## Plus d'aide {#more-help}

Les limites connues sont dans la section
[Known limits](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#known-limits) du README.
