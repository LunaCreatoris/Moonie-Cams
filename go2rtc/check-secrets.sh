#!/bin/sh
# Copyright (C) 2026 LunaCreatoris
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Entrypoint of the go2rtc image, between tini and go2rtc. For every file KEY of
# $CREDENTIALS_DIRECTORY it exports KEY_URL, the value percent-encoded for a URL's account
# (everything but A-Z a-z 0-9 - . _ ~ becomes %XX of its bytes), then execs the command it was
# given (go2rtc's own).
#
# go2rtc replaces ${KEY} in its config text, before parsing it, with the file KEY trimmed of
# surrounding whitespace, else with the environment variable KEY. A camera password holding "@"
# or "/" pasted raw into rtsp://user:${KEY}@host broke the URL ("net/url: invalid userinfo",
# retried forever); app.py writes ${KEY_URL} there instead, which net/url decodes back to the
# real password. A value holding '"', '\' or a control character would still break the config
# text itself, encoded or not where it is pasted raw (the copy's own RTSP account): such a
# secret stops the start with one line naming the file. No value is ever printed.
set -u
LC_ALL=C
export LC_ALL

# The ASCII whitespace Go's strings.TrimSpace removes around a credential
ws=$(printf ' \t\n\v\f\rx')
ws=${ws%x}
# TrimSpace also removes the Unicode spaces, which this script does not trim: a secret holding
# one anywhere is refused rather than trimmed differently. In UTF-8: U+00A0, U+1680,
# U+2000-U+200A, U+2028, U+2029, U+202F, U+205F and U+3000, and the C1 controls U+0080-U+009F
# (U+0085 among them), which YAML cannot hold either. One sequence per word: none holds an IFS byte.
unicode=$(printf '\302\240 \341\232\200 \342\200\250 \342\200\251 \342\200\257 \342\201\237 \343\200\200')
for octal in 200 201 202 203 204 205 206 207 210 211 212; do
    unicode="$unicode $(printf "\\342\\200\\$octal")"
done
for high in 0 1 2 3; do
    for low in 0 1 2 3 4 5 6 7; do
        unicode="$unicode $(printf "\\302\\2$high$low")"
    done
done

dir=${CREDENTIALS_DIRECTORY-}
if [ -n "$dir" ] && [ -d "$dir" ]; then
    for file in "$dir"/*; do
        name=${file##*/}
        # Only a readable file named like a variable can be exported (an empty folder leaves
        # the pattern itself, which is skipped the same way)
        case $name in
            [!A-Za-z_]* | *[!A-Za-z0-9_]*) continue ;;
        esac
        { [ -f "$file" ] && [ -r "$file" ]; } || continue
        bad="secret $name holds a character go2rtc's config cannot hold (\" \\, a control character such as a tab or an inner newline, or a Unicode space such as a no-break space); change it"
        # A NUL byte cannot sit in a shell variable: compare the sizes with and without it
        if [ "$(wc -c < "$file")" -ne "$(tr -d '\000' < "$file" | wc -c)" ]; then
            printf '%s\n' "$bad" >&2
            exit 1
        fi
        value=$(cat -- "$file"; printf x)
        value=${value%x}
        while :; do
            case $value in
                [$ws]*) value=${value#?} ;;
                *) break ;;
            esac
        done
        while :; do
            case $value in
                *[$ws]) value=${value%?} ;;
                *) break ;;
            esac
        done
        case $value in
            *\"* | *\\* | *[[:cntrl:]]*)
                printf '%s\n' "$bad" >&2
                exit 1
                ;;
        esac
        for sequence in $unicode; do
            case $value in
                *"$sequence"*)
                    printf '%s\n' "$bad" >&2
                    exit 1
                    ;;
            esac
        done
        # Percent-encode byte by byte (LC_ALL=C: one character is one byte)
        encoded=""
        rest=$value
        while [ -n "$rest" ]; do
            tail=${rest#?}
            char=${rest%"$tail"}
            rest=$tail
            case $char in
                [A-Za-z0-9._~-]) encoded=$encoded$char ;;
                *) encoded=$encoded%$(printf '%s' "$char" | od -An -tx1 | tr -d ' \n' | tr a-f A-F) ;;
            esac
        done
        export "${name}_URL=$encoded"
    done
fi

exec "$@"
