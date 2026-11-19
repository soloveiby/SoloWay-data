"""Разбор недельной таблицы МГЭУ им. Сахарова на сохранённом ответе сайта."""
import sys
from datetime import date, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from adapters.iseu_aspx import merge_parallel, options, parse_timetable, short_name, _rooms  # noqa: E402

PAGE = (HERE / "fixtures" / "iseu_week.html").read_text(encoding="utf-8")


def test_names_and_rooms():
    assert short_name("доц. Луцевич Александр Александрович") == "Луцевич А.А."
    assert short_name("ст.пр. Беляева Татьяна-Светлана Валериевна") == "Беляева Т.В."
    assert short_name("доктор Шахаб Сиямак Насер") == "Шахаб С.Н."
    assert _rooms("ауд. 103, К2") == ["103 К2"]
    assert _rooms("ауд. , ") == []


def test_week_table():
    ls = parse_timetable(PAGE)
    first = ls[0]
    assert (first.date, first.start, first.end, first.title, first.type, first.teachers, first.rooms) == (
        date(2026, 11, 16), time(8, 30), time(9, 50), "Физика", "LECTURE", ["Луцевич А.А."], ["103 К2"])
    assert ls[2].type == "PRACTICE" and ls[2].title == "Физика"
    # физкультура без преподавателя и аудитории
    pe = next(l for l in ls if l.title == "Физическая культура")
    assert pe.teachers == [] and pe.rooms == []
    # иностранный язык: четыре строки в одно время → одна пара с четырьмя преподавателями
    merged = merge_parallel(ls)
    lang = [l for l in merged if l.title == "Иностранный язык" and l.date == date(2026, 11, 17)]
    assert len(lang) == 1 and len(lang[0].teachers) == 4 and len(lang[0].rooms) == 4
    assert {l.date.weekday() for l in ls} <= set(range(6))


def test_selects():
    opts = options(PAGE, "ddlCourse")
    assert [o[1] for o in opts][:2] == ["1 курс", "2 курс"]
    assert any(sel for _, _, sel in options(PAGE, "ddlWeek"))
