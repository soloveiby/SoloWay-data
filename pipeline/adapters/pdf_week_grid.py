"""
Адаптер «PDF-сетка на семестр»: так публикует расписание ФМО БГУ (fir.bsu.by) — один PDF на специальность,
на каждой странице курс: строки — дни и пары, колонки — группы. Неделя одна и та же весь семестр,
а особенности пишут прямо в ячейке:
  • «С 5.10 - Политическое страноведение – Яо Цзэни – 813» — занятие начинается с 5 октября (бывает и «по 10.11»);
  • «Иностранный язык первый (общее владение) Англ.1 Тозик О.В. – 913 Пол.(в) Круглова О.В. – 602 …» —
    общий заголовок и языковые подгруппы со своими преподавателями и аудиториями;
  • «10.00-11.20 - Беларусь в системе…» — своё время внутри пары;
  • ячейка на несколько групп — общая лекция потока.
День недели в первой колонке написан вертикально — текст приходит перевёрнутым по буквам.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time

import pdfplumber

from adapters.pdf_daily_grid import Line, Vocabulary, _join, _lines, feed_pdf  # noqa: F401  (feed_pdf — для run.py)

TIME = re.compile(r"(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})")
DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота"]
GROUP = re.compile(r"^Группа\s*(\d+\s*[А-ЯA-Z]?)\s*(\((?:в|вост)\))?", re.I)
COURSE = re.compile(r"(\d)\s*курс")
SPEC = re.compile(r"Специальность\s+([\d\- ]+)\s+(.+)")
FROM = re.compile(r"(?:^|\s)[Сс]\s*(\d{1,2})\.(\d{1,2})\s*[-–—]?\s*")
UNTIL = re.compile(r"\s*\(?\s*(?:по|до)\s*(\d{1,2})\.(\d{1,2})\s*\)?")
OWN_TIME = re.compile(r"(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})\s*[-–—]\s*")
# конец записи: « – 913», « – 8а, холл», « – 1118»
ENTRY = re.compile(r"(.+?)\s*[–—-]\s*((?:\d{1,4}[а-яa-z]?)(?:\s*,\s*холл)?|холл|спортзал|актовый зал)(?=\s|$)")
LANG = re.compile(r"\d?(?:(?:Англ|Нем|Фр|Исп|Пол|Кит|Кор|Рус|Шв|Ит|Араб|Ар|Яп|Тур|Чеш|Порт|Хинди|Перс|Вьет|Лат|Нид|Греч|Хорв|Серб|Укр|Лит|Латыш|Эст)(?:\.\s*\d*|\s*\d+)|Фарси)\s*(?:\((?:в|вост)\))?")
TEACHER = re.compile(r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.")
# список дат: «16.11», «14 и 21.11», «11.11,18.11», «17, 24.11»
DATES = r"\d{1,2}(?:\.\d{1,2})?(?:\s*(?:,|;|и)\s*\d{1,2}(?:\.\d{1,2})?)*"
ONLY = re.compile(r"^\(?\s*(" + DATES + r")\s*[-–—]\s*")
SKIP = re.compile(r"\(\s*(?:не будет\s*(" + DATES + r")|(" + DATES + r")\s*не будет)\s*\)")
NOTE = re.compile(r"\([^()]{2,80}\)")


def dates_list(text: str) -> list[tuple[int, int]]:
    """«14 и 21.11» → [(14,11),(21,11)]; день без месяца берёт месяц следующей даты."""
    parts = re.findall(r"(\d{1,2})(?:\.(\d{1,2}))?", text)
    out, month = [], None
    for d, m in reversed(parts):
        if m: month = int(m)
        if month: out.append((int(d), month))
    return list(reversed(out))


URL = re.compile(r"\(?\s*(?:\d{1,2}\.\d{1,2}\s*-\s*)?https?://\S+\)?")


@dataclass
class Lesson:
    group: str
    weekday: int
    start: time
    end: time
    title: str
    teachers: list[str]
    rooms: list[str]
    tags: list[str] = field(default_factory=list)
    since: tuple[int, int] | None = None       # (день, месяц)
    until: tuple[int, int] | None = None
    only: list[tuple[int, int]] | None = None  # «16.11 - …», «14 и 21.11 - …» — только в эти дни
    skip: list[tuple[int, int]] = field(default_factory=list)   # «(11.11,18.11 не будет)»
    note: str = ""                              # прочие пометки из ячейки — как есть, в подробности


@dataclass
class Course:
    number: int
    specialty: str
    groups: list[str]
    lessons: list[Lesson]


def _t(h, m): return time(int(h), int(m))


def norm_group(text: str) -> str | None:
    m = GROUP.match(text.strip())
    if not m: return None
    n = re.sub(r"\s+", "", m.group(1))
    return f"Группа {n}" + (" (в)" if m.group(2) else "")


def _clean(s: str) -> str:
    s = URL.sub(" ", s)
    s = re.sub(r"(\w)-\s+([а-яё])", r"\1\2", s)          # «миро- хозяйственных» → «мирохозяйственных»
    s = re.sub(r"([а-яё])-([а-яё]{2,})", r"\1\2", s)     # «информацион-ный» → «информационный»
    return re.sub(r"\s+", " ", s).strip(" ,;–—-")


def cell_text(lines: list[Line], vocab: Vocabulary) -> str:
    out = str(lines[0]) if lines else ""
    for prev, ln in zip(lines, lines[1:]):
        out = out + ln if _join(prev, ln, vocab) else out + " " + ln
    return _clean(out)


def parse_cell(text: str):
    """Текст ячейки → записи: (название, преподаватели, аудитории, метки, с, по, своё время)."""
    entries = []
    pos = 0
    header = None
    for m in ENTRY.finditer(text):
        body, room = m.group(1).strip(), m.group(2).strip()
        pos = m.end()
        since = until = own = None
        only = None; skip = []; notes = []
        for sk in SKIP.finditer(body):
            skip += dates_list(sk.group(1) or sk.group(2))
        body = SKIP.sub(" ", body)
        zm = re.match(r"^\s*(\d{1,2}\.\d{1,2})\s*(зач[её]т|экзамен)\s+", body, re.I)
        if zm: notes.append(f"{zm.group(1)} — {zm.group(2).lower()}"); body = body[zm.end():]
        om = ONLY.search(body.strip())
        if om and "." in om.group(1):
            only = dates_list(om.group(1)); body = body.strip()[om.end():]
        for n in NOTE.findall(body):
            if re.search(r"\d{1,2}\.\d{1,2}|недел|семинар|лекци|зачет|ЗАЧЕТ|ДО\b", n):
                notes.append(n.strip("() ")); body = body.replace(n, " ")
        f = FROM.search(body)
        if f and f.start() <= 2 or (f and header is None and body[:f.start()].strip() == ""):
            since = (int(f.group(1)), int(f.group(2))); body = body[:f.start()] + " " + body[f.end():]
        u = UNTIL.search(body)
        if u: until = (int(u.group(1)), int(u.group(2))); body = body[:u.start()] + " " + body[u.end():]
        o = OWN_TIME.search(body)
        if o: own = (_t(o[1], o[2]), _t(o[3], o[4])); body = body[:o.start()] + " " + body[o.end():]
        body = _clean(body)
        # языковая подгруппа: «Иностранный язык … Англ.1 Тозик О.В.» — заголовок до метки языка
        tag = None
        lm = LANG.search(body)
        if lm and (lm.start() == 0 or header is None):
            before = body[:lm.start()].strip(" –—-")
            if before and header is None and not TEACHER.search(before): header = before
            tag = re.sub(r"\s+", "", lm.group(0)).replace("(в)", " (в)")
            body = body[lm.end():].strip(" –—-")
        parts = [p.strip() for p in re.split(r"\s+[–—-]\s+", body) if p.strip()]
        teachers = []
        if tag is not None:
            teachers = TEACHER.findall(body) or ([parts[-1]] if parts else [])
            title = header or "Иностранный язык"
        else:
            if len(parts) >= 2:
                teachers = [parts[-1]]; title = " – ".join(parts[:-1])
            else:
                t = TEACHER.search(body)
                teachers = [t.group(0)] if t else []
                title = (body[:t.start()] if t else body).strip(" –—-")
        title = _clean(title)
        # обрывки («14.40», «) Нем.», «2») — не пара
        if len(title) < 4 or re.fullmatch(r"[\d\s.,:;()–—-]*", title) or title[0] in ")(,.;": continue
        tags = [tag] if tag else []
        entries.append((title, [re.sub(r"\s+", " ", x).strip() for x in teachers if x.strip()], [room], tags, since, until, own, only, skip, "; ".join(notes)))
    rest = _clean(text[pos:])
    if not entries and rest:
        # без аудитории: «Кураторский час», «День самоподготовки»…
        since = None
        f = FROM.search(rest)
        if f and f.start() <= 2: since = (int(f.group(1)), int(f.group(2))); rest = _clean(rest[f.end():])
        t = TEACHER.search(rest)
        title = _clean(rest[:t.start()] if t else rest)
        if len(title) >= 4 and not re.fullmatch(r"[\d\s.,:;()–—-]*", title) and title[0] not in ")(,.;":
            entries.append((title, [t.group(0)] if t else [], [], [], since, None, None, None, [], ""))
    return entries


def parse_pdf(path: str, vocab: Vocabulary) -> list[Course]:
    courses: list[Course] = []
    cur: Course | None = None
    groups_x: list[tuple[float, float, str]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            first = text.split("\n")[:12]
            cm = COURSE.search("\n".join(first))
            code = re.match(r"^\s*(\d)\s*[A-ZА-ЯЁ]{1,4}\s*$", first[0]) if first else None
            sm = SPEC.search(text)
            tables = page.find_tables()
            if not tables: continue
            table = max(tables, key=lambda t: len(t.cells))
            cells = [(c, _lines(page, c)) for c in table.cells]
            heads = [(c, norm_group(" ".join(ls))) for c, ls in cells if ls and norm_group(" ".join(ls))]
            number = int(cm.group(1)) if cm else (int(code.group(1)) if code else None)
            if number is not None and (cur is None or cur.number != number):
                spec = re.sub(r"\s*\(.*?\)\s*$", "", sm.group(2)).strip().capitalize() if sm else (cur.specialty if cur else "")
                cur = Course(number, spec, [], [])
                courses.append(cur)
                groups_x = []
            if cur is None: continue
            if heads:
                groups_x = sorted([(c[0], c[2], g) for c, g in heads])
                for _, _, g in groups_x:
                    if g not in cur.groups: cur.groups.append(g)
            if not groups_x: continue
            gx0 = groups_x[0][0]
            # дни (вертикальный текст в первой колонке) и пары (время во второй)
            days, slots = [], []
            for c, ls in cells:
                if not ls: continue
                if c[2] <= gx0 + 1:
                    raw = "".join(str(l) for l in ls).replace(" ", "")
                    word = raw[::-1].lower()
                    d = next((i for i, n in enumerate(DAYS) if word.startswith(n[:5]) or raw.lower().startswith(n[:5])), None)
                    if d is not None: days.append((c[1], c[3], d)); continue
                    tm = TIME.search(" ".join(ls))
                    if tm: slots.append((c[1], c[3], _t(tm[1], tm[2]), _t(tm[3], tm[4])))
            for c, ls in cells:
                if not ls or c[0] < gx0 - 1: continue
                cy = (c[1] + c[3]) / 2
                day = next((d for y0, y1, d in days if y0 - 1 <= cy <= y1 + 1), None)
                rows = [s for s in slots if s[0] < c[3] - 1 and s[1] > c[1] + 1]
                if day is None or not rows: continue
                start, end = min(r[2] for r in rows), max(r[3] for r in rows)
                covered = [g for x0, x1, g in groups_x if min(x1, c[2]) - max(x0, c[0]) > (x1 - x0) * 0.5]
                if not covered: continue
                for title, teachers, rooms, tags, since, until, own, only, skip, note in parse_cell(cell_text(ls, vocab)):
                    s, e = own or (start, end)
                    if not (s < e): s, e = start, end
                    for g in covered:
                        cur.lessons.append(Lesson(g, day, s, e, title, teachers, rooms, list(tags), since, until, only, list(skip), note))
    return courses
