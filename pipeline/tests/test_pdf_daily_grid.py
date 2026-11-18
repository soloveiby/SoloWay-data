"""Разбор PDF колледжа на сохранённых файлах: если сайт поменяет вид таблицы, тесты это поймают."""
import sys
from datetime import date, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from adapters.pdf_daily_grid import Vocabulary, feed_pdf, parse_pdf  # noqa: E402

FIX = sorted((HERE / "fixtures").glob("*.pdf"))
VOCAB = Vocabulary()
for f in FIX:
    feed_pdf(str(f), VOCAB)
DAY = parse_pdf(str(HERE / "fixtures" / "18.11.2026.pdf"), VOCAB)


def find(group, start, tag=None):
    out = [l for l in DAY.lessons if l.group == group and l.start == start and (tag is None or tag in l.tags)]
    assert out, f"нет пары {group} {start} {tag}"
    return out[0]


def test_header():
    assert DAY.date == date(2026, 11, 18)
    assert DAY.parity == "знаменатель"
    assert len(DAY.groups) == 50
    assert DAY.warnings == []


def test_plain_lesson():
    l = find("6К9091", time(8, 0))
    assert (l.title, l.teachers, l.rooms, l.end) == ("Белорусская литература", ["Альшевская"], ["313"], time(9, 40))


def test_subgroups_split_cell():
    a = find("6К9091", time(9, 50), "1 подгруппа")
    b = find("6К9091", time(9, 50), "2 подгруппа")
    assert a.title == "Физ к и зд" and a.teachers == ["Авдеенко", "Макейчик"] and a.rooms == ["с/з", "а/з"]
    assert b.title == "Русский язык" and b.teachers == ["Швец"] and b.rooms == ["214"]


def test_word_wraps_glued():
    assert find("6К9191", time(11, 50), "2 подгруппа").title == "Математика"
    assert find("6К9191", time(8, 0)).title == "Допризывная/медицинская подготовка"
    assert find("6К9092", time(8, 0), "1 подгруппа").title == "Белорусский язык"
    assert find("6К9391", time(11, 50), "2 подгруппа").teachers == ["Соколовская", "Цедрик"]


def test_word_boundary_kept():
    assert find("6К9391", time(11, 50), "1 подгруппа").title == "Русская литература"
    assert find("6К9391", time(13, 40)).title == "История Беларуси"


def test_special_time_from_cell_above():
    l = find("6К9091", time(13, 40))
    assert (l.title, l.end, l.teachers) == ("Информационный час", time(14, 0), ["Костоглодова"])


def test_practice_spanning_pairs_with_own_time():
    a = find("5К9191", time(13, 40), "1 подгруппа")
    b = find("5К9191", time(8, 0), "2 подгруппа")
    assert (a.title, a.end, a.teachers, a.rooms) == ("Практика", time(19, 10), ["Ручаевская"], ["218"])
    assert (b.end, b.teachers, b.rooms) == (time(13, 30), ["Смолер"], ["103"])


def test_two_teachers_two_rooms():
    l = find("5К9491", time(9, 50))
    assert (l.title, l.teachers, l.rooms) == ("ИГ", ["Будник", "Костоглодова"], ["406а", "406б"])


def test_all_fixtures_parse_cleanly():
    for f in FIX:
        d = parse_pdf(str(f), VOCAB)
        assert d.warnings == [], f.name
        assert len(d.groups) >= 40 and len(d.lessons) >= 120, f.name
        for l in d.lessons:
            assert l.title and l.start < l.end, (f.name, l)
