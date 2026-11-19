"""
Адаптер МГЭУ им. А. Д. Сахарова БГУ (rsp.iseu.by/Raspisanie/TimeTable/umu.aspx).

Страница на ASP.NET WebForms: выпадающие списки ddlFac → ddlDep (форма обучения) → ddlCourse → ddlGroup → ddlWeek,
каждый выбор — «postback» (POST той же страницы со скрытым __VIEWSTATE). Кнопка ShowTT отдаёт таблицу id="TT"
на одну неделю одной группы: дата (rowspan на день), время «08:30-09:50», подгруппа, «лек. Физика»,
«доц. Луцевич Александр Александрович», «ауд. 103, К2».
"""
from __future__ import annotations

import html as htmllib
import http.cookiejar
import re
import time as _time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time

URL = "http://rsp.iseu.by/Raspisanie/TimeTable/umu.aspx"
UA = "SoloWay-schedule-bot (+https://github.com/soloveiby/SoloWay-data)"

# сокращение типа в начале названия → тип занятия приложения
TYPES = [
    ("лек.", "LECTURE"), ("практ. зан.", "PRACTICE"), ("пр. зан.", "PRACTICE"), ("лаб. зан.", "LAB"), ("лаб.", "LAB"),
    ("сем. зан.", "SEMINAR"), ("сем.", "SEMINAR"),
]
MARKS = [("зачет", "зачёт"), ("зачёт", "зачёт"), ("экзамен", "экзамен"), ("консультац", "консультация"), ("конс.", "консультация")]
POSITIONS = re.compile(r"^(?:доц\.|проф\.|пр\.|ст\.\s?пр\.|асс\.|доктор|канд\.|зав\.\s?каф\.)\s*", re.I)


@dataclass
class Lesson:
    date: date
    start: time
    end: time
    title: str
    type: str
    teachers: list[str]
    rooms: list[str]
    tags: list[str] = field(default_factory=list)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _hidden(page: str) -> dict:
    return {m[0]: htmllib.unescape(m[1]) for m in re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', page)}


def options(page: str, name: str) -> list[tuple[str, str, bool]]:
    """Пункты выпадающего списка: (значение, текст, выбран ли)."""
    m = re.search(rf'<select[^>]*name="{name}"[^>]*>(.*?)</select>', page, re.S)
    if not m:
        return []
    return [(htmllib.unescape(v), _text(t), "selected" in a) for a, v, t in re.findall(r'<option([^>]*)value="([^"]*)"[^>]*>(.*?)</option>', m.group(1), re.S)]


def selected(page: str, name: str) -> str | None:
    opts = options(page, name)
    return next((v for v, _, s in opts if s), opts[0][0] if opts else None)


class Client:
    def __init__(self, delay: float = 0.3):
        self.delay = delay
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.page = ""

    def _open(self, data: dict | None) -> str:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(URL, data=body, headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Referer": URL})
        for attempt in range(4):
            try:
                with self.op.open(req, timeout=60) as r:
                    text = r.read().decode("utf-8", "replace")
                _time.sleep(self.delay)
                return text
            except Exception:
                if attempt == 3: raise
                _time.sleep(5 * (attempt + 1))
        raise RuntimeError("unreachable")

    def start(self) -> str:
        self.page = self._open(None)
        return self.page

    def _form(self, **over) -> dict:
        data = _hidden(self.page)
        for name in ("ddlFac", "ddlDep", "ddlCourse", "ddlGroup", "ddlWeek"):
            v = selected(self.page, name)
            if v is not None: data[name] = v
        data.update(over)
        return data

    def postback(self, target: str, **values) -> str:
        """Выбрать значение в списке target (и что надо в остальных) — как будто его поменяли руками."""
        self.page = self._open(self._form(__EVENTTARGET=target, __EVENTARGUMENT="", **values))
        return self.page

    def timetable(self, group: str, week: str) -> str:
        """Таблица одной группы на неделю (week — значение из ddlWeek, «16.11.2026 0:00:00»)."""
        return self._open(self._form(__EVENTTARGET="", ddlGroup=group, ddlWeek=week, ShowTT="Показать"))


def week_value(monday: date) -> str:
    return f"{monday:%d.%m.%Y} 0:00:00"


def short_name(full: str) -> str:
    """«доц. Луцевич Александр Александрович» → «Луцевич А.А.»."""
    full = POSITIONS.sub("", re.sub(r"\s+", " ", full).strip())
    p = full.split(" ")
    if len(p) >= 3 and p[1][:1].isupper() and p[2][:1].isupper():
        return f"{p[0]} {p[1][0]}.{p[2][0]}."
    return full


def _kind(title: str):
    low = title.lower()
    for k, v in TYPES:
        if low.startswith(k):
            return v, title[len(k):].strip(), None
    for k, v in MARKS:
        if k in low:
            return "OTHER", re.sub(rf"^{re.escape(k)}\w*\.?\s*", "", title, flags=re.I).strip() or title, v
    return "OTHER", title, None


def _rooms(cell: str) -> list[str]:
    """«ауд. 103, К2» → «103 К2»; пустое «ауд. , » → нет аудитории."""
    t = _text(cell)
    m = re.match(r"ауд\.\s*([^,]*),\s*(.*)$", t)
    if not m:
        return [t] if t else []
    room, building = m.group(1).strip(), m.group(2).strip()
    if not room:
        return [building] if building else []
    return [f"{room} {building}".strip()]


def parse_timetable(page: str) -> list[Lesson]:
    i = page.find('id="TT"')
    if i < 0:
        return []
    table = page[i:page.find("</table>", i)]
    out: list[Lesson] = []
    day: date | None = None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = {m[0]: m[1] for m in re.findall(r'<td class="cell-(date|time|subgroup|discipline|staff|auditory)"[^>]*>(.*?)</td>', row, re.S)}
        if "date" in cells:
            m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", cells["date"])
            day = date(int(m[3]), int(m[2]), int(m[1])) if m else None
        if day is None or "time" not in cells:
            continue
        tm = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", _text(cells["time"]))
        if not tm:
            continue
        disc = re.sub(r"<span class='topic'>.*?</span>", "", cells.get("discipline", ""), flags=re.S)
        raw = _text(disc)
        if not raw:
            continue
        typ, title, mark = _kind(raw)
        topic = _text(" ".join(re.findall(r"<span class='topic'>(.*?)</span>", cells.get("discipline", ""), re.S)))
        sub = _text(cells.get("subgroup", ""))
        # подгруппа бывает в своей колонке («1») или в квадратных скобках в названии («[1п/гр]»)
        br = re.search(r"\[\s*(\d+)\s*п\s*/\s*гр\.?\s*\]", title)
        if br:
            title = (title[:br.start()] + title[br.end():]).strip()
            sub = sub or br.group(1)
        tags = ([f"{sub} подгруппа" if sub.isdigit() else sub] if sub else []) + ([mark] if mark else []) + ([topic] if topic else [])
        teacher = _text(cells.get("staff", ""))
        out.append(Lesson(day, time(int(tm[1]), int(tm[2])), time(int(tm[3]), int(tm[4])), title, typ,
                          [short_name(teacher)] if teacher else [], _rooms(cells.get("auditory", "")), tags))
    return out


def merge_parallel(lessons: list[Lesson]) -> list[Lesson]:
    """Иностранный язык: одна пара, несколько преподавателей и аудиторий в одно время → одна карточка."""
    out: list[Lesson] = []
    for l in lessons:
        same = next((o for o in out if o.date == l.date and o.start == l.start and o.title == l.title and o.type == l.type and o.tags == l.tags), None)
        if same:
            same.teachers += [t for t in l.teachers if t not in same.teachers]
            same.rooms += [r for r in l.rooms if r not in same.rooms]
        else:
            out.append(l)
    return out
