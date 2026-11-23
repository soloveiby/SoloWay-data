"""
Адаптер БГЭУ (bseu.by/schedule): у страницы есть внутренний API.

  POST /schedule/  __act=__id.22.main.inpFldsA.GetForms   faculty=…            → [{value,text}] формы обучения
  POST /schedule/  __act=__id.23.main.inpFldsA.GetCourse  faculty=… form=…     → курсы
  POST /schedule/  __act=__id.23.main.inpFldsA.GetGroups  faculty=… form=… course=… → группы «25 ДГГ-1 | Специальность»
  POST /schedule/  faculty, form, course, group, period=3 (семестр), __act=…GetSchedule… → HTML-таблица на семестр

В таблице: день недели строкой, дальше пары — время, номера учебных недель «(2-18)», предмет,
тип «(Лекции)», преподаватель, «корпус/аудитория». Подгруппы — несколько строк под общей шапкой.
Номер недели переводим в дату по «Текущая — N учебная неделя» на той же странице.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import time as _time
import urllib.parse
import urllib.request
import http.cookiejar
from dataclasses import dataclass, field
from datetime import date, time, timedelta

URL = "https://bseu.by/schedule/"
UA = "SoloWay-schedule-bot (+https://github.com/soloveiby/SoloWay-data)"
ACT_FORMS = "__id.22.main.inpFldsA.GetForms"
ACT_COURSES = "__id.23.main.inpFldsA.GetCourse"
ACT_GROUPS = "__id.23.main.inpFldsA.GetGroups"
ACT_SCHED = "__id.25.main.inpFldsA.GetSchedule__sp.7.results__fp.4.main"

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
TIME = re.compile(r"(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})")
CUR_WEEK = re.compile(r"Текущая\s*-?\s*<strong>\s*(\d+)\s*</strong>\s*учебная неделя")
CALENDAR_DATE = re.compile(r"\((\d{1,2})\.(\d{1,2})\.(\d{4})\)$")
TYPES = {
    "лекци": "LECTURE", "практическ": "PRACTICE", "лаборатор": "LAB", "семинар": "SEMINAR",
}
# итоговые и прочие виды — тип OTHER, но с понятной пометкой
MARKS = {"зачет": "зачёт", "зачёт": "зачёт", "экзамен": "экзамен", "консультац": "консультация", "курсов": "курсовая"}


@dataclass
class Lesson:
    weekday: int                # 0 — понедельник
    start: time
    end: time
    weeks: list[int]
    title: str
    type: str
    teachers: list[str]
    rooms: list[str]
    tags: list[str] = field(default_factory=list)
    calendar_day: date | None = None


class Client:
    def __init__(self, delay: float = 0.4):
        self.delay = delay
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._warm = False

    def _post(self, data: dict) -> bytes:
        if not self._warm:
            self.op.open(urllib.request.Request(URL, headers={"User-Agent": UA}), timeout=40).read()
            self._warm = True
        req = urllib.request.Request(URL, data=urllib.parse.urlencode(data).encode(), headers={
            "User-Agent": UA, "Referer": URL, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
        for attempt in range(3):
            try:
                with self.op.open(req, timeout=40) as r:
                    body = r.read()
                _time.sleep(self.delay)
                return body
            except Exception:
                if attempt == 2: raise
                _time.sleep(3 * (attempt + 1))
        raise RuntimeError("unreachable")

    def forms(self, faculty): return json.loads(self._post({"__act": ACT_FORMS, "faculty": faculty}))
    def courses(self, faculty, form): return json.loads(self._post({"__act": ACT_COURSES, "faculty": faculty, "form": form}))
    def groups(self, faculty, form, course):
        return json.loads(self._post({"__act": ACT_GROUPS, "faculty": faculty, "form": form, "course": course}))

    def schedule_html(self, faculty, form, course, group) -> str:
        raw = self._post({"faculty": faculty, "form": form, "course": course, "group": group,
                          "tname": "", "period": "3", "week": "1", "__act": ACT_SCHED})
        return raw.decode("cp1251", "replace")


def parse_weeks(s: str) -> list[int]:
    """«(2-18)», «(11)», «(2,4,6)», «(1-7, 9-15)» → номера недель."""
    out: list[int] = []
    for part in re.findall(r"\d+\s*-\s*\d+|\d+", s):
        if "-" in part:
            a, b = (int(x) for x in part.split("-"))
            out += list(range(a, b + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def short_name(full: str) -> str:
    """«Сошникова Ирина Сергеевна» → «Сошникова И.С.»; уже короткие и прочие — как есть."""
    full = re.sub(r"\s+", " ", full).strip()
    p = full.split(" ")
    if len(p) == 3 and all(x[:1].isupper() and x[1:].islower() for x in p if x):
        return f"{p[0]} {p[1][0]}.{p[2][0]}."
    return full


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _kind(raw: str):
    low = raw.lower()
    for k, v in TYPES.items():
        if k in low: return v, None
    for k, v in MARKS.items():
        if k in low: return "OTHER", v
    return "OTHER", (raw.strip("() ").lower() or None)


def _desc(cell: str):
    """Ячейка «Предмет <span distype>(Тип)</span>, <span teacher>ФИО</span>»."""
    m = re.search(r'<span class="distype">(.*?)</span>', cell, re.S)
    raw_type = _text(m.group(1)) if m else ""
    teachers = [short_name(_text(t)) for t in re.findall(r'<span class="teacher[^"]*">(.*?)</span>', cell, re.S)]
    title = cell
    if m: title = title[:m.start()]
    title = _text(title).rstrip(" ,")
    return title, raw_type, [t for t in teachers if t]


def _rooms(cell: str) -> list[str]:
    t = _text(re.sub(r"<!--.*?-->", "", cell, flags=re.S))
    return [r for r in re.split(r"[ ,;]+", t) if r]


def parse_schedule(page: str) -> tuple[int | None, list[Lesson]]:
    """Разбирает обычную сетку по номерам недель и таблицы с датами занятий."""
    cw = CUR_WEEK.search(page)
    current = int(cw.group(1)) if cw else None
    i = page.find('id="sched"')
    if i < 0:
        return current, []
    table = page[i:page.find("</table>", i)]
    rows = re.findall(r"<tr>(.*?)</tr>", table, re.S)
    lessons: list[Lesson] = []
    day = None
    calendar_day = None
    head = None          # шапка пары с подгруппами: (время, недели, название, тип, метка, осталось строк)
    for row in rows:
        cells = re.findall(r"<t([dh])([^>]*)>(.*?)</t[dh]>", row, re.S)
        if not cells or cells[0][0] == "h":
            continue
        attrs0 = cells[0][1]
        if 'class="wday"' in attrs0:
            name = _text(cells[0][2]).lower()
            dated = CALENDAR_DATE.search(name)
            calendar_day = date(int(dated[3]), int(dated[2]), int(dated[1])) if dated else None
            day = calendar_day.weekday() if calendar_day else (WEEKDAYS.index(name) if name in WEEKDAYS else None)
            head = None
            continue
        if day is None:
            continue
        # строка подгруппы под общей шапкой
        if 'class="sg"' in attrs0 and head is not None:
            st, en, weeks, title, typ, mark = head
            sg = _text(cells[0][2])
            teachers = [short_name(_text(t)) for t in re.findall(r'<span class="teacher[^"]*">(.*?)</span>', cells[1][2], re.S)] if len(cells) > 1 else []
            rooms = _rooms(cells[2][2]) if len(cells) > 2 else []
            n = re.search(r"\d+", sg)
            tags = ([f"{n.group(0)} подгруппа"] if n else [sg]) + ([mark] if mark else [])
            lessons.append(Lesson(day, st, en, weeks, title, typ, [t for t in teachers if t], rooms, tags, calendar_day))
            continue
        tm = TIME.search(_text(cells[0][2]))
        desc = 1 if calendar_day else 2
        if not tm or len(cells) <= desc:
            continue
        st, en = time(int(tm[1]), int(tm[2])), time(int(tm[3]), int(tm[4]))
        weeks = [] if calendar_day else parse_weeks(_text(cells[1][2]))
        title, raw_type, teachers = _desc(cells[desc][2])
        typ, mark = _kind(raw_type)
        span = 'colspan="2"' if calendar_day else 'colspan="3"'
        if span in cells[desc][1] and len(cells) == desc + 1:
            head = (st, en, weeks, title, typ, mark)     # дальше пойдут подгруппы
            continue
        head = None
        rooms = _rooms(cells[desc + 1][2]) if len(cells) > desc + 1 else []
        lessons.append(Lesson(day, st, en, weeks, title, typ, teachers, rooms, [mark] if mark else [], calendar_day))
    return current, lessons


def week_one_monday(current_week: int, today: date) -> date:
    """Понедельник 1-й учебной недели по номеру текущей."""
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(weeks=current_week - 1)


def parse_group_title(text: str) -> tuple[str, str]:
    """«25 ДГГ-1 | Ресторанный бизнес» → («25 ДГГ-1», «Ресторанный бизнес»)."""
    name, _, spec = text.partition("|")
    return name.strip(), spec.strip()
