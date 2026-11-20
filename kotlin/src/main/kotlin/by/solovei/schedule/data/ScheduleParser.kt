package by.solovei.schedule.data

import java.io.InputStream
import java.time.LocalDate
import java.time.LocalTime

/**
 * Превращает «человеческую» таблицу расписания в структурированные данные.
 *
 * Формат листа: строка-шапка с «Группа N» (справа от каждой — колонка «Ауд.»),
 * слева колонки «ДН / Дата / Время», дни идут блоками. Лекции для нескольких
 * групп — объединённые по горизонтали ячейки, длинные пары — по вертикали.
 */
object ScheduleParser {

    fun parse(input: InputStream): Schedule {
        val sheets = XlsxReader.read(input)
        val dated = sheets.filter { SHEET_RANGE.containsMatchIn(it.name) }
        val weeks = (dated.ifEmpty { sheets })
            .mapNotNull { runCatching { parseWeek(it) }.getOrNull() }
            .filter { it.days.isNotEmpty() }
            .sortedBy { it.start }
            .distinctBy { it.start }
        return Schedule(weeks)
    }

    // ───────────────────────── лист → неделя ─────────────────────────

    private val SHEET_RANGE = Regex("""\d{1,2}\.\d{2}\s*-\s*\d{1,2}\.\d{2}""")
    /** «Группа 3», а на старших курсах радиофака — «1 кафедра (21)». */
    private val GROUP_HEADER = Regex("""^(Группа\s*\d+|\d+\s*кафедра)""", RegexOption.IGNORE_CASE)
    private val STUDENT_COUNT = Regex("""\s*\(\s*\d+\s*\)\s*$""")
    private val CODE = Regex("""\d-\d{2}-\d{4}-\d{2}|\d-\d{2} \d{2} \d{2}(?:-\d{2})?""")
    private val DAY_NAMES = listOf("ПОНЕДЕЛЬНИК", "ВТОРНИК", "СРЕДА", "ЧЕТВЕРГ", "ПЯТНИЦА", "СУББОТА", "ВОСКРЕСЕНЬЕ")
    /** Дни сокращённо — «Пн», «Вт.» (радиофак БГУ). */
    private val DAY_SHORT = Regex("""^(ПН|ВТ|СР|ЧТ|ПТ|СБ|ВС)(?![А-ЯЁ])""")
    private val SHORT_NAMES = listOf("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС")
    /** «16.11-21.11 (12-я неделя)26-27» — начало недели и учебный год из названия листа. */
    private val SHEET_START = Regex("""^\s*(\d{1,2})\.(\d{2})""")
    private val ACADEMIC_YEAR = Regex("""(\d{2})\s*-\s*(\d{2})\s*$""")
    private val TIME_RANGE = Regex("""^(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})""")
    private val FULL_DATE = Regex("""(\d{1,2})\.(\d{1,2})\.(\d{4})""")

    private class Slot(val start: LocalTime, val end: LocalTime)

    private fun parseWeek(sheet: Sheet): Week? {
        val maxCol = 60
        val headerRow = (0 until minOf(sheet.rowCount, 40)).firstOrNull { r ->
            (0..maxCol).any { GROUP_HEADER.containsMatchIn(sheet.text(r, it)) }
        } ?: return null

        val groupCols = (0..maxCol).filter { GROUP_HEADER.containsMatchIn(sheet.text(headerRow, it)) }
        val timeCol = (0 until groupCols.first()).firstOrNull { sheet.text(headerRow, it).startsWith("Время", true) }
            ?: (groupCols.first() - 1)
        val dateCol = (0 until timeCol).firstOrNull { sheet.text(headerRow, it).startsWith("Дата", true) } ?: (timeCol - 1)
        val dayCol = (0 until timeCol).firstOrNull { sheet.text(headerRow, it).startsWith("ДН", true) } ?: 0

        val profileRow = headerRow + 1
        val hasProfiles = (0..timeCol).any { sheet.text(profileRow, it).contains("профил", true) }

        val groups = groupCols.mapIndexed { i, c ->
            val lines = sheet.text(headerRow, c).lines().map { it.trim() }.filter { it.isNotEmpty() }
            val rest = lines.drop(1).joinToString(" ")
            val code = CODE.find(rest)?.value.orEmpty()
            Group(
                index = i,
                // «Группа 1 (23)» — в скобках число студентов, в названии группы оно не нужно
                name = lines.firstOrNull()?.replace(STUDENT_COUNT, "")?.squash() ?: "Группа ${i + 1}",
                code = code,
                specialty = rest.replace(code, "").squash(),
                profiles = if (hasProfiles) sheet.text(profileRow, c).lines()
                    .map { it.squash().replace(Regex("""^((пр|сп)\.\s*\d+\s*)+"""), "").trim() }
                    .filter { it.isNotEmpty() } else emptyList(),
            )
        }

        // 1) размечаем строки: к какому дню относятся и какое у них время
        val rowDay = HashMap<Int, Int>()
        val rowSlot = HashMap<Int, Slot>()
        val dates = ArrayList<LocalDate?>()
        val weekdays = ArrayList<Int>()
        var day = -1
        var lastSlot: Slot? = null
        var prevExplicit = false
        for (r in headerRow + 1 until sheet.rowCount) {
            val dayName = sheet.text(r, dayCol).uppercase().trim()
            val dayIdx = DAY_NAMES.indexOfFirst { dayName.startsWith(it) }
                .takeIf { it >= 0 } ?: DAY_SHORT.find(dayName)?.let { SHORT_NAMES.indexOf(it.groupValues[1]) } ?: -1
            if (dayIdx >= 0) {
                day = dates.size
                weekdays += dayIdx
                dates += parseDate(sheet.text(r, dateCol))
                lastSlot = null
                prevExplicit = false
            }
            if (day < 0) continue
            val slot = parseTimeRange(sheet.text(r, timeCol))
            when {
                slot != null -> { lastSlot = slot; prevExplicit = true }
                // строка без времени сразу под строкой со временем — продолжение той же пары
                prevExplicit && lastSlot != null -> prevExplicit = false
                else -> { lastSlot = null; prevExplicit = false }
            }
            val s = lastSlot ?: continue
            rowDay[r] = day
            rowSlot[r] = s
        }
        fillMissingDates(dates)
        // колонки с датами нет — берём неделю из названия листа и раскладываем дни по их названиям
        if (dates.any { it == null }) mondayFromName(sheet.name)?.let { mon -> for (i in dates.indices) dates[i] = mon.plusDays(weekdays[i].toLong()) }
        if (dates.any { it == null }) return null

        // 2) собираем пары
        val perDay = List(dates.size) { HashMap<Int, MutableList<Lesson>>() }
        for ((r, d) in rowDay) {
            val slot = rowSlot.getValue(r)
            for (gc in groupCols) {
                val text = sheet.text(r, gc)
                if (text.isEmpty() || sheet.isCovered(r, gc)) continue
                val span = sheet.span(r, gc)
                val covered = groupCols.withIndex().filter { it.value in span.c1..span.c2 }.map { it.index }
                val roomCol = groupCols.last { it in span.c1..span.c2 } + 1
                val lastRow = (span.r2 downTo r).firstOrNull { rowDay[it] == d } ?: r
                val end = rowSlot[lastRow]?.end ?: slot.end
                val lessons = parseCell(text, sheet.text(r, roomCol), slot.start, end, covered, dates[d])
                for (g in covered) perDay[d].getOrPut(g) { ArrayList() }.addAll(lessons)
            }
        }
        // одна и та же пара в колонках трёх и больше групп (радиофак повторяет лекцию в каждой колонке) — лекция
        for (day in perDay) {
            val counts = day.values.flatten().filter { it.type == LessonType.OTHER }
                .groupingBy { listOf(it.start, it.title, it.teachers, it.rooms) }.eachCount()
            for ((g, list) in day) list.replaceAll { l ->
                if (l.type == LessonType.OTHER && (counts[listOf(l.start, l.title, l.teachers, l.rooms)] ?: 0) >= 3 &&
                    l.teachers.isNotEmpty() && !NOT_LECTURE.containsMatchIn(l.title) && !HOUR.containsMatchIn(l.title)) l.copy(type = LessonType.LECTURE) else l
            }
        }
        val days = dates.mapIndexed { i, date ->
            Day(date!!, perDay[i].mapValues { (_, v) -> v.sortedBy { it.start } })
        }.sortedBy { it.date }
        return Week(sheet.name.trim(), groups, days)
    }

    private fun parseDate(raw: String): LocalDate? {
        raw.toDoubleOrNull()?.let { if (it > 20000) return LocalDate.of(1899, 12, 30).plusDays(it.toLong()) }
        FULL_DATE.find(raw)?.let { m ->
            val (d, mo, y) = m.destructured
            return runCatching { LocalDate.of(y.toInt(), mo.toInt(), d.toInt()) }.getOrNull()
        }
        return null
    }

    /** «16.11-21.11 (12-я неделя)26-27» → понедельник 16.11.2026 (год — по учебному году в конце или ближайший к сегодня). */
    internal fun mondayFromName(name: String, today: LocalDate = LocalDate.now()): LocalDate? {
        val m = SHEET_START.find(name) ?: return null
        val (d, mo) = m.destructured
        val ay = ACADEMIC_YEAR.find(name.trim())
        val years = if (ay != null) listOf(2000 + if (mo.toInt() >= 8) ay.groupValues[1].toInt() else ay.groupValues[2].toInt())
            else listOf(today.year - 1, today.year, today.year + 1)
        val date = years.mapNotNull { y -> runCatching { LocalDate.of(y, mo.toInt(), d.toInt()) }.getOrNull() }
            .minByOrNull { kotlin.math.abs(java.time.temporal.ChronoUnit.DAYS.between(today, it)) } ?: return null
        return date.minusDays((date.dayOfWeek.value - 1).toLong())
    }

    private fun fillMissingDates(dates: MutableList<LocalDate?>) {
        val known = dates.indexOfFirst { it != null }
        if (known < 0) return
        for (i in dates.indices) if (dates[i] == null) dates[i] = dates[known]!!.plusDays((i - known).toLong())
    }

    private fun parseTimeRange(raw: String): Slot? {
        val m = TIME_RANGE.find(raw.trim()) ?: return null
        val (h1, m1, h2, m2) = m.destructured
        return runCatching { Slot(LocalTime.of(h1.toInt(), m1.toInt()), LocalTime.of(h2.toInt(), m2.toInt())) }.getOrNull()
    }

    // ───────────────────────── ячейка → пары ─────────────────────────

    private const val RANK = """доц|проф|ст\.?\s*преп|ст\.?\s*пр\.|пр\.?\s*ст\.|пр\.(?=\s*[А-ЯЁ][а-яё])|преп|асс|зав|акад|assoc\.?\s*prof|prof"""
    private val TEACHER_PREFIX = Regex("""^($RANK)(?![а-яёa-z])""", RegexOption.IGNORE_CASE)
    private val NAME_ONLY = Regex("""^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)?\s*[А-ЯЁ]\.\s?[А-ЯЁ]\.?$""")
    /** «1ПГ», «3 ПГ нечет», «1ПГ\2ПГ чет\нечет», «1(3) ПГ» — подгруппы и через какую неделю. */
    private val PG_LINE = Regex("""^(\d)(?:\(\d\))?\s*ПГ(?:\s*[\\/]\s*(\d)\s*ПГ)?\s*(нечет|чет)?(?:\s*[\\/]\s*(нечет|чет))?\.?$""", RegexOption.IGNORE_CASE)
    private fun pgTag(m: MatchResult): String {
        val (a, b, pa, pb) = m.destructured
        fun par(p: String) = if (p.lowercase() == "чет") "чёт" else "нечёт"
        return when {
            b.isNotEmpty() && pa.isNotEmpty() && pb.isNotEmpty() -> "$a подгр. — ${par(pa)}, $b подгр. — ${par(pb)}"
            b.isNotEmpty() -> "$a и $b подгр." + (if (pa.isNotEmpty()) " — ${par(pa)}" else "")
            pa.isNotEmpty() -> "$a подгр. — ${par(pa)}"
            else -> "$a подгр."
        }
    }
    private val NAME_SPLIT = Regex("""^(.*?[А-ЯЁ]\.\s?[А-ЯЁ]\.?)(?:\s+(.*))?$""")
    /** «Предмет, доц. Иванов И.И.» — преподаватель дописан в ту же строку. */
    private val INLINE_TEACHER = Regex(
        """^(.+?)[,\s]\s*((?:$RANK)(?![а-яёa-z])\.?\s*[^,]*?[А-ЯЁ][а-яё-]+\s*[А-ЯЁ]\.\s?[А-ЯЁ]?\.?)\s*/?\s*$""",
        RegexOption.IGNORE_CASE,
    )
    private val DATE_PREFIX = Regex(
        """^(с\s*|c\s*|до\s*|по\s*)?(\d{1,2})\.\s?(\d{2})(?:\.\d{2,4})?(?![\d:])\.?\s*(и далее|включ[а-я]*\.?)?""", RegexOption.IGNORE_CASE,
    )
    private val DATE_SPACED = Regex("""^(с|c|до)\s+(\d{1,2})\s(\d{2})(?!\d)""", RegexOption.IGNORE_CASE)
    private val CANCEL_LINE = Regex("""^отмен[аы]\s+(?:(на|с)\s+)?\d{1,2}\.\d{2}""", RegexOption.IGNORE_CASE)
    /** «на 24.11», «контрольная на 25.11», «отработка на 25.11», «доп. занятие на 16,.11, 23.11», «на 03.12, 10.12» */
    private val ONLY_LINE = Regex("""^(?:(доп\.?\s*занятие|контрольная|отработка|замена)\s*)?на\s+(\d{1,2}[\d.,\s]*\d\.?)$""", RegexOption.IGNORE_CASE)
    private val EXTRA_LINE = Regex("""^доп\.?\s*занятие$""", RegexOption.IGNORE_CASE)
    /** «с 21.11», «С 17.11», «с 25.11 с 10.30» (дата — если «месяц» не больше 12) */
    private val FROM_LINE = Regex("""^[сСcC]о?\s+(\d{1,2})[.,]\s?(\d{1,2})\.?(?:\s+с\s+(\d{1,2})[.:](\d{2}))?$""")
    /** «по 25.11», «до 05.12» */
    private val UNTIL_LINE = Regex("""^(?:по|до)\s+(\d{1,2})[.,](\d{1,2})\.?$""", RegexOption.IGNORE_CASE)
    /** «с 17.00», «с 10.30 до 13.20», «С 13.50 по 15.10» (время — если «минуты» больше 12 или 00) */
    private val TIME_LINE = Regex("""^[сСcC]\s+(\d{1,2})[.:](\d{2})(?:\s+(?:до|по)\s+(\d{1,2})[.:](\d{2}))?$""")
    /** «чет\нечет», «нечет / чет», «четн \нечет» отдельной строкой */
    private val PARITY_LINE = Regex("""^((?:не)?четн?|неч)\s*[\\/]\s*((?:не)?четн?|неч)\.?$""", RegexOption.IGNORE_CASE)
    private val TWO_SUBGROUPS = Regex("""^(\d+) и (\d+) подгр\.$""")
    private val TITLE_PARITY = Regex("""\s+((?:не)?чет[а-я]*)\s*[\\/]\s*((?:не)?чет[а-я]*)\s*$""", RegexOption.IGNORE_CASE)

    private fun isClock(minutes: String) = minutes.toInt() == 0 || minutes.toInt() > 12
    private fun parityWord(w: String) = if (w.lowercase().startsWith("не")) "нечёт" else "чёт"

    /** «16,.11, 23.11», «03.12, 10.12» → [(16,11),(23,11)]: день без месяца берёт месяц следующей даты. */
    private fun dayMonths(s: String): List<Pair<Int, Int>> {
        val parts = Regex("""(\d{1,2})(?:\s*[.,]\s*(\d{1,2})(?!\d))?""").findAll(s.replace(",.", ".")).toList()
        val out = ArrayList<Pair<Int, Int>>(); var month: Int? = null
        for (m in parts.asReversed()) {
            m.groupValues[2].takeIf { it.isNotEmpty() && it.toInt() in 1..12 }?.let { month = it.toInt() }
            month?.let { out += m.groupValues[1].toInt() to it }
        }
        return out.asReversed().distinct()
    }

    /** Строка только из подгрупп и чётности: «1,2 ПГ», «3ПГ\4ПГ (КБ) нечет\чет», «5ПГ (нечет)», «3 пг неч», «2ПГ еженедельно». */
    private fun isSubgroupLine(line: String): Boolean {
        if (!line.contains("пг", true)) return false
        val rest = line.lowercase().replace(Regex("""нечетн?|четн?|неч|еженедельно|нет|пгг?|кб|пи|[\d\s,()\\/.-]"""), "")
        return rest.isEmpty()
    }

    private fun looseSubgroups(line: String): String {
        val nums = Regex("""(\d+(?:\(\d\))?)\s*(?=,|пг|ПГ|Пг|пГ)""").findAll(line).map { it.groupValues[1] }.toList()
        val par = Regex("""(?i)(не)?чет|неч""").findAll(line).map { parityWord(it.value) }.toList()
        val tail = listOfNotNull(
            "КБ".takeIf { line.contains("КБ") }, "ПИ".takeIf { line.contains("ПИ") },
            "каждую неделю".takeIf { line.contains("еженед", true) },
        )
        val head = when {
            nums.size == 2 && par.size == 2 -> "${nums[0]} подгр. — ${par[0]}, ${nums[1]} подгр. — ${par[1]}"
            nums.size == 2 && par.isEmpty() -> "${nums[0]} и ${nums[1]} подгр."      // чётность может прийти следующей строкой
            par.size == 1 -> "${nums.joinToString(", ")} подгр. — ${par[0]}"
            nums.isNotEmpty() -> "${nums.joinToString(", ")} подгр."
            else -> "подгруппы"
        }
        return (listOf(head) + tail).joinToString(" · ")
    }

    /** Метки подгрупп: голова отдельно, «КБ»/«ПИ»/«каждую неделю» — отдельными метками. */
    private fun subgroupTags(line: String): List<String> = looseSubgroups(line).split(" · ")

    /** Ближайшая к дню пары дата «день.месяц» (правило на декабрь в январской неделе и т. п.). */
    private fun near(dm: Pair<Int, Int>, date: LocalDate): LocalDate? =
        listOf(date.year - 1, date.year, date.year + 1).mapNotNull { y -> runCatching { LocalDate.of(y, dm.second, dm.first) }.getOrNull() }
            .minByOrNull { kotlin.math.abs(java.time.temporal.ChronoUnit.DAYS.between(date, it)) }

    /** Видна ли пара в этот день: «на 24.11» — только тогда; «с 21.11» — не раньше; «по 25.11» — не позже. */
    private fun visibleOn(d: Draft, date: LocalDate?): Boolean {
        if (date == null) return true
        if (d.onlyDates.isNotEmpty() && d.onlyDates.mapNotNull { near(it, date) }.none { it == date }) return false
        // «с 25.11» / «до 05.12» в самой строке предмета — из меток
        val from = d.fromDate ?: d.tags.firstNotNullOfOrNull { t -> Regex("""^с (\d{2})\.(\d{2})$""").matchEntire(t)?.let { it.groupValues[1].toInt() to it.groupValues[2].toInt() } }
        val until = d.untilDate ?: d.tags.firstNotNullOfOrNull { t -> Regex("""^(?:до|по) (\d{2})\.(\d{2})$""").matchEntire(t)?.let { it.groupValues[1].toInt() to it.groupValues[2].toInt() } }
        from?.let { near(it, date) }?.let { if (date.isBefore(it)) return false }
        until?.let { near(it, date) }?.let { if (date.isAfter(it)) return false }
        return true
    }
    private val DAY_MONTH = Regex("""(\d{1,2})\.(\d{2})""")

    /** Попадает ли день пары под «отмена на 15.10 и 22.10», «отмена с 03.11 по 19.11», «отмена с 16.11». */
    internal fun cancelledOn(rule: String?, date: LocalDate?): Boolean {
        if (rule == null || date == null) return false
        fun at(m: MatchResult): LocalDate? {
            val (d, mo) = m.destructured
            // год — тот, при котором дата ближе к дню пары (правило на декабрь в январской неделе и т. п.)
            return listOf(date.year - 1, date.year, date.year + 1).mapNotNull { y -> runCatching { LocalDate.of(y, mo.toInt(), d.toInt()) }.getOrNull() }
                .minByOrNull { kotlin.math.abs(java.time.temporal.ChronoUnit.DAYS.between(date, it)) }
        }
        val dates = DAY_MONTH.findAll(rule).mapNotNull(::at).toList()
        if (dates.isEmpty()) return false
        val low = rule.lowercase()
        return if (Regex("""^отмен[аы]\s+с\s""").containsMatchIn(low)) {
            val from = dates[0]
            val to = if (low.contains(" по ")) dates.getOrNull(1) else null
            !date.isBefore(from) && (to == null || !date.isAfter(to))
        } else date in dates
    }
    private val MONTHS_GEN = listOf("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
    /** «1 сентября в 9.00 Информационный час» */
    /** по началу слова: в таблицах бывают опечатки («1 сентяюря в 9.00») */
    private val MONTH_STEMS = listOf("янв", "фев", "мар", "апр", "ма", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
    private val MONTH_PREFIX = Regex("""^(\d{1,2})\s+((?:янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек)[а-яё]*)(?:\s+в\s+(\d{1,2})[.:](\d{2}))?\s+""", RegexOption.IGNORE_CASE)
    private val CANCEL_PREFIX = Regex("""^не будет""", RegexOption.IGNORE_CASE)
    private val PAREN_PREFIX = Regex("""^\(+([^()]*)\)+""")
    private val SUBGROUP_PREFIX = Regex("""^(\d+)\s*гр\.""")
    private val PROFILE_PREFIX = Regex("""^(пр|сп)\.\s*(\d)(?!\d)""", RegexOption.IGNORE_CASE)
    private val LONE_TIME = Regex("""^(\d{1,2})[.:](\d{2})$""")
    private val TIME_BEFORE_TITLE = Regex("""^(\d{1,2})[.:](\d{2})(?=\s+[А-ЯЁA-Z])""")
    private val TYPE_SUFFIX = Regex("""(?:,\s*|\s+)(ЛК|ПЗ|ЛЗ|ЛР|СЗ|лаб)\.?\s*$""")
    /** Тип в скобках: «Механика (пз)», «Физика (лаб.)», отдельной строкой «(лк)». */
    private val TYPE_PAREN = Regex("""\s*\((лк|лек|пз|пр|лаб|лр|сем|сз)\.?\)\s*$""", RegexOption.IGNORE_CASE)
    private fun typeOf(word: String) = when (word.lowercase().trimEnd('.')) {
        "лк", "лек" -> LessonType.LECTURE
        "пз", "пр" -> LessonType.PRACTICE
        "сем", "сз" -> LessonType.SEMINAR
        "лаб", "лр" -> LessonType.LAB
        else -> null
    }
    private val NOT_LECTURE = Regex("""физическ|физкульт|кураторск|информационн|иностранн|английск|немецк|русск|белорусск""", RegexOption.IGNORE_CASE)

    /**
     * Тип пары, если он не написан явно: «лаб.»/«практикум» в названии — лабораторная;
     * подгруппы «через неделю» — лабораторная; общая ячейка на 3+ групп — лекция (кроме физкультуры, языков, кураторского часа).
     */
    private fun guessType(d: Draft, sharedWith: List<Int>): LessonType = when {
        d.type != LessonType.OTHER -> d.type
        LAB_WORDS.containsMatchIn(d.title) -> LessonType.LAB
        d.tags.any { it.contains("подгр") && (it.contains("чёт") || it.contains("нечёт")) } -> LessonType.LAB
        sharedWith.size >= 3 && !NOT_LECTURE.containsMatchIn(d.title) && !HOUR.containsMatchIn(d.title) -> LessonType.LECTURE
        // иностранный язык — всегда практические занятия (так же размечает конвертер у ФМО)
        FOREIGN_LANG.containsMatchIn(d.title) -> LessonType.PRACTICE
        else -> LessonType.OTHER
    }
    private val FOREIGN_LANG = Regex("""^(иностранн|английск|немецк|французск|испанск|китайск|англ\.)""", RegexOption.IGNORE_CASE)
    private val LAB_WORDS = Regex("""лаб\.|лаборат|практикум""", RegexOption.IGNORE_CASE)
    private val HOUR = Regex("""^(Кураторский|Информационный) час""", RegexOption.IGNORE_CASE)

    private class Draft {
        var title = ""
        var type = LessonType.OTHER
        var time: Slot? = null
        var startOnly: LocalTime? = null
        val teachers = ArrayList<String>()
        val tags = ArrayList<String>()
        val details = ArrayList<String>()
        var rooms: List<String> = emptyList()
        var cancelled = false
        /** после преподавателя или времени следующая «заглавная» строка — уже новый предмет */
        var closed = false
        /** название оборвано запятой — следующая строка может быть его продолжением */
        var openTitle = false
        var marked = false   // строка начиналась с «пр.N», «сп.N» или «*»
        var cancelRule: String? = null
        /** «на 24.11», «контрольная на 25.11» — пара только в эти дни (день, месяц) */
        var onlyDates: List<Pair<Int, Int>> = emptyList()
        /** «с 21.11» / «по 25.11» — с какого и по какой день пара идёт */
        var fromDate: Pair<Int, Int>? = null
        var untilDate: Pair<Int, Int>? = null
    }

    internal fun parseCell(
        text: String, roomText: String, start: LocalTime, end: LocalTime, sharedWith: List<Int>, date: LocalDate? = null,
    ): List<Lesson> {
        val drafts = ArrayList<Draft>()
        var cur: Draft? = null
        var pendingCancel: String? = null
        // пометки строкой ВЫШЕ предмета: «на 24.11», «с 21.11», «с 17.00», «доп. занятие» — достаются следующей паре
        var pendingOnly: List<Pair<Int, Int>> = emptyList()
        var pendingFrom: Pair<Int, Int>? = null
        var pendingUntil: Pair<Int, Int>? = null
        var pendingTime: Pair<LocalTime, LocalTime?>? = null
        val pendingTags = ArrayList<String>()
        fun fresh() = Draft().also {
            drafts += it; cur = it
            it.cancelRule = pendingCancel; pendingCancel = null
            it.onlyDates = pendingOnly; pendingOnly = emptyList()
            it.fromDate = pendingFrom; pendingFrom = null
            it.untilDate = pendingUntil; pendingUntil = null
            pendingTime?.let { (st, en) -> if (en != null) it.time = Slot(st, en) else it.startOnly = st }; pendingTime = null
            it.tags += pendingTags; pendingTags.clear()
        }
        // пометка уже начатой паре: предмет есть, преподаватель записан
        fun closedCur() = cur?.takeIf { it.closed && it.title.isNotEmpty() }

        /** Строка-пометка (время, даты, «доп. занятие»): запоминаем для пары и пропускаем. */
        fun noteLine(line: String): Boolean {
            TIME_LINE.matchEntire(line)?.takeIf { isClock(it.groupValues[2]) }?.let { m ->
                val st = runCatching { LocalTime.of(m.groupValues[1].toInt(), m.groupValues[2].toInt()) }.getOrNull() ?: return false
                val en = m.groupValues[3].takeIf { it.isNotEmpty() }?.let { runCatching { LocalTime.of(it.toInt(), m.groupValues[4].toInt()) }.getOrNull() }
                pendingTime = st to en; cur?.let { it.closed = true }
                return true
            }
            ONLY_LINE.matchEntire(line)?.let { m ->
                pendingOnly = dayMonths(m.groupValues[2])
                m.groupValues[1].takeIf { it.isNotBlank() }?.let { pendingTags += it.lowercase().squash() }
                cur?.let { it.closed = true }
                return true
            }
            if (EXTRA_LINE.matches(line)) { pendingTags += "доп. занятие"; cur?.let { it.closed = true }; return true }
            FROM_LINE.matchEntire(line)?.takeIf { !isClock(it.groupValues[2]) }?.let { m ->
                pendingFrom = m.groupValues[1].toInt() to m.groupValues[2].toInt()
                if (m.groupValues[3].isNotEmpty()) runCatching { LocalTime.of(m.groupValues[3].toInt(), m.groupValues[4].toInt()) }.getOrNull()?.let { pendingTime = it to null }
                cur?.let { it.closed = true }
                return true
            }
            UNTIL_LINE.matchEntire(line)?.let { m ->
                val until = m.groupValues[1].toInt() to m.groupValues[2].toInt()
                // «по 21.11» под преподавателем — про эту пару; над предметом — про следующую
                val c = closedCur()
                if (c != null) c.untilDate = until else pendingUntil = until
                return true
            }
            PARITY_LINE.matchEntire(line)?.let { m ->
                val d = cur ?: fresh()
                val p1 = parityWord(m.groupValues[1]); val p2 = parityWord(m.groupValues[2])
                val i = d.tags.indexOfLast { TWO_SUBGROUPS.matches(it) }
                val last = d.tags.getOrNull(i)?.let { TWO_SUBGROUPS.matchEntire(it) }
                if (last != null) d.tags[i] = "${last.groupValues[1]} подгр. — $p1, ${last.groupValues[2]} подгр. — $p2"
                else d.tags += "$p1 / $p2 неделя"
                return true
            }
            if (line.equals("КБ", true) || line.equals("ПИ", true)) { (cur ?: fresh()).tags += line.uppercase(); return true }
            return false
        }

        val lines = text.lines().map { it.squash() }.filter { it.isNotEmpty() }.flatMap { line ->
            val m = if (isTeacher(line)) null else INLINE_TEACHER.find(line)
            if (m != null) listOf(m.groupValues[1].trim() + ",", m.groupValues[2].trim()) else listOf(line)
        }

        for (line in lines) {
            // «отмена на 19.11» / «отмена с 03.11 по 19.11» — относится к предмету, который идёт ниже
            if (CANCEL_LINE.containsMatchIn(line)) { pendingCancel = line; cur?.let { it.closed = true }; continue }
            if (noteLine(line)) continue
            if (isSubgroupLine(line)) {
                val pg = PG_LINE.matchEntire(line)
                val d = cur ?: fresh()
                if (pg != null) d.tags += pgTag(pg) else d.tags.addAll(subgroupTags(line))
                continue
            }
            if (isTeacher(line)) {
                val d = cur ?: fresh()
                // «асс. Коршикова Д.В.отмена 20.11» — отмена прилипла к фамилии
                val glued = Regex("""(отмен[аы])\s*(на\s*)?(\d.*)$""", RegexOption.IGNORE_CASE).find(line)
                if (glued != null) d.cancelRule = "отмена на " + glued.groupValues[3]
                val line = if (glued != null) line.substring(0, glued.range.first).trim() else line
                val m = NAME_SPLIT.find(line)
                normalizeTeacher(m?.groupValues?.get(1) ?: line).takeIf { t -> t.count { it.isLetter() } >= 4 }?.let { d.teachers += it }
                m?.groupValues?.getOrNull(2)?.takeIf { it.isNotBlank() }?.let { d.details += it.trim() }
                d.closed = true
                continue
            }

            val c = cur
            if (c != null && c.title.isNotEmpty()) {
                // пробный разбор: что это за строка — новый предмет, время, продолжение названия или уточнение?
                val probe = Draft().also { parseTitleLine(it, line) }
                val titleLike = probe.title.isNotEmpty() && probe.title.first().let { it.isUpperCase() || it.isDigit() || it == '"' }
                val startsNew = titleLike &&
                    (c.closed || probe.marked || probe.time != null || probe.startOnly != null || HOUR.containsMatchIn(probe.title))
                if (!startsNew) {
                    when {
                        // «(лаб.)» отдельной строкой под названием — тип пары
                        probe.title.isEmpty() && probe.tags.size == 1 && typeOf(probe.tags[0]) != null -> {
                            if (c.type == LessonType.OTHER) c.type = typeOf(probe.tags[0])!!
                        }
                        probe.title.isEmpty() && probe.time != null && !c.closed && probe.tags.isEmpty() -> {
                            c.time = probe.time; c.closed = true
                        }
                        // название перенесено на следующую строку со строчной буквы: «Физика полупроводников и / полупроводниковых»
                        !c.closed && c.teachers.isEmpty() && line.first().isLowerCase() && probe.time == null && probe.tags.isEmpty() -> {
                            setTitle(c, c.title + " " + line)
                        }
                        !c.closed && (c.openTitle || line.startsWith("\"")) && probe.time == null -> {
                            c.openTitle = line.trimEnd().endsWith(",")
                            setTitle(c, c.title + " " + line)
                        }
                        else -> c.details += line
                    }
                    continue
                }
                fresh()
            }
            parseTitleLine(cur ?: fresh(), line)
        }

        // «(пз)» отдельной строкой попадает в пометки — это тип пары
        for (d in drafts) d.tags.firstNotNullOfOrNull { t -> typeOf(t)?.let { t to it } }?.let { (t, type) ->
            if (d.type == LessonType.OTHER) d.type = type
            d.tags.remove(t)
        }
        val ready = drafts.filter { d -> d.title.count { it.isLetter() } >= 3 || (d.title.isNotEmpty() && d.teachers.isNotEmpty()) }
            .filter { visibleOn(it, date) }
        assignRooms(ready, roomText)
        return ready.map { d ->
            Lesson(
                start = d.time?.start ?: d.startOnly?.takeIf { it < end } ?: start,
                end = d.time?.end ?: end,
                title = d.title,
                type = guessType(d, sharedWith),
                teachers = d.teachers,
                rooms = d.rooms,
                tags = d.tags.distinct(),
                details = d.details,
                cancelled = d.cancelled || cancelledOn(d.cancelRule, date),
                sharedWith = sharedWith,
            )
        }
    }

    private fun isTeacher(line: String) = TEACHER_PREFIX.containsMatchIn(line) || NAME_ONLY.matches(line)

    private val T_SENIOR = Regex("""(?:ст\.?\s*преп|ст\.?\s*пр|пр\.?\s*ст)\.*""")
    private val T_NO_DOT = Regex("""^(доц|проф|асс|преп)\s+""")
    private val T_PR = Regex("""^пр\.\s*(?=[А-ЯЁ])""")
    private val T_INITIALS_GAP = Regex("""([А-ЯЁ])\.\s+([А-ЯЁ])\.?$""")
    private val T_LAST_DOT = Regex("""([А-ЯЁ]\.[А-ЯЁ])$""")
    /** «СаечниковВ.А.» → «Саечников В.А.», «РадчиковаВС» → «Радчикова В.С.» */
    /** «ст. пр. ст. пр. Белявский» → одно звание */
    private val T_DUP_RANK = Regex("""^((?:ст\.\s*)?пр(?:еп)?\.)\s*(?:ст\.\s*)?пр(?:еп)?\.\s*""")
    /** «Мигель.А.С.», «Серая.И.» → «Мигель А.С.» */
    private val T_SURNAME_DOT = Regex("""([а-яё])\.([А-ЯЁ]\.)""")
    /** «Шмарловский АВ», «Белявский ДС.» → «А.В.» */
    private val T_NODOT_INITIALS = Regex("""\s([А-ЯЁ])([А-ЯЁ])\.?$""")
    private val T_GLUED_INITIALS = Regex("""([а-яё])([А-ЯЁ]\.)""")
    private val T_GLUED_NODOTS = Regex("""([а-яё])([А-ЯЁ])([А-ЯЁ])$""")
    private val T_GLUED = Regex("""([а-яё])\.(?=[А-ЯЁ][а-яё])""")
    private val PAREN_GLUED = Regex("""(?<=[^\s(])\(""")
    private val SPACE_COMMA = Regex("""\s+,""")
    private val ROOM_TOKEN = Regex("""\d+[а-яА-Я]?""")
    private val ROOM_GLUED = Regex("""^(\d+)(?=[^\d\s])""")

    private fun normalizeTeacher(s: String) = s
        .replace(T_SENIOR, "ст. преп.")
        .replace(T_NO_DOT, "$1. ")
        .replace(T_PR, "преп. ")
        .replace(T_INITIALS_GAP, "$1.$2.")
        .replace(T_LAST_DOT, "$1.")
        .replace(T_GLUED, "$1. ")
        .replace(T_DUP_RANK, "$1 ")
        .replace(Regex("""([А-ЯЁ]),([А-ЯЁ])"""), "$1.$2")
        .replace(Regex("""\s*\((п\d)\)$"""), " ($1)")
        .replace(T_SURNAME_DOT, "$1 $2")
        .replace(T_NODOT_INITIALS, " $1.$2.")
        .replace(T_GLUED_NODOTS, "$1 $2.$3.")
        .replace(T_GLUED_INITIALS, "$1 $2")
        .replace(Regex(""",\s*гр\.?\s*\d*$"""), "")
        .replace(Regex("""\.{2,}"""), ".").replace(Regex("""[,\s]+$"""), "").replace(Regex("""\.\d+$"""), ".")
        .squash()

    /** Снимает с начала строки служебные пометки (профиль, даты, «не будет», время) и кладёт остаток в название. */
    private fun parseTitleLine(d: Draft, source: String) {
        var s = source.trim()
        d.openTitle = s.endsWith(",")
        var hadDate = false
        while (true) {
            val before = s
            if (hadDate && s.startsWith(",")) s = s.drop(1).trim()   // «09.09, 16.09, 23.09 Предмет»
            TIME_RANGE.find(s)?.let { m ->
                parseTimeRange(m.value)?.let { d.time = it }
                s = s.substring(m.range.last + 1).trim(' ', ',')
            }
            PROFILE_PREFIX.find(s)?.let { m ->
                d.tags += "${m.groupValues[1].lowercase()}.${m.groupValues[2]}"
                d.marked = true
                s = s.substring(m.range.last + 1).trim()
            }
            (DATE_PREFIX.find(s) ?: DATE_SPACED.find(s))?.let { m ->
                val day = m.groupValues[2].toInt()
                val month = m.groupValues[3].toInt()
                if (day in 1..31 && month in 1..12) {
                    val date = "%02d.%02d".format(day, month)
                    val word = m.groupValues[1].trim().lowercase().replace('c', 'с')   // латинская «c» → кириллица
                    val after = m.groupValues.getOrNull(4).orEmpty()
                    d.tags += when {
                        word == "до" || word == "по" -> "до $date"
                        word == "с" || after.startsWith("и далее", true) -> "с $date"
                        else -> date
                    }
                    hadDate = true
                    s = s.substring(m.range.last + 1).trim()
                }
            }
            CANCEL_PREFIX.find(s)?.let { d.cancelled = true; s = s.substring(it.range.last + 1).trim() }
            MONTH_PREFIX.find(s)?.let { m ->
                val w = m.groupValues[2].lowercase()
                val month = MONTH_STEMS.indexOfFirst { stem -> if (stem == "ма") w.startsWith("ма") && !w.startsWith("мар") else w.startsWith(stem) } + 1
                if (month > 0) d.onlyDates = d.onlyDates + (m.groupValues[1].toInt() to month)
                if (m.groupValues[3].isNotEmpty()) runCatching { LocalTime.of(m.groupValues[3].toInt(), m.groupValues[4].toInt()) }.getOrNull()?.let { d.startOnly = it }
                s = s.substring(m.range.last + 1).trim()
            }
            PAREN_PREFIX.find(s)?.let { m ->
                m.groupValues[1].squash().takeIf { it.isNotEmpty() }?.let { d.tags += it }
                s = s.substring(m.range.last + 1).trim()
            }
            SUBGROUP_PREFIX.find(s)?.let { d.tags += "${it.groupValues[1]} гр."; s = s.substring(it.range.last + 1).trim() }
            LONE_TIME.find(s)?.let { d.tags += "в ${it.groupValues[1].toInt()}:${it.groupValues[2]}"; s = "" }
            TIME_BEFORE_TITLE.find(s)?.let { m ->
                runCatching { LocalTime.of(m.groupValues[1].toInt(), m.groupValues[2].toInt()) }.getOrNull()?.let { d.startOnly = it }
                s = s.substring(m.range.last + 1).trim()
            }
            if (s.startsWith("*")) { d.tags += "по выбору"; d.marked = true; s = s.drop(1).trim() }
            if (s == before) break
        }
        if (s.isNotEmpty()) setTitle(d, s)
    }

    private fun setTitle(d: Draft, raw: String) {
        var s = raw.trim().trimEnd('/', '\\', ' ').removePrefix("- ").trim()
        // «Механика чет\нечет» — чётность в названии
        TITLE_PARITY.find(s)?.let { m -> d.tags += "${parityWord(m.groupValues[1])} / ${parityWord(m.groupValues[2])} неделя"; s = s.substring(0, m.range.first) }
        // «Физическая культура основная группа: ул. Карла Маркса, 31 группа СМГ: …» — адреса в подробности
        Regex("""\s+(основная группа\s*:.*|ул\.\s.*)$""", RegexOption.IGNORE_CASE).find(s)?.let { m -> d.details += m.groupValues[1].trim(); s = s.substring(0, m.range.first) }
        // «Основы КБ по 03.12» — до какого дня
        Regex("""\s+(?:по|до)\s+(\d{1,2})\.(\d{2})$""").find(s)?.let { m -> d.untilDate = m.groupValues[1].toInt() to m.groupValues[2].toInt(); s = s.substring(0, m.range.first) }
        // «Информационный час (еженедельно) 13.30» — время в конце
        Regex("""\s+(\d{1,2})[.:](\d{2})$""").find(s)?.takeIf { isClock(it.groupValues[2]) }?.let { m ->
            runCatching { LocalTime.of(m.groupValues[1].toInt(), m.groupValues[2].toInt()) }.getOrNull()?.let { d.startOnly = it }
            s = s.substring(0, m.range.first)
        }
        TYPE_PAREN.find(s)?.let { m -> typeOf(m.groupValues[1])?.let { d.type = it; s = s.substring(0, m.range.first) } }
        TYPE_SUFFIX.find(s)?.let { m ->
            d.type = when (m.groupValues[1]) {
                "ЛК" -> LessonType.LECTURE
                "ПЗ" -> LessonType.PRACTICE
                "СЗ" -> LessonType.SEMINAR
                else -> LessonType.LAB
            }
            s = s.substring(0, m.range.first)
        }
        d.title = s.trim(' ', ',').replace(PAREN_GLUED, " (").replace(SPACE_COMMA, ",").squash()
    }

    /** «230\n309.   212» → три аудитории; «1303\nФМО» и «156гл. корп.» → одна, с пояснением. */
    private fun parseRooms(roomText: String): List<String> {
        val out = ArrayList<String>()
        // «33 \К1», «41 /К1» — корпус через слэш; «62 \К1,, 93 \К1» — несколько аудиторий через запятую
        val parts = roomText.lines().flatMap { l ->
            val p = l.split(',').map { it.trim() }.filter { it.isNotEmpty() }
            if (p.size > 1 && p.all { it.first().isDigit() }) p else listOf(l)
        }
        for (raw in parts) {
            val line = raw.replace(ROOM_SLASH, " ").squash().removeSuffix(".0").trimEnd(',')
            if (line.isEmpty()) continue
            val tokens = line.split(' ').map { it.trim('.', ',', ';') }.filter { it.isNotEmpty() }
            when {
                tokens.all { t -> t.matches(ROOM_TOKEN) } -> out += tokens
                line.first().isDigit() -> out += line.replace(ROOM_GLUED, "$1 ")
                out.isNotEmpty() -> out[out.lastIndex] = out.last() + " " + line   // «ФМО», «гл. корп.» — к предыдущей
                else -> out += line
            }
        }
        // «156гл. корп.», «156 г.к.», «156 гл.к» — это одна и та же аудитория главного корпуса
        return out.map { MAIN_BUILDING.replace(it) { m -> "${m.groupValues[1]} гл. корп." } }
    }

    private val ROOM_SLASH = Regex("""\s*[\\/]\s*(?=[А-ЯЁа-яёA-Za-z])""")
    private val MAIN_BUILDING = Regex("""^(\d+)\s*(?:гл|г)\.?\s*к(?:орп)?\.?$""")

    private fun assignRooms(drafts: List<Draft>, roomText: String) {
        val rooms = parseRooms(roomText)
        if (rooms.isEmpty() || drafts.isEmpty()) return
        val teacherCount = drafts.sumOf { it.teachers.size }
        when {
            drafts.size == 1 -> drafts[0].rooms = rooms
            rooms.size == drafts.size -> drafts.forEachIndexed { i, d -> d.rooms = listOf(rooms[i]) }
            rooms.size == teacherCount -> {
                var i = 0
                for (d in drafts) { d.rooms = rooms.subList(i, i + d.teachers.size).toList(); i += d.teachers.size }
            }
            else -> drafts.forEach { it.rooms = rooms }
        }
    }

    private val WS = Regex("""\s+""")
    private fun String.squash() = replace(WS, " ").trim()
}
