#!/bin/zsh
# Копирует разбор таблиц из Android-кода SoloWay (папка beta рядом) — запускать после правок парсера в приложении.
set -e
SRC=${1:-../../beta/app/src/main/java/by/solovei/schedule/data}
DST=src/main/kotlin/by/solovei/schedule/data
cd "$(dirname "$0")"
for f in Models.kt ScheduleParser.kt XlsxReader.kt ScheduleJson.kt; do cp "$SRC/$f" "$DST/$f"; done
echo "скопировано из $SRC"
