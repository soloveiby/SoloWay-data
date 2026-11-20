package by.solovei.feed

import by.solovei.schedule.data.ScheduleJson
import by.solovei.schedule.data.TermGridParser
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.time.temporal.ChronoUnit

/**
 * Таблицы «сетка на семестр» с сайтов вузов, которые не пускают серверы (pac.by). Файлы присылают телефоны
 * студентов (server/share на своём сервере), принятые лежат в <accepted>/<вуз>/<курс>.xlsx.
 * Разбор — TermGridParser, тот же, что в приложении. Пишет <feeds>/<вуз>/<курс>.json и index.json.
 * Аргументы: term <вуз> <accepted> <feeds> <sources.json>
 */
fun term(args: List<String>) {
    val (inst, acceptedDir, feedsDir, sourcesFile) = args
    val courses = JSONObject(File(sourcesFile).readText()).getJSONObject(inst).getJSONArray("courses")
    val out = File(feedsDir, inst).apply { mkdirs() }
    val index = JSONArray()
    for (i in 0 until courses.length()) {
        val c = courses.getJSONObject(i)
        val slug = c.getString("slug")
        val file = File(acceptedDir, "$inst/$slug.xlsx")
        if (!file.exists()) { println("  ${c.getString("name")}: файла ещё нет"); continue }
        val schedule = runCatching { file.inputStream().use(TermGridParser::parse) }
            .onFailure { println("  ${c.getString("name")}: не разобрать — ${it.message}") }.getOrNull() ?: continue
        if (schedule.weeks.isEmpty()) { println("  ${c.getString("name")}: пусто"); continue }
        val text = JSONObject(ScheduleJson.encode(schedule, 0, 0)).apply { remove("source") }.toString()
        File(out, "$slug.json").writeText(text)
        index.put(JSONObject().put("name", c.getString("name")).put("id", "$inst/$slug").put("groups", schedule.weeks.maxOf { it.groups.size }))
        println("  ${c.getString("name")} → $slug.json: недель ${schedule.weeks.size}")
    }
    File(out, "index.json").writeText(
        JSONObject().put("format", "soloweek-feed").put("version", 1).put("id", inst).put("courses", index)
            .put("updatedAt", Instant.now().truncatedTo(ChronoUnit.SECONDS).toString()).toString(1),
    )
}
