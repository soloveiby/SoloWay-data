// Разбор Google-таблиц тем же кодом, что в Android-приложении SoloWay (папка by/solovei/schedule/data — копия,
// обновляется скриптом sync.sh). Запуск: ./gradlew -q run --args="bsu-physics ../feeds"
plugins {
    kotlin("jvm") version "2.0.20"
    application
}
dependencies { implementation("org.json:json:20240303") }
kotlin { jvmToolchain(21) }
application { mainClass.set("by.solovei.feed.MainKt") }
