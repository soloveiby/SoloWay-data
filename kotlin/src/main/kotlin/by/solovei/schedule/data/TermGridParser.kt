package by.solovei.schedule.data

import java.io.InputStream
import java.time.DayOfWeek
import java.time.Duration
import java.time.LocalDate
import java.time.LocalTime
import java.time.temporal.TemporalAdjusters

/**
 * Разборщик «Excel-сетки на семестр» (тип источника `xlsx-term`): один файл — курс, листы — потоки.
 *
 * На листе недели идут колонками слева направо («I неделя», «II неделя»…), под каждой — группы («гр.1д2601ГУП»),
 * а когда ширины не хватает — вторым таким же блоком ниже. Строки — дни и пары, слева «День недели / Время занятий».
 * В ячейке — сокращение, вид занятия и аудитория: «ФИЛ (сз) а.708»; общая лекция потока — ячейка на все группы недели.
 * Своё время пишут в начале («9.00 КЧ а.1005», «8.40 Зачет ТГП а.808»). Внизу листа — таблица
 * «Изучаемые дисциплины и преподаватели»: сокращение → полное название и кто что ведёт.
 */
object TermGridParser {

    fun parse(input: InputStream): Schedule = parse(XlsxReader.read(input))

    fun parse(sheets: List<Sheet>): Schedule {
        val parsed = sheets.mapNotNull { runCatching { parseSheet(it) }.getOrNull() }.filter { it.groups.isNotEmpty() }
        if (parsed.isEmpty()) return Schedule(emptyList())

        // потоки одного курса — одно расписание: группы всех листов подряд
        val groups = parsed.flatMap { it.groups }.distinctBy { it.name }
            .mapIndexed { i, g -> Group(i, g.name, g.code, g.specialty) }
        val index = groups.associate { it.name to it.index }

        val weeks = parsed.flatMap { it.weeks }.groupBy { it.monday }.toSortedMap().map { (monday, parts) ->
            val days = (0 until 6).map { d ->
                val date = monday.plusDays(d.toLong())
                val per = HashMap<Int, MutableList<Lesson>>()
                for (p in parts) for (e in p.entries) {
                    if (e.date != date) continue
                    val shared = e.groups.mapNotNull { index[it] }
                    for (g in shared) per.getOrPut(g) { ArrayList() } += e.lesson.copy(sharedWith = shared)
                }
                Day(date, per.mapValues { (_, v) -> v.sortedBy { it.start } })
            }
            Week("Неделя ${parts.first().number}", groups, days)
        }
        return Schedule(weeks)
    }

    // ───────────────────────── лист ─────────────────────────

    private class Entry(val date: LocalDate, val groups: List<String>, val lesson: Lesson)
    private class WeekPart(val monday: LocalDate, val number: Int, val entries: List<Entry>)
    private class SheetResult(val groups: List<Group>, val weeks: List<WeekPart>)
    private class WeekCols(val number: Int, val dateCol: Int, val cols: List<Int>, val names: List<String>, val monday: LocalDate?)
    private class Slot(val start: LocalTime, val end: LocalTime)

    private const val MAX_COL = 320
    private val WEEK = Regex("""^([IVXL]+|\d{1,2})\s*недел[яи]$""", RegexOption.IGNORE_CASE)
    // «гр.1д2601ГУП» (ГУП) или просто «1д2601УИР» (ГУЭ, УИР — у них шапка без «гр.»)
    private val GROUP = Regex("""^(?:гр\.?\s*(\S.*)|(\d{1,2}[а-яё]{1,2}\d{3,5}[а-яёa-z]{2,6}[\w-]*))$""", RegexOption.IGNORE_CASE)
    private fun groupOf(text: String): String? = GROUP.find(text.squash())?.let { m -> m.groupValues[1].ifEmpty { m.groupValues[2] }.squash() }
    private val CODE = Regex("""\d-\d{2}-\d{4}-\d{2}""")
    private val DAY_NAMES = listOf("понедельник", "вторник", "среда", "четверг", "пятница", "суббота")
    private val TIME = Regex("""(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})""")
    private val DATE = Regex("""(\d{1,2})\.(\d{1,2})\.(\d{4})""")
    private val WS = Regex("""\s+""")
    private fun String.squash() = replace(WS, " ").trim()

    private fun parseSheet(sheet: Sheet): SheetResult? {
        val headers = (0 until sheet.rowCount).filter { r -> (0..MAX_COL).any { WEEK.matches(sheet.text(r, it).squash()) } }
        if (headers.isEmpty()) return null
        val legendRow = (headers.last() until sheet.rowCount).firstOrNull { sheet.text(it, 0).contains("Изучаемые дисциплины", true) } ?: sheet.rowCount
        val legend = Legend.read(sheet, legendRow)

        // шапка листа: специальность, лекционная аудитория, где проходят лабораторные
        var code = ""; var specialty = ""; var lectureRoom = emptyList<String>()
        val labRooms = HashMap<String, List<String>>()
        for (r in 0 until headers.first()) for (c in 0..MAX_COL) {
            val t = sheet.text(r, c).squash()
            if (t.isEmpty()) continue
            if (code.isEmpty()) CODE.find(t)?.let { code = it.value; specialty = t.replace(it.value, "").squash() }
            if (t.startsWith("Лекционная аудитория", true)) {
                val own = t.substringAfter(':', "").squash()
                lectureRoom = rooms(own.ifEmpty { (c + 1..c + 6).map { sheet.text(r, it).squash() }.firstOrNull { it.isNotEmpty() }.orEmpty() })
            }
            LAB_NOTE.find(t)?.let { labRooms[it.groupValues[1]] = rooms(it.groupValues[2]) }
            // ГУЭ, УИР: «Лабораторные занятия:», ниже строками «сокращение … аудитории»
            if (t.equals("Лабораторные занятия:", true) || t.equals("Лабораторные и практические занятия:", true)) {
                for (rr in r..minOf(r + 8, headers.first() - 1)) {
                    val abbr = (c + 1..c + 2).map { sheet.text(rr, it).squash() }.firstOrNull { it.isNotEmpty() && !it.first().isDigit() } ?: if (rr == r) continue else break
                    val where = (c + 2..c + 6).map { sheet.text(rr, it).squash() }.firstOrNull { it.isNotEmpty() && it.first().isDigit() } ?: continue
                    labRooms[abbr] = rooms(where)
                }
            }
        }

        val groups = LinkedHashMap<String, Group>()
        val parts = ArrayList<WeekPart>()
        for ((bi, h) in headers.withIndex()) {
            val bandEnd = minOf(headers.getOrNull(bi + 1) ?: sheet.rowCount, legendRow)
            val groupRow = (h + 1..minOf(h + 5, bandEnd - 1)).firstOrNull { r -> (0..MAX_COL).any { groupOf(sheet.text(r, it)) != null } } ?: continue
            val dayCol = (0..5).firstOrNull { sheet.text(h, it).startsWith("День", true) } ?: 0
            val timeCol = (0..5).firstOrNull { sheet.text(h, it).startsWith("Время", true) } ?: (dayCol + 1)

            val weeks = (0..MAX_COL).mapNotNull { c ->
                val m = WEEK.find(sheet.text(h, c).squash()) ?: return@mapNotNull null
                val cols = generateSequence(c) { it + 1 }.takeWhile { groupOf(sheet.text(groupRow, it)) != null }.toList()
                if (cols.isEmpty()) return@mapNotNull null
                val names = cols.map { groupOf(sheet.text(groupRow, it))!! }
                // «01.09.2026 - 05.09.2026» под названием недели
                val range = (h + 1 until groupRow).firstNotNullOfOrNull { r -> DATE.find(sheet.text(r, c))?.let(::date) }
                WeekCols(weekNumber(m.groupValues[1]), c - 1, cols, names, range?.with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY)))
            }
            weeks.flatMap { it.names }.forEach { n -> groups.getOrPut(n) { Group(groups.size, n, code, specialty) } }

            // строки блока: день недели и время пары
            val rowDay = HashMap<Int, Int>(); val rowSlot = HashMap<Int, Slot>(); val dayFirstRow = HashMap<Int, Int>()
            var day = -1
            for (r in groupRow + 1 until bandEnd) {
                val name = sheet.text(r, dayCol).lowercase()
                DAY_NAMES.indexOfFirst { name.startsWith(it) }.takeIf { it >= 0 }?.let { day = it; dayFirstRow[it] = r }
                if (day < 0) continue
                val times = TIME.findAll(sheet.text(r, timeCol)).toList()
                if (times.isEmpty()) continue
                val a = times.first().groupValues; val b = times.last().groupValues
                rowDay[r] = day
                rowSlot[r] = Slot(LocalTime.of(a[1].toInt(), a[2].toInt()), LocalTime.of(b[3].toInt(), b[4].toInt()))
            }

            for (w in weeks) {
                // дата дня — из колонки «Дата» (число Excel), иначе считаем от понедельника недели
                fun dateOf(d: Int): LocalDate? =
                    dayFirstRow[d]?.let { excelDate(sheet.text(it, w.dateCol)) }?.takeIf { it.dayOfWeek.value == d + 1 } ?: w.monday?.plusDays(d.toLong())
                val monday = w.monday ?: (0 until 6).firstNotNullOfOrNull { d -> dateOf(d)?.minusDays(d.toLong()) } ?: continue
                val entries = ArrayList<Entry>()
                for ((r, d) in rowDay) for ((i, c) in w.cols.withIndex()) {
                    val text = sheet.text(r, c).squash()
                    if (text.isEmpty() || sheet.isCovered(r, c)) continue
                    val span = sheet.span(r, c)
                    val names = w.cols.withIndex().filter { it.value in span.c1..span.c2 }.map { w.names[it.index] }.ifEmpty { listOf(w.names[i]) }
                    val slot = rowSlot.getValue(r)
                    val lastRow = (span.r2 downTo r).firstOrNull { rowDay[it] == d } ?: r
                    val lesson = cell(text, slot, rowSlot[lastRow]?.end?.takeIf { lastRow != r }, names, legend, lectureRoom, labRooms) ?: continue
                    entries += Entry(dateOf(d) ?: monday.plusDays(d.toLong()), names, lesson)
                }
                parts += WeekPart(monday, w.number, entries)
            }
        }
        return SheetResult(groups.values.toList(), parts)
    }

    // ───────────────────────── ячейка ─────────────────────────

    private val LAB_NOTE = Regex("""Лабораторные занятия по дисциплине\s+(\S+)\s+а\.\s*(.+)$""", RegexOption.IGNORE_CASE)
    private val OWN_TIME = Regex("""^(\d{1,2})[.:](\d{2})\s+""")
    private val ROOM = Regex("""\s*а(?:уд)?\.\s*([^()]+?)\s*\)?\s*$""", RegexOption.IGNORE_CASE)
    private val KIND = Regex("""\(\s*(лаб|л|сз|пз)\.?\s*\)?""", RegexOption.IGNORE_CASE)
    private val CHECK = Regex("""^(диф\.?\s*зач[её]т|зач[её]т|экзамен|консультация)\s+""", RegexOption.IGNORE_CASE)

    private val BARE_ROOM = Regex(""",\s*(?:а\s+)?(\d{3,4}(?:\s*,\s*\d{3,4})*)\s*$""")  // «, 912», «, а 708»
    /** Сокращение для поиска в списке дисциплин: без пробелов, переносов и точки в конце («Введ_в_⏎спец.» = «Введ_в_спец»). */
    private fun key(abbr: String) = abbr.lowercase().replace(WS, "").trimEnd('.')

    private fun rooms(raw: String): List<String> = raw.split(',', ';').map { it.squash() }.filter { it.isNotEmpty() }

    private fun cell(
        raw: String, slot: Slot, mergedEnd: LocalTime?, groups: List<String>,
        legend: Legend, lectureRoom: List<String>, labRooms: Map<String, List<String>>,
    ): Lesson? {
        var text = raw
        // своё время начала: «9.00 КЧ», «8.40 Зачет ТГП»
        val own = OWN_TIME.find(text)?.let { m -> text = text.substring(m.range.last + 1); LocalTime.of(m.groupValues[1].toInt(), m.groupValues[2].toInt()) }
        val start = own ?: slot.start
        val end = mergedEnd ?: if (own != null) own.plus(Duration.between(slot.start, slot.end)) else slot.end

        var room = ROOM.find(text)?.let { m -> text = text.removeRange(m.range); rooms(m.groupValues[1]) }
            // ГУЭ: аудитория без «а.» — «ПЛ (сз), 912»
            ?: BARE_ROOM.find(text)?.let { m -> text = text.removeRange(m.range); rooms(m.groupValues[1]) } ?: emptyList()
        val kind = KIND.find(text)?.let { m -> text = text.removeRange(m.range); m.groupValues[1].lowercase() }
        val check = CHECK.find(text.squash())?.let { m -> text = text.squash().substring(m.range.last + 1); m.groupValues[1].lowercase().replace("зачет", "зачёт").squash() }
        // «БелЯ (пз), а.912» — после вида занятия запятая перед аудиторией
        val abbr = text.squash().trim('(', ')', ' ', '.', ',', ';')
        if (abbr.isEmpty()) return null

        val type = when (kind) { "л" -> LessonType.LECTURE; "сз" -> LessonType.SEMINAR; "пз" -> LessonType.PRACTICE; "лаб" -> LessonType.LAB; else -> LessonType.OTHER }
        if (room.isEmpty() && check == null) room = when (type) {
            LessonType.LECTURE -> lectureRoom
            LessonType.LAB -> labRooms[abbr] ?: emptyList()
            else -> emptyList()
        }
        val subject = legend.subjects[key(abbr)]
        val key = when { check != null -> if ("зач" in check) "зач" else if ("экз" in check) "экз" else null; else -> kind }
        return Lesson(
            start = start, end = if (end > start) end else slot.end.coerceAtLeast(start),
            title = subject?.name ?: abbr, type = type,
            teachers = subject?.teachers(key, groups) ?: emptyList(),
            rooms = room, tags = listOfNotNull(check), details = emptyList(),
            cancelled = false, sharedWith = emptyList(),
        )
    }

    // ───────────────────────── дисциплины и преподаватели ─────────────────────────

    /** Кто ведёт предмет: вид занятий («л», «сз», «пз», «лаб», «зач», «экз»; «*» — любые) → группы (пусто — все). */
    private class Teacher(val name: String, val scope: Map<String, Set<String>>)

    private class Subject(val name: String, val staff: List<Teacher>) {
        fun teachers(kind: String?, groups: List<String>): List<String> {
            fun Teacher.fits(k: String) = scope[k]?.let { it.isEmpty() || it.any { g -> g in groups } } == true
            val exact = if (kind == null) emptyList() else staff.filter { it.fits(kind) }
            // зачёт без отдельной пометки принимает лектор
            val picked = exact.ifEmpty { staff.filter { it.scope.isEmpty() || it.fits("*") } }
                .ifEmpty { if (kind == "зач" || kind == "экз") staff.filter { it.fits("л") } else emptyList() }
            return picked.map { it.name }.distinct()
        }
    }

    private class Legend(val subjects: Map<String, Subject>) {
        companion object {
            private val NAME = Regex("""[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.""")

            fun read(sheet: Sheet, top: Int): Legend {
                if (top >= sheet.rowCount) return Legend(emptyMap())
                val head = top + 1..minOf(top + 3, sheet.rowCount - 1)
                fun col(word: String) = head.firstNotNullOfOrNull { r -> (0..MAX_COL).firstOrNull { sheet.text(r, it).contains(word, true) } }
                val abbrCol = col("Сокр") ?: 1
                val nameCol = col("Полное") ?: (abbrCol + 1)
                val staffCol = col("преподавател")
                val known = (0 until top).flatMap { r -> (0..MAX_COL).mapNotNull { groupOf(sheet.text(r, it)) } }.distinct()
                val out = HashMap<String, Subject>()
                // данные — сразу под строкой «Сокр. название / Полное название»
                val first = (head.firstOrNull { r -> sheet.text(r, abbrCol).contains("Сокр", true) } ?: top) + 1
                for (r in first until sheet.rowCount) {
                    val abbr = sheet.text(r, abbrCol).squash()
                    val name = sheet.text(r, nameCol).squash()
                    if (abbr.isEmpty() || name.isEmpty()) { if (out.isNotEmpty()) break else continue }
                    out[key(abbr)] = Subject(name, staffCol?.let { staff(sheet.text(r, it), known) } ?: emptyList())
                }
                return Legend(out)
            }

            /** «доцент Иванов И.И. (л, сз - 1д2601ГУП, зачет), преподаватель Петров П.П. (сз - 1д2602ГУП)» */
            private fun staff(text: String, groups: List<String>): List<Teacher> = NAME.findAll(text).map { m ->
                var i = m.range.last + 1
                while (i < text.length && text[i].isWhitespace()) i++
                val scope = LinkedHashMap<String, MutableSet<String>>()
                if (i < text.length && text[i] == '(') {
                    var depth = 0; var j = i
                    while (j < text.length) { if (text[j] == '(') depth++ else if (text[j] == ')' && --depth == 0) break; j++ }
                    scope(text.substring(i + 1, minOf(j, text.length)), groups, scope)
                }
                Teacher(m.value.squash(), scope)
            }.toList()

            /** Одни скобки: «л, (сз, пз - 1д2601ГУП), лаб.зан., экзамен» — группы после тире относятся к видам занятий перед ним. */
            private fun scope(text: String, groups: List<String>, out: MutableMap<String, MutableSet<String>>) {
                val parts = ArrayList<String>(); val sb = StringBuilder(); var depth = 0
                for (ch in text) when {
                    ch == '(' -> { if (depth++ > 0) sb.append(ch) else { parts += sb.toString(); sb.setLength(0); sb.append('\u0000') } }
                    ch == ')' -> { if (--depth > 0) sb.append(ch) else { parts += sb.toString(); sb.setLength(0) } }
                    (ch == ',' || ch == ';') && depth == 0 -> { parts += sb.toString(); sb.setLength(0) }
                    else -> sb.append(ch)
                }
                parts += sb.toString()
                val pending = ArrayList<String>(); var closed = false
                for (p in parts) {
                    if (p.startsWith('\u0000')) { scope(p.substring(1), groups, out); continue }
                    val head = p.substringBefore('-').squash().lowercase()
                    val kind = when {
                        head.startsWith("лаб") -> "лаб"; head == "л" || head.startsWith("лек") -> "л"
                        head.startsWith("сз") || head.startsWith("сем") -> "сз"; head.startsWith("пз") || head.startsWith("пр") -> "пз"
                        "зач" in head -> "зач"; head.startsWith("экз") -> "экз"; else -> null
                    }
                    val named = groups.filter { it in p }
                    if (kind != null) { if (closed) { pending.clear(); closed = false }; pending += kind; out.getOrPut(kind) { LinkedHashSet() } }
                    if (named.isNotEmpty()) {
                        if (pending.isEmpty()) out.getOrPut("*") { LinkedHashSet() } += named
                        else pending.forEach { out.getValue(it) += named }
                        closed = true
                    }
                }
            }
        }
    }

    // ───────────────────────── мелочи ─────────────────────────

    private fun date(m: MatchResult): LocalDate? = runCatching { LocalDate.of(m.groupValues[3].toInt(), m.groupValues[2].toInt(), m.groupValues[1].toInt()) }.getOrNull()

    /** Дата, как её хранит Excel: число дней от 30.12.1899. */
    private fun excelDate(raw: String): LocalDate? =
        raw.trim().toDoubleOrNull()?.toLong()?.takeIf { it in 30_000..80_000 }?.let { LocalDate.of(1899, 12, 30).plusDays(it) }
            ?: DATE.find(raw)?.let(::date)

    private val ROMAN = mapOf('I' to 1, 'V' to 5, 'X' to 10, 'L' to 50)
    private fun weekNumber(raw: String): Int = raw.toIntOrNull() ?: run {
        var total = 0
        val v = raw.uppercase().map { ROMAN[it] ?: 0 }
        for (i in v.indices) total += if (i + 1 < v.size && v[i] < v[i + 1]) -v[i] else v[i]
        total
    }
}
