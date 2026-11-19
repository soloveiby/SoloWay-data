"""
Сетка на семестр с датами в ячейках (магистратура радиофака БГУ, Google-таблицы).

Лист: шапка «Специальность (код)⏎гр. 60» по колонкам, слева день недели («Понедельник(Monday)») и время пары «9:00-10:20».
В ячейке одно или несколько занятий подряд, например:
  «ПЗ - 07.09 -16.11 (08:15-09:50) (чётн.нед) Прикладные задачи …, Иванов И.И.»
  «ЛК - 30.11 ИКТ,⏎Семинар -07.12-28.12 ауд.609   Менеджмент информационной безопасности, Воротницкий Ю.И.»
  «ЛК - 03.09 - 24.09, 08.10-10.12 ауд.514  Динамика движения …»
  «LB - 17.11 (all group) room 618, 08.12-05.01 (odd week) (group 3) room 81 …»
Вид занятия и даты — у каждой части свои; предмет и преподаватель обычно один на ячейку, в конце.
Даты — отдельные дни или диапазоны «с–по» (каждую неделю в этот день недели, при «чётн./нечётн.» — через неделю).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta

DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
# время внутри ячейки пишут только через двоеточие: «(08:15-09:50)», иначе спутаем с датами «07.09-16.11»
CTIME = re.compile(r"(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})")
SLOT = re.compile(r"(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})")
GROUP = re.compile(r"гр\.\s*([А-ЯA-Z]?\d+)")
KIND = re.compile(
    r"(?:(?<=^)|(?<=[,⏎/;]))\s*(ЛК\s*,\s*[Сс]еминар|ЛК\s*,\s*seminar|ЛК|ЛБ|LB|LK|ПЗ|PZ|Семинар|Seminar|seminar)\s*-\s*")
TYPES = {"лк": "LECTURE", "lk": "LECTURE", "пз": "PRACTICE", "pz": "PRACTICE", "лб": "LAB", "lb": "LAB",
         "семинар": "SEMINAR", "seminar": "SEMINAR"}
DATE_ITEM = re.compile(r"(\d{1,2})\.(\d{2})\.?(?:\s*[-–]\s*(\d{1,2})\.(\d{2})\.?)?")
PAREN = re.compile(r"\(([^()]*)\)")
PARITY = re.compile(r"чётн|четн|even", re.I), re.compile(r"нечётн|нечетн|неч\.\s*н|odd", re.I)
ROOM = re.compile(r"(?:ауд\.?|room)\s*,?\s*([0-9]+[а-яa-z]?)", re.I)
PLACES = re.compile(r"\b(ИКТ|НИИ ПФП)\b")
TEACHER_RU = re.compile(r"[А-ЯЁ][а-яё-]+\s*[А-ЯЁ]\.\s*[А-ЯЁ]?\.?$")
# «Kozlova Elena I.», «Bezrodnyi A.A», «Semenovich Sergei.N» — в конце обязательно инициал
TEACHER_EN = re.compile(r"^[A-Z][a-z-]+\s+(?:[A-Z][a-z]+[\s.]*)?[A-Z]\.?(?:\s?[A-Z]\.?)?$")
TEACHER_FULL = re.compile(r"^[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(вна|вич|ична|ич)?$")


def _is_teacher(s: str) -> bool:
    return bool(TEACHER_RU.search(s) or TEACHER_EN.match(s) or TEACHER_FULL.match(s))


def _tidy_teacher(s: str) -> str:
    s = re.sub(r"([а-яё])([А-ЯЁ]\.)", r"\1 \2", s.strip())
    return s + "." if re.search(r"(?:^|\s|\.)[А-ЯЁA-Z]$", s) else s


@dataclass
class Part:
    type: str
    dates: list[date]
    time: tuple[time, time] | None
    rooms: list[str]
    tags: list[str] = field(default_factory=list)


@dataclass
class Lesson:
    date: date
    start: time
    end: time
    title: str
    type: str
    teachers: list[str]
    rooms: list[str]
    tags: list[str]


def _year_for(month: int, term_start: date) -> int:
    """Учебный год: сентябрь–декабрь — год начала, январь–июль — следующий."""
    return term_start.year + (1 if month < 8 and term_start.month >= 8 else 0)


def _dates(spec: str, weekday: int, term_start: date, parity: int | None) -> list[date]:
    """«07.09 -16.11», «16.11, 23.11», «03.09 - 24.09, 08.10-10.12» → конкретные дни недели [weekday].
    «(чётн.нед)» после диапазона — через неделю, только для этого диапазона."""
    out: list[date] = []
    items = list(DATE_ITEM.finditer(spec))
    for k, m in enumerate(items):
        tail = spec[m.end(): items[k + 1].start() if k + 1 < len(items) else len(spec)]
        step2 = parity is not None and (PARITY[0].search(tail) or PARITY[1].search(tail) or not any(
            PARITY[0].search(spec[x.end():]) for x in items))
        try:
            a = date(_year_for(int(m[2]), term_start), int(m[2]), int(m[1]))
            b = date(_year_for(int(m[4]), term_start), int(m[4]), int(m[3])) if m[3] else None
        except ValueError:
            continue
        if b is None:
            out.append(a)
            continue
        d = a + timedelta(days=(weekday - a.weekday()) % 7)
        step = 2 if step2 else 1
        while d <= b:
            out.append(d)
            d += timedelta(weeks=step)
    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("⏎", " ")).strip(" ,.;/")


def strip_tail(rest: str) -> tuple[str, list[str], list[str]]:
    """Из хвоста ячейки убираем аудитории и места в скобках: «…, (Физический фак-т), ауд. 215/» → (текст, аудитории, пометки)."""
    rooms = ROOM.findall(rest) + PLACES.findall(rest)
    tags = [_clean(p) for p in PAREN.findall(rest) if re.search(r"фак|street|ул\.|корп|Курчатова", p, re.I)]
    rest = PAREN.sub(" ", ROOM.sub(" ", PLACES.sub(" ", rest)))
    return rest, rooms, tags


def split_teacher(rest: str) -> tuple[str, list[str]]:
    """«Безопасность баз данных, Скакун В.В.» → (предмет, [преподаватель])."""
    rest = _clean(rest)
    parts = [p.strip() for p in re.split(r",|/", rest) if p.strip()]
    teachers = []
    while len(parts) > 1 and _is_teacher(parts[-1]):
        teachers.insert(0, _tidy_teacher(parts.pop()))
    # «Shcherbak Igor N., Telecommunications Systems» — преподаватель впереди
    while len(parts) > 1 and _is_teacher(parts[0]) and not teachers:
        teachers.append(_tidy_teacher(parts.pop(0)))
    return ", ".join(parts), teachers


def parse_cell(text: str, weekday: int, slot: tuple[time, time], term_start: date) -> list[Lesson]:
    text = text.replace("\n", "⏎").strip()
    marks = list(KIND.finditer(text))
    if not marks:
        return []
    parts: list[tuple[Part, str]] = []
    for i, m in enumerate(marks):
        body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        typ = TYPES.get(re.split(r"\s*,\s*", m[1].lower())[0], "OTHER")
        tags = ["семинар"] if "," in m[1] else []
        # пометки в скобках: чётность, время, «(11.11 отмена)», «(all group)», адрес
        cancelled: list[str] = []
        parity = None
        t = None
        for p in PAREN.findall(body):
            low = p.lower()
            if PARITY[1].search(low): parity = 1
            elif PARITY[0].search(low): parity = 0
            elif CTIME.search(p) and not re.search(r"faculty|факультет", low): pass
            elif re.search(r"отмен|postpone|перенос", low): cancelled.append(p)
            elif re.search(r"group|групп", low): tags.append(_clean(p))
        sm = CTIME.search(body)
        if sm:
            t = (time(int(sm[1]), int(sm[2])), time(int(sm[3]), int(sm[4])))
        # где кончаются даты/аудитории и начинается название: после последней даты, аудитории или скобки
        spec_end = 0
        for rx in (DATE_ITEM, ROOM, PAREN, PLACES, CTIME):
            for mm in rx.finditer(body):
                # не уходим далеко: название пишут после служебной части
                if mm.start() - spec_end < 40:
                    spec_end = max(spec_end, mm.end())
        spec, rest = body[:spec_end], body[spec_end:]
        spec_wo_cancel = PAREN.sub(lambda mm: "" if re.search(r"отмен|postpone|перенос", mm[1].lower()) else mm[0], spec)
        spec_wo_slot = CTIME.sub(" ", spec_wo_cancel)
        days = _dates(spec_wo_slot, weekday, term_start, parity)
        skip = {d for c in cancelled for d in _dates(CTIME.sub(" ", c.split("to")[0].split("на")[0]), weekday, term_start, None)}
        days = [d for d in days if d not in skip]
        rooms = [r for r in ROOM.findall(spec)] + PLACES.findall(spec)
        if parity is not None: tags.append("через неделю")
        parts.append((Part(typ, sorted(set(days)), t, list(dict.fromkeys(rooms)), tags), rest))
    # предмет — из последней части, где он есть; части без названия берут его
    title, teachers = "", []
    for _, rest in reversed(parts):
        title, teachers = split_teacher(strip_tail(rest)[0])
        if len(title) >= 3: break
    title = title.lstrip("- ").strip()
    star = title.endswith("*") or "*" in title
    title = title.replace("*", "").strip(" ,")
    out: list[Lesson] = []
    for part, rest in parts:
        rest, more_rooms, more_tags = strip_tail(rest)
        part.rooms = list(dict.fromkeys(part.rooms + more_rooms))
        part.tags += more_tags
        t2, tch = split_teacher(rest)
        tt = t2.lstrip("- ").replace("*", "").strip(" ,") if len(t2) >= 3 else title
        for d in part.dates:
            if d.weekday() != weekday:
                continue
            st, en = part.time or slot
            out.append(Lesson(d, st, en, tt, part.type, tch or teachers, part.rooms, part.tags + (["доп. дисциплина"] if star else [])))
    return out


def parse_sheet(rows: list[list[str]], term_start: date) -> tuple[list[tuple[str, str]], dict[int, list[Lesson]]]:
    """Строки листа (тексты ячеек) → (группы [(имя, специальность)], пары по номеру группы)."""
    head = next((i for i, r in enumerate(rows[:15]) if sum(1 for c in r if GROUP.search(c or "")) >= 1), None)
    if head is None:
        return [], {}
    cols = []
    for j, c in enumerate(rows[head]):
        # объединённая шапка тянется на соседние колонки — берём её один раз
        if c and GROUP.search(c) and not (cols and cols[-1][1] == c and cols[-1][0] == j - 1):
            cols.append((j, c))
    groups = []
    for _, c in cols:
        g = GROUP.search(c)[1]
        spec = _clean(re.sub(r"\(\s*\d-\d{2}-\d{4}-\d{2}\s*\)|\d-\d{2}-\d{4}-\d{2}", "", c[:GROUP.search(c).start()]))
        groups.append((f"Группа {g.replace('A', 'А')}", spec))   # латинская «A» → русская
    lessons: dict[int, list[Lesson]] = {i: [] for i in range(len(cols))}
    weekday = None
    for r in rows[head + 1:]:
        name = (r[0] or "").strip().lower() if r else ""
        for i, dn in enumerate(DAYS):
            if name.startswith(dn): weekday = i
        sm = SLOT.search(r[1] or "") if len(r) > 1 else None
        if weekday is None or not sm:
            continue
        slot = (time(int(sm[1]), int(sm[2])), time(int(sm[3]), int(sm[4])))
        for gi, (j, _) in enumerate(cols):
            cell = r[j] if j < len(r) else ""
            if cell and cell.strip():
                lessons[gi] += parse_cell(cell, weekday, slot, term_start)
    return groups, lessons


def read_xlsx(data: bytes) -> dict[str, list[list[str]]]:
    """Листы xlsx → тексты ячеек (объединённые ячейки заполняем значением левой верхней)."""
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data))
    out = {}
    for ws in wb.worksheets:
        grid = [[("" if v is None else str(v)) for v in row] for row in ws.iter_rows(values_only=True)]
        for rng in ws.merged_cells.ranges:
            v = grid[rng.min_row - 1][rng.min_col - 1] if rng.min_row - 1 < len(grid) else ""
            for rr in range(rng.min_row - 1, min(rng.max_row, len(grid))):
                for cc in range(rng.min_col - 1, min(rng.max_col, len(grid[rr]))):
                    grid[rr][cc] = v
        out[ws.title] = grid
    return out
