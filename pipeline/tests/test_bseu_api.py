"""Разбор страницы расписания БГЭУ на сохранённых ответах сайта."""
import sys
from datetime import date, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from adapters.bseu_api import parse_schedule, parse_weeks, short_name, week_one_monday, parse_group_title  # noqa: E402

FIX = HERE / "fixtures" / "bseu"
DGG = (FIX / "9769-cp1251.html").read_bytes().decode("cp1251")


def test_weeks_and_names():
    assert parse_weeks("(2-18)") == list(range(2, 19))
    assert parse_weeks("(2-4,7,9-10)") == [2, 3, 4, 7, 9, 10]
    assert short_name("Сошникова Ирина Сергеевна") == "Сошникова И.С."
    assert short_name("Субботина Е.А.") == "Субботина Е.А."
    assert parse_group_title("25 ДГГ-1 | Ресторанный бизнес") == ("25 ДГГ-1", "Ресторанный бизнес")
    # 18.11.2026 — 12-я неделя → 1-я неделя началась в понедельник 31.08
    assert week_one_monday(12, date(2026, 11, 18)) == date(2026, 8, 31)


def test_group_page():
    cur, ls = parse_schedule(DGG)
    assert cur == 12
    assert len(ls) > 40
    pe = ls[0]
    assert (pe.weekday, pe.start, pe.end, pe.title, pe.type, pe.teachers, pe.weeks) == (
        0, time(9, 45), time(11, 5), "Физическая культура", "PRACTICE", ["Сошникова И.С."], list(range(2, 19)))
    # подгруппы под общей шапкой
    lab = [l for l in ls if l.title == "Техническое обеспечение торгового бизнеса" and l.type == "LAB" and l.weeks == [15] and l.weekday == 0]
    assert {tuple(l.tags) for l in lab} == {("1 подгруппа",), ("2 подгруппа",)}
    assert {l.rooms[0] for l in lab} == {"5/219", "5/304"}
    # зачёт — тип OTHER с пометкой
    assert any(l.title == "Международная экономика" and "зачёт" in l.tags for l in ls)
    assert all(l.weeks and l.title for l in ls)


def test_all_fixtures_parse():
    for f in FIX.glob("*.html"):
        raw = f.read_bytes()
        try: page = raw.decode("utf-8")
        except UnicodeDecodeError: page = raw.decode("cp1251")
        cur, ls = parse_schedule(page)
        assert cur and ls, f.name
        for l in ls:
            assert l.start < l.end and l.weeks and l.title, (f.name, l)
