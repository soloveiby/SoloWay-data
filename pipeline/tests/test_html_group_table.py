"""Разбор страниц групп мехмата БГУ (mmf.bsu.by) на сохранённых копиях."""
import sys
from datetime import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from adapters.html_group_table import group_links, parse_group_page, parse_remarks, teacher_name  # noqa: E402

FIX = HERE / "fixtures" / "bsu-mmf"


def page(name):
    return parse_group_page((FIX / f"{name}.html").read_text(encoding="utf-8"))


def test_small_parts():
    assert parse_remarks("2н/б") == (2, ["подгр. б"])
    assert parse_remarks("А,Б") == (None, ["подгр. А, Б"])
    assert parse_remarks("1н") == (1, [])
    assert parse_remarks("2гр.") == (None, ["2 подгруппа"])
    assert teacher_name("доц. Малинин Д.А.") == "Малинин Д.А."
    assert teacher_name("ст.преп.Листратенко Н.В.") == "Листратенко Н.В."
    assert teacher_name("преп. Кимбар") == "Кимбар"
    assert teacher_name("Бухтояров С.Е.") == "Бухтояров С.Е."
    assert teacher_name("ул. Ленинградская, 14") is None
    assert teacher_name("(профессиональная лексика)") is None


def test_course_page_links():
    links = group_links((FIX / "course-1.html").read_text(encoding="utf-8"))
    assert len(links) == 12
    assert links[0][0].endswith("/1-kurs/1-gruppa/")
    assert links[-1][1].startswith("ВФ")


def test_group_page():
    g = page("1-kurs_1-gruppa")
    assert g.name == "Группа 1"
    assert g.specialty == "Математика и компьютерные науки. Математика"
    assert g.updated.startswith("2026-11-17")
    mon = [l for l in g.lessons if l.weekday == 0]
    # физкультура идёт по своему времени, а не по сетке звонков
    assert (mon[0].title, mon[0].start, mon[0].end) == ("Физическая культура", time(9, 30), time(10, 50))
    alg = mon[1]
    assert (alg.title, alg.type, alg.teachers, alg.rooms) == ("Алгебра и теория чисел", "LECTURE", ["Малинин Д.А."], ["334"])
    eng = [l for l in g.lessons if l.weekday == 1 and l.title == "Английский язык" and "подгр. б" in l.tags][0]
    assert eng.skip == [(1, 9)] and eng.teachers == ["Буславская М.С."]
    # две строки названия, адрес корпуса — в подробностях, опечатка сайта исправлена
    vov = [l for l in g.lessons if l.title.startswith("ВОВ") and l.type == "LECTURE"][0]
    assert vov.title == "ВОВ советского народа (в контексте Второй мировой войны)"
    assert vov.details == ["ул. Ленинградская, 14"] and vov.rooms == ["701 Химфак"]
    assert any(l.title == "История белорусской государственности" for l in g.lessons)
    assert [l.only for l in g.lessons if l.title == "Аналитическая геометрия"] == [[(22, 10)]]
    cur = [l for l in g.lessons if l.title.startswith("Кураторский") and l.weekday == 3][0]
    assert cur.parity == 1 and cur.teachers == ["Бухтояров С.Е."]
    assert [l for l in g.lessons if l.title.startswith("2/6")] == []
    assert any("поток 2/6в(н)" in l.tags for l in g.lessons)


def test_dates_and_transfers():
    g = page("1-kurs_7-gruppa")
    moved = [l for l in g.lessons if "вместо 19.11" in l.tags][0]
    assert moved.only == [(14, 11)]
    g = page("4-kurs_1-gruppa")
    ip = [l for l in g.lessons if l.title == "Основы управления интеллектуальной собственностью" and l.type == "LECTURE"][0]
    assert (ip.since, ip.until) == ((12, 9), (14, 11))
    phys = [l for l in g.lessons if l.title == "Физика" and l.type == "LECTURE"][0]
    assert phys.skip == [(18, 11), (25, 11)]
    extra = [l for l in g.lessons if l.title.startswith("Дополнительный вид обучения") and l.type == "SEMINAR"][0]
    assert extra.since == (9, 9) and extra.teachers == ["Мартинович И.В."]


def test_subgroups_and_own_time():
    g = page("4-kurs_2-gruppa")
    img = [l for l in g.lessons if l.title == "Методы и алгоритмы обработки изображений"][0]
    assert img.parity == 1 and img.tags == ["подгр. А"] and img.teachers == ["Абламейко С.В."]
    assert any(l.teachers == ["Кушнеров"] for l in g.lessons)
    g = page("3-kurs_8-gruppa")
    online = [l for l in g.lessons if l.rooms == ["онлайн"]][0]
    assert (online.start, online.end) == (time(9, 45), time(11, 5))
    g = page("2-kurs_10-gruppa")
    # «Математический анализ (кроме 19.10)» и «Алгебра … 19.10» в одной ячейке времени — замена на один день
    swap = [l for l in g.lessons if l.title == "Алгебра и теория чисел" and l.only]
    assert [l.only for l in swap] == [[(19, 10)], [(22, 10)]]
    assert all((19, 10) in l.skip or (22, 10) in l.skip for l in g.lessons if l.title == "Математический анализ" and l.start == swap[0].start and l.weekday in (0, 3))
    assert all(l.title for l in g.lessons)
