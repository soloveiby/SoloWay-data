package by.solovei.schedule.data

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.LocalDate
import java.time.LocalTime

/**
 * Собственный формат расписания SoloWay — «исходный вид», в который приводится любая таблица.
 *
 * Зачем:
 *  1. Быстрый запуск: разбирать xlsx долго, а этот файл читается в разы быстрее. Он лежит рядом
 *     с xlsx и пересоздаётся, только когда меняется таблица или версия приложения.
 *  2. Общий формат: таблицу другого факультета или курса достаточно один раз привести к этому виду —
 *     дальше приложение работает с ней так же, как с нашей.
 *
 * Пример:
 * {
 *   "format": "soloweek-schedule", "version": 1,
 *   "source": { "stamp": 123, "app": 36 },
 *   "weeks": [{
 *     "title": "17–22 ноября",
 *     "groups": [{ "index": 0, "name": "Группа 1", "code": "6-05-0533-01", "specialty": "Физика", "profiles": [] }],
 *     "days": [{
 *       "date": "2026-11-18",
 *       "lessons": { "0": [{
 *         "start": "08:15", "end": "09:50", "title": "Математический анализ", "type": "LECTURE",
 *         "teachers": ["доц. Иванов И.И."], "rooms": ["321"], "tags": [], "details": [],
 *         "cancelled": false, "sharedWith": [0, 1]
 *       }]}
 *     }]
 *   }]
 * }
 */
object ScheduleJson {
    const val FORMAT = "soloweek-schedule"
    const val VERSION = 1

    fun encode(schedule: Schedule, stamp: Long, app: Int): String = JSONObject()
        .put("format", FORMAT).put("version", VERSION)
        .put("source", JSONObject().put("stamp", stamp).put("app", app))
        .put("weeks", JSONArray().apply { schedule.weeks.forEach { put(week(it)) } })
        .toString()

    /** null — файл не наш, другой версии или от другой таблицы/версии приложения: тогда разбираем xlsx заново. */
    fun decode(text: String, stamp: Long, app: Int): Schedule? = runCatching {
        val o = JSONObject(text)
        if (o.optString("format") != FORMAT || o.optInt("version") != VERSION) return null
        val src = o.optJSONObject("source")
        if (src == null || src.optLong("stamp") != stamp || src.optInt("app") != app) return null
        val weeks = o.getJSONArray("weeks")
        Schedule((0 until weeks.length()).map { week(weeks.getJSONObject(it)) })
    }.getOrNull()

    /**
     * Готовое расписание от конвертера (репозиторий SoloWay-data): тот же формат, но без отметки
     * stamp/app — его не нужно сверять с исходной таблицей, он сам и есть исходник.
     */
    fun decodeFeed(text: String): Schedule? = runCatching {
        val o = JSONObject(text)
        if (o.optString("format") != FORMAT || o.optInt("version") != VERSION) return null
        val weeks = o.getJSONArray("weeks")
        Schedule((0 until weeks.length()).map { week(weeks.getJSONObject(it)) }.filter { it.days.isNotEmpty() })
    }.getOrNull()

    fun write(file: File, schedule: Schedule, stamp: Long, app: Int) {
        // пишем во временный файл и переименовываем: читатель никогда не увидит недописанный JSON
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(encode(schedule, stamp, app))
        if (!tmp.renameTo(file)) { tmp.copyTo(file, overwrite = true); tmp.delete() }
    }

    fun read(file: File, stamp: Long, app: Int): Schedule? =
        if (file.exists()) runCatching { decode(file.readText(), stamp, app) }.getOrNull() else null

    // ── неделя / группа / день / пара ──

    private fun week(w: Week) = JSONObject()
        .put("title", w.title)
        .put("groups", JSONArray().apply { w.groups.forEach { put(group(it)) } })
        .put("days", JSONArray().apply { w.days.forEach { put(day(it)) } })

    private fun week(o: JSONObject) = Week(
        o.getString("title"),
        o.getJSONArray("groups").let { a -> (0 until a.length()).map { group(a.getJSONObject(it)) } },
        o.getJSONArray("days").let { a -> (0 until a.length()).map { day(a.getJSONObject(it)) } },
    )

    private fun group(g: Group) = JSONObject()
        .put("index", g.index).put("name", g.name).put("code", g.code).put("specialty", g.specialty)
        .put("profiles", strings(g.profiles))

    private fun group(o: JSONObject) = Group(
        o.getInt("index"), o.getString("name"), o.getString("code"), o.getString("specialty"), strings(o.optJSONArray("profiles")),
    )

    private fun day(d: Day) = JSONObject()
        .put("date", d.date.toString())
        .put("lessons", JSONObject().apply { d.lessons.forEach { (g, list) -> put(g.toString(), JSONArray().apply { list.forEach { put(lesson(it)) } }) } })

    private fun day(o: JSONObject): Day {
        val ls = o.getJSONObject("lessons")
        val map = LinkedHashMap<Int, List<Lesson>>()
        ls.keys().forEach { k -> val a = ls.getJSONArray(k); map[k.toInt()] = (0 until a.length()).map { lesson(a.getJSONObject(it)) } }
        return Day(LocalDate.parse(o.getString("date")), map)
    }

    private fun lesson(l: Lesson) = JSONObject()
        .put("start", l.start.toString()).put("end", l.end.toString())
        .put("title", l.title).put("type", l.type.name)
        .put("teachers", strings(l.teachers)).put("rooms", strings(l.rooms))
        .put("tags", strings(l.tags)).put("details", strings(l.details))
        .put("cancelled", l.cancelled)
        .put("sharedWith", JSONArray().apply { l.sharedWith.forEach { put(it) } })

    private fun lesson(o: JSONObject) = Lesson(
        LocalTime.parse(o.getString("start")), LocalTime.parse(o.getString("end")),
        o.getString("title"),
        runCatching { LessonType.valueOf(o.getString("type")) }.getOrDefault(LessonType.OTHER),
        strings(o.optJSONArray("teachers")), strings(o.optJSONArray("rooms")),
        strings(o.optJSONArray("tags")), strings(o.optJSONArray("details")),
        o.optBoolean("cancelled"),
        o.optJSONArray("sharedWith")?.let { a -> (0 until a.length()).map { a.getInt(it) } } ?: emptyList(),
    )

    private fun strings(list: List<String>) = JSONArray().apply { list.forEach { put(it) } }
    private fun strings(a: JSONArray?): List<String> = if (a == null) emptyList() else (0 until a.length()).map { a.getString(it) }
}
