package by.solovei.feed

import by.solovei.schedule.data.ScheduleJson
import by.solovei.schedule.data.ScheduleParser
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.Charset
import java.time.Instant
import java.time.temporal.ChronoUnit

/**
 * Готовое расписание для сайта, iPhone и Telegram из Google-таблиц факультета — тем же разбором, что в Android.
 * Источник — sources/<id>.json: { "adapter": "kotlin_gsheet_grid", "page": "<страница со ссылками «1 курс»…>" }.
 * Пишет feeds/<id>/<курс>.json (формат soloweek-schedule) и feeds/<id>/index.json (soloweek-feed).
 * Аргументы: <id> <папка feeds> [<папка sources>]
 */
fun main(args: Array<String>) {
    if (args.firstOrNull() == "term") return term(args.drop(1))
    val id = args.getOrNull(0) ?: error("нужен id, например bsu-physics")
    val feeds = File(args.getOrElse(1) { "../feeds" })
    val sources = File(args.getOrElse(2) { "../sources" })
    val src = JSONObject(File(sources, "$id.json").readText())
    val page = src.getString("page")
    val out = File(feeds, id).apply { mkdirs() }

    // страница факультета из-за границы часто не открывается (геоблокировка) — тогда запасной список из sources
    val fallback = src.optJSONArray("courses")?.let { arr -> (0 until arr.length()).map { arr.getJSONObject(it).let { o -> o.getString("name") to o.getString("sheet") } } }.orEmpty()
    val found = runCatching { discover(fetchText(page)) }.onFailure { println("$id: страница не открылась (${it.javaClass.simpleName}) — беру запасной список") }.getOrDefault(emptyList())
    val courses = found.ifEmpty { fallback }
    println("$id: таблиц ${courses.size}")
    val index = JSONArray()
    var changed = false
    for ((name, sheet) in courses) {
        val slug = slug(name) ?: continue
        val schedule = runCatching {
            download("https://docs.google.com/spreadsheets/d/$sheet/export?format=xlsx").inputStream().use(ScheduleParser::parse)
        }.onFailure { println("  $name: не разобрать — ${it.message}") }.getOrNull() ?: continue
        if (schedule.weeks.isEmpty()) { println("  $name: пусто"); continue }
        // отметка stamp/app в фиде не нужна — приложение сверяет её только у своего кеша
        val text = JSONObject(ScheduleJson.encode(schedule, 0, 0)).apply { remove("source") }.toString()
        val file = File(out, "$slug.json")
        if (!file.exists() || file.readText() != text) { file.writeText(text); changed = true }
        val groups = schedule.weeks.maxOf { it.groups.size }
        index.put(JSONObject().put("name", name).put("id", "$id/$slug").put("groups", groups))
        println("  $name → $slug.json: недель ${schedule.weeks.size}, групп $groups")
    }
    if (index.length() == 0) error("$id: ни одной таблицы не разобрали — feeds не трогаю")
    val indexFile = File(out, "index.json")
    val old = runCatching { JSONObject(indexFile.readText()).getJSONArray("courses").toString() }.getOrNull()
    if (changed || old != index.toString()) {
        indexFile.writeText(
            JSONObject().put("format", "soloweek-feed").put("version", 1).put("id", id).put("courses", index)
                .put("updatedAt", Instant.now().truncatedTo(ChronoUnit.SECONDS).toString()).toString(1),
        )
        println("$id: обновлено")
    } else println("$id: без изменений")
}

/** «1 курс» → «1», «Магистратура 1 курс» → «m1»; прочие ссылки (без номера курса) пропускаем. */
fun slug(name: String): String? {
    val n = Regex("""(\d)""").find(name)?.value ?: return null
    return if (name.contains("магистр", ignoreCase = true)) "m$n" else n
}

private val LINK = Regex(
    """<a\b[^>]*?href\s*=\s*["']([^"']*docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]{20,})[^"']*)["'][^>]*>(.*?)</a>""",
    setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
)

/** Ссылки на Google-таблицы со страницы факультета — как CourseDiscovery в приложении. */
fun discover(html: String): List<Pair<String, String>> {
    val seen = LinkedHashMap<String, String>()
    for (m in LINK.findAll(html)) {
        if (m.groupValues[1].contains("/spreadsheets/d/e/")) continue
        val name = m.groupValues[3].replace(Regex("<[^>]+>"), " ").replace("&nbsp;", " ").replace("&amp;", "&")
            .replace(Regex("""\s+"""), " ").trim()
        if (name.isNotEmpty() && m.groupValues[2] !in seen) seen[m.groupValues[2]] = name
    }
    return seen.map { (sheet, name) -> name to sheet }
}

private fun open(url: String): HttpURLConnection = (URL(url).openConnection() as HttpURLConnection).apply {
    connectTimeout = 15_000; readTimeout = 60_000; instanceFollowRedirects = true
    setRequestProperty("User-Agent", "Mozilla/5.0 SoloWay-feed")
}

fun download(url: String): ByteArray {
    val c = open(url)
    try {
        if (c.responseCode != 200) error("ответ ${c.responseCode}")
        return c.inputStream.use { it.readBytes() }
    } finally { c.disconnect() }
}

private fun fetchText(url: String): String {
    val c = open(url)
    try {
        if (c.responseCode != 200) error("страница ответила ${c.responseCode}")
        val bytes = c.inputStream.use { it.readBytes() }
        val cs = Regex("""charset\s*=\s*["']?([\w-]+)""", RegexOption.IGNORE_CASE)
        val declared = cs.find(c.contentType.orEmpty())?.groupValues?.get(1)
            ?: cs.find(String(bytes, 0, minOf(bytes.size, 2048), Charsets.ISO_8859_1))?.groupValues?.get(1)
        return String(bytes, runCatching { Charset.forName(declared ?: "UTF-8") }.getOrDefault(Charsets.UTF_8))
    } finally { c.disconnect() }
}
