"""
Адаптер «HTML-таблица на группу»: так публикует расписание мехмат БГУ (mmf.bsu.by) —
страница курса со ссылками на группы, на странице группы таблица на одну неделю:
  День | Время | Группа (пометки) | Предмет, преподаватель | Тип | Ауд.
Неделя одна на весь семестр, особенности пишут прямо в ячейках:
  • «Группа»: подгруппы («а», «Б», «А,Б», «2гр.») и через неделю («1н», «2н/б»);
  • в «Предмет, преподаватель» строки через <br>: предмет (иногда в две строки), преподаватель с должностью,
    адрес корпуса («ул. Ленинградская, 14») и даты: «с 25.09», «(до 02.12)», «(с 12.09 до 14.11)»,
    «кроме 19.10», «(кроме 18.11; 25.11)», «22.10» / «12.11 19.11» — только в эти дни, «14.11 за 19.11» — перенос;
  • своё время прямо в ячейке: «9:30-10:50» (физкультура), «(16:05-18:35)», «(с 9:45 до 11:05)», «12-30-13-50».
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from datetime import time

DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота"]
TYPES = {"лекц": "LECTURE", "л": "LECTURE", "лаб": "LAB", "практ": "PRACTICE", "сем": "SEMINAR"}

DM = r"(\d{1,2})\.(\d{1,2})"
SPAN_TIME = re.compile(r"^\(?\s*(?:с\s*)?(\d{1,2})[:.\-](\d{2})\s*(?:[-–—]|до)\s*(\d{1,2})[:.\-](\d{2})\s*\)?$")
SINCE_UNTIL = re.compile(r"\(?\s*с\s*" + DM + r"\s*(?:до|по|[-–—])\s*" + DM + r"\s*\)?")
SINCE = re.compile(r"\(?\s*с\s*" + DM + r"\s*\)?")
UNTIL = re.compile(r"\(?\s*(?:до|по)\s*" + DM + r"\s*\)?")
EXCEPT = re.compile(r"\(?\s*кроме\s*(" + DM + r"(?:\s*[,;и]?\s*" + DM + r")*)\s*\)?")
INSTEAD = re.compile(r"^\(?\s*" + DM + r"\s*за\s*" + DM + r"\s*\)?$")
ONLY = re.compile(r"^\(?\s*(?:только\s*)?(" + DM + r"(?:\s*[,;и]?\s*" + DM + r")*)\s*\)?$")
RANK = re.compile(r"^(?:(?:проф|доц|ст\.?\s*преп|преп|асс|зав\.?\s*каф|академик|стаж)\.?\s*)+", re.I)
NAME = re.compile(r"^[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s*[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.)?$")
INITIALS = re.compile(r"^[А-ЯЁ]\.\s?[А-ЯЁ]?\.?$")
SURNAME = re.compile(r"^[А-ЯЁа-яё][а-яё]+(?:ич|ов|ова|ев|ева|ин|ина|ко|ук|юк|ий|ая|ых|их|ец|ик)$")
STREAM = re.compile(r"^(\d+/\d+[а-яa-z]?(?:\([а-я]\))?)\s+")      # «2/6в(н) Английский язык» — поток с другой группой
TYPO = {"гусударственности": "государственности"}


@dataclass
class Lesson:
    weekday: int
    start: time
    end: time
    title: str
    type: str
    teachers: list[str]
    rooms: list[str]
    tags: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    parity: int | None = None                        # 1 — «1н», 2 — «2н», None — каждую неделю
    since: tuple[int, int] | None = None             # (день, месяц)
    until: tuple[int, int] | None = None
    only: list[tuple[int, int]] | None = None
    skip: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class GroupPage:
    name: str          # «Группа 1», «ВФ»
    specialty: str
    updated: str       # «2026-11-17 14:48» из подписи таблицы
    lessons: list[Lesson]


def _text(raw: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def _dates(text: str) -> list[tuple[int, int]]:
    return [(int(d), int(m)) for d, m in re.findall(DM, text)]


def _t(h: str, m: str) -> time | None:
    h, m = int(h), int(m)
    return time(h, m) if 7 <= h <= 22 and m < 60 else None


def _span(raw: str) -> tuple[time, time] | None:
    m = TIME.search(raw)
    if not m: return None
    a, b = _t(m[1], m[2]), _t(m[3], m[4])
    return (a, b) if a and b and a < b else None


TIME = re.compile(r"(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})")


def teacher_name(line: str) -> str | None:
    """«доц. Малинин Д.А.» → «Малинин Д.А.»; «преп. Кимбар» → «Кимбар»; не преподаватель — None."""
    s = line.strip()
    if s.lower() == "вакансия": return ""
    ranked = RANK.match(s)
    rest = s[ranked.end():].strip() if ranked else s
    rest = re.sub(r"\s+", " ", rest).replace(". ", ".").strip()
    rest = re.sub(r"([а-яё])([А-ЯЁ]\.)", r"\1 \2", rest)                # «ПетровА.А.» → «Петров А.А.»
    if ranked and rest:
        return rest
    if NAME.match(rest):
        return rest
    return None


def parse_remarks(raw: str) -> tuple[int | None, list[str]]:
    """«2н/б» → (2, ["подгр. б"]); «А,Б» → (None, ["подгр. А, Б"]); «1гр.» → (None, ["1 подгруппа"])."""
    parity, tags, letters = None, [], []
    for tok in re.split(r"[/]", raw.strip()):
        tok = tok.strip().rstrip(".")
        if not tok: continue
        if re.fullmatch(r"[12]н", tok): parity = int(tok[0]); continue
        g = re.fullmatch(r"(\d)\s*г(?:р)?", tok)
        if g: tags.append(f"{g[1]} подгруппа"); continue
        letters += [x.strip() for x in tok.split(",") if x.strip()]
    if letters:
        tags.insert(0, "подгр. " + ", ".join(letters))
    return parity, tags


def parse_cell(lines: list[str], kind: str, slot: tuple[time, time], room: str, remarks: str, weekday: int) -> Lesson | None:
    lines = [l for l in (re.sub(r"\s+", " ", x).strip() for x in lines) if l]
    if not lines: return None
    parity, tags = parse_remarks(remarks)
    title = lines[0]
    m = STREAM.match(title)
    if m:
        tags.append(f"поток {m[1]}"); title = title[m.end():]
    teachers: list[str] = []
    details: list[str] = []
    since = until = None
    only: list[tuple[int, int]] | None = None
    skip: list[tuple[int, int]] = []
    start, end = slot
    for line in lines[1:]:
        s = line
        # даты могут стоять и в конце строки преподавателя: «Мартинович И.В. (с 09.09)»
        for rx in (SINCE_UNTIL, EXCEPT, SINCE, UNTIL):
            for mm in list(rx.finditer(s)):
                if rx is SINCE_UNTIL: since, until = (int(mm[1]), int(mm[2])), (int(mm[3]), int(mm[4]))
                elif rx is EXCEPT: skip += _dates(mm[1])
                elif rx is SINCE: since = (int(mm[1]), int(mm[2]))
                else: until = (int(mm[1]), int(mm[2]))
                s = s.replace(mm[0], " ")
            s = re.sub(r"\s+", " ", s).strip()
        if not s: continue
        own = SPAN_TIME.match(s)
        # «16.11-18.11» — это даты, а не время: через точку и оба «минутных» числа похожи на месяц
        if own and not ("." in s and ":" not in s and int(own[2]) <= 12 and int(own[4]) <= 12):
            a, b = _t(own[1], own[2]), _t(own[3], own[4])
            if a and b and a < b:
                start, end = a, b; continue
        mi = INSTEAD.match(s)
        if mi:
            only = [(int(mi[1]), int(mi[2]))]; tags.append(f"вместо {int(mi[3]):02d}.{int(mi[4]):02d}"); continue
        mo = ONLY.match(s)
        if mo:
            only = (only or []) + _dates(mo[1]); continue
        if s.lower().startswith(("ул.", "ул ", "пр.", "корпус")):
            details.append(s); continue
        if INITIALS.match(s) and teachers:
            teachers[-1] = f"{teachers[-1]} {s.replace(' ', '')}"; continue
        t = teacher_name(s)
        if t is not None:
            if t: teachers.append(t)
            continue
        if SURNAME.match(s) and " " not in s and (s[0].isupper() or not teachers):
            teachers.append(s[0].upper() + s[1:]); continue
        if not teachers and not details:
            title = f"{title} {s}"
        else:
            details.append(s)
    for bad, good in TYPO.items():
        title = title.replace(bad, good)
    title = title.strip().rstrip(".")
    if title and title[0].islower(): title = title[0].upper() + title[1:]
    k = re.split(r"[./ ]", kind.strip().lower())[0] if kind.strip() else ""
    typ = "OTHER" if "/" in kind else TYPES.get(k, "OTHER")
    rooms = [room] if room else []
    return Lesson(weekday, start, end, title, typ, teachers, rooms, tags, details, parity, since, until, only, skip)


def parse_group_page(page: str) -> GroupPage | None:
    a = page.find('<section class="content-area')
    b = page.find('<section class="footer"', a)
    content = page[a:b] if a >= 0 else page
    if "<table" not in content: return None
    prog = re.search(r'<div class="programme">(.*?)</div>', content, re.S)
    cap = _text((re.search(r"<caption>(.*?)</caption>", content, re.S) or [None, ""])[1])
    code = cap.split("Обновлено")[0].strip()                          # «1-10», «4-ВФ»
    num = code.split("-", 1)[1] if "-" in code else code
    name = f"Группа {num}" if num.isdigit() else num
    upd = re.search(r"Обновлено\s*([\d\-]+\s*[\d:]*)", cap)
    lessons = []
    day = -1
    for row in re.findall(r"<tr>(.*?)</tr>", content, re.S):
        cells = dict((k, v) for k, v in re.findall(r'<td class="([^"]+)">(.*?)</td>', row, re.S))
        if not cells: continue
        dn = _text(cells.get("weekday", "")).lower()
        if dn in DAYS: day = DAYS.index(dn)
        slot = _span(_text(cells.get("time", "")))
        if day < 0 or not slot: continue
        lines = [_text(x) for x in re.split(r"<br\s*/?>", cells.get("subject-teachers", ""))]
        l = parse_cell(lines, _text(cells.get("lecture-practice", "")), slot, _text(cells.get("room", "")), _text(cells.get("remarks", "")), day)
        if l and l.title: lessons.append(l)
    return GroupPage(name, _text(prog[1]) if prog else "", upd[1].strip() if upd else "", lessons)


def content(page: str) -> str:
    """Только содержимое страницы — без меню, шапки и подвала сайта."""
    a = page.find('<section class="content-area')
    b = page.find('<section class="footer"', a)
    if a < 0: return page
    return '<html><body>' + page[a:b if b > a else len(page)] + '<section class="footer"></section></body></html>\n'


def group_links(course_page: str) -> list[tuple[str, str]]:
    """Страница курса → [(ссылка, «1 группа …»)]."""
    a = course_page.find('<section class="content-area')
    b = course_page.find('<section class="footer"', a)
    return [(h, _text(t)) for h, t in re.findall(r'<a href="([^"]+)">(.*?)</a>', course_page[a:b], re.S)]
