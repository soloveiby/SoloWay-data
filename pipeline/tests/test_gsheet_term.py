"""Магистратура радиофака: сетка на семестр, даты в ячейках."""
import sys
from datetime import date, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from adapters.gsheet_term import parse_cell, parse_sheet, read_xlsx, split_teacher  # noqa: E402

TERM = date(2026, 9, 1)
SLOT = (time(9, 0), time(10, 20))


def days(cell, weekday):
    return [d.isoformat()[5:] for d in sorted({l.date for l in parse_cell(cell, weekday, SLOT, TERM)})]


def test_ranges_and_parity():
    # нечётные недели только у первого диапазона, второй — каждую неделю
    assert days("ЛБ - 28.09 - 23.11 (нечётн.нед.), 30.11-28.12 ауд. 49  Вычислительная радиофизика, Малый С.В.", 0) == [
        "09-28", "10-12", "10-26", "11-09", "11-23", "11-30", "12-07", "12-14", "12-21", "12-28"]
    assert days("Семинар  - 16.11, 23.11 ИКТ Интернет всего, Корань В.В.", 0) == ["11-16", "11-23"]
    # январь в осеннем семестре — следующий год
    assert days("ЛК, seminar - 01.12-29.12, 05.01-19.01 room 612 Methodology, Ivanov I.I.", 1)[-1] == "01-19"
    assert "11-11" not in days("ПЗ - 02.09-18.11, (11.11 отмена) ауд. 705, Иностранн.язык*, Брич О.В.", 2)


def test_cell_fields():
    c = ("ПЗ  -  07.09 -16.11 (08:15-09:50) (чётн.нед) Прикладные задачи математической физики, Комаров Станислав Олегович,  "
         "(Физический фак-т), ауд. 215/\nЛБ - 28.09 - 23.11 (нечётн.нед.), 30.11-28.12 ауд. 49  Вычислительная радиофизика, Малый С.В.\n")
    ls = parse_cell(c, 0, SLOT, TERM)
    pz = next(l for l in ls if l.date == date(2026, 11, 16))
    assert (pz.start, pz.end, pz.type, pz.title, pz.teachers, pz.rooms) == (
        time(8, 15), time(9, 50), "PRACTICE", "Прикладные задачи математической физики", ["Комаров Станислав Олегович"], ["215"])
    lab = next(l for l in ls if l.date == date(2026, 11, 30))
    assert (lab.type, lab.title, lab.teachers, lab.rooms, lab.start) == ("LAB", "Вычислительная радиофизика", ["Малый С.В."], ["49"], time(9, 0))


def test_teachers():
    assert split_teacher("Shcherbak Igor N., Telecommunications Systems") == ("Telecommunications Systems", ["Shcherbak Igor N."])
    assert split_teacher("Internet of Everything, Kozlova Elena I") == ("Internet of Everything", ["Kozlova Elena I."])
    assert split_teacher("Безопасность баз данных,Скакун В.В") == ("Безопасность баз данных", ["Скакун В.В."])


def test_sheet():
    book = read_xlsx((HERE / "fixtures" / "rct_mag.xlsx").read_bytes())
    groups, lessons = parse_sheet(book["1 курс расписание"], TERM)
    assert [g[0] for g in groups] == ["Группа 60", "Группа 61", "Группа 63"]
    assert groups[1][1] == "Кибербезопасность"
    week = [l for l in lessons[1] if date(2026, 11, 16) <= l.date <= date(2026, 11, 21)]
    assert any(l.title == "Безопасность баз данных" and l.type == "LAB" for l in week)
    assert all(l.date.weekday() < 6 for ls in lessons.values() for l in ls)
