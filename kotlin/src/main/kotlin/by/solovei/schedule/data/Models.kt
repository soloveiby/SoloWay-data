package by.solovei.schedule.data

import java.time.LocalDate
import java.time.LocalTime

/** Учебная группа — колонка таблицы. */
data class Group(
    val index: Int,
    val name: String,          // «Группа 5»
    val code: String,          // «6-05-0533-02»
    val specialty: String,     // «Прикладная физика»
    val profiles: List<String> = emptyList(),
)

enum class LessonType(val short: String, val full: String) {
    LECTURE("ЛК", "Лекция"),
    PRACTICE("ПЗ", "Практика"),
    LAB("ЛЗ", "Лабораторная"),
    SEMINAR("СЗ", "Семинар"),
    OTHER("", ""),
}

data class Lesson(
    val start: LocalTime,
    val end: LocalTime,
    val title: String,
    val type: LessonType,
    val teachers: List<String>,
    val rooms: List<String>,
    /** Короткие пометки: «с 25.11», «1 раз в две недели», «по выбору». */
    val tags: List<String>,
    /** Длинные уточнения: адреса, подгруппы и т.п. */
    val details: List<String>,
    val cancelled: Boolean,
    /** Номера групп (index), у которых эта пара общая. */
    val sharedWith: List<Int>,
    /** id личного занятия, если пару добавил сам пользователь */
    val customId: Long? = null,
)

data class Day(
    val date: LocalDate,
    /** groupIndex -> пары, отсортированные по времени */
    val lessons: Map<Int, List<Lesson>>,
)

data class Week(
    val title: String,
    val groups: List<Group>,
    val days: List<Day>,
) {
    val start: LocalDate get() = days.first().date
    val end: LocalDate get() = days.last().date
}

data class Schedule(val weeks: List<Week>) {
    val groups: List<Group> get() = weeks.lastOrNull()?.groups ?: emptyList()
}
