"""
Адаптер «PDF-таблица на день»: строки — группы, колонки — пары («1 пара … 7 пара» со временем звонков в шапке).
Так публикует расписание Минский радиотехнический колледж (mrk.bsuir.by): каждый день отдельный PDF из Word.

Что встречается в таблице и как это разбирается:
  • ячейка на всю пару — обычное занятие;
  • пара поделена пополам по горизонтали — две подгруппы (левая — 1-я, правая — 2-я);
  • ячейка на несколько пар — одно длинное занятие (практика) с явным временем внутри («13:40-19:10»);
  • над ячейкой узкая ячейка «13:40-14:00» — особое время для занятия под ней (информационный час);
  • переносы посреди слова («Математик / а», «Соколовска / я Цедрик») склеиваются.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time

import pdfplumber

PAIR_HEAD = re.compile(r"^\s*(\d)\s*пара\s*$")
TIME_RANGE = re.compile(r"(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})")
DAY_HEAD = re.compile(r"на\s+(\d{2})\.(\d{2})\.(\d{4})(?:\s*\((\w+)\))?")
SUBGROUP = re.compile(r"(\d)\s*подгрупп\w*", re.I)
# аудитории: «313», «406а», «2к11» (корпус 2, ауд. 11), «2-3», «с/з», «а/з», «ЧЗ» (читальный зал)
ROOM_TOKEN = re.compile(r"^(?:\d{1,4}[а-яa-z]?|\d+к\d+[а-я]?|\d+-\d+|[а-я]{1,3}/[а-я]{1,3}|с/з|а/з|чз|спортзал|тир)$", re.I)
ROOM_WORDS = {"ауд", "ауд.", "корпус", "корп.", "к."}
ABBREV = re.compile(r"^[А-ЯЁA-Z]{2,}[а-яё]{0,2}[А-ЯЁA-Z]*$")
# фамилия преподавателя: «Будник», «Щербакова-Шаблова», можно с инициалами «Дерман У.В.»
TEACHER = re.compile(r"^[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?(?:\s*[А-ЯЁ]\.(?:\s*[А-ЯЁ]\.)?)?$")


@dataclass
class Lesson:
    group: str
    start: time
    end: time
    title: str
    teachers: list[str]
    rooms: list[str]
    tags: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    def key(self):
        return (self.start, self.end, self.title, tuple(self.tags))


@dataclass
class DaySchedule:
    date: date
    parity: str | None          # «числитель» / «знаменатель»
    groups: list[str]
    lessons: list[Lesson]
    warnings: list[str]


def _t(h: str, m: str) -> time:
    return time(int(h), int(m))


def _time_range(s: str):
    m = TIME_RANGE.search(s)
    return (_t(m[1], m[2]), _t(m[3], m[4])) if m else None


class Line(str):
    """Строка ячейки + заполнила ли она ячейку по ширине (тогда на ней мог быть перенос посреди слова)."""
    full: bool = False


# текст в ячейках выровнен по центру; поле у ячейки ~5.4pt, строка «во всю ширину» оставляет по краю меньше 8pt
FULL_MARGIN = 8.0


def _lines(page, box) -> list[Line]:
    out = []
    for ln in page.crop(box).extract_text_lines():
        text = ln["text"].strip()
        if not text:
            continue
        line = Line(text)
        line.full = (ln["x0"] - box[0]) < FULL_MARGIN and (box[2] - ln["x1"]) < FULL_MARGIN + 2
        out.append(line)
    return out


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


class Vocabulary:
    """
    Что встречалось в расписаниях целиком: слова (чтобы отличить перенос посреди слова от пробела)
    и строки (чтобы понять, что «История / Беларуси» — одно название, а не предмет и преподаватель).
    Копится по всем PDF за прогон: в широкой ячейке «История Беларуси» стоит в одну строку.
    """

    def __init__(self):
        self.words: set[str] = set()
        self.phrases: set[str] = set()

    def feed(self, lines: list[Line]):
        prev_full = False
        for ln in lines:
            toks = ln.split()
            for i, w in enumerate(toks):
                # обрывками могут быть: последнее слово строки во всю ширину и первое слово строки после неё
                if (i == len(toks) - 1 and getattr(ln, "full", False)) or (i == 0 and prev_full):
                    continue
                self.words.add(w.lower().strip(",.;"))
            if len(toks) > 1 and not getattr(ln, "full", False) and not prev_full:
                self.phrases.add(ln.lower())
            prev_full = getattr(ln, "full", False)

    def known(self, w: str) -> bool:
        return w.lower().strip(",.;") in self.words

    def phrase(self, s: str) -> bool:
        return s.lower() in self.phrases


def _join(prev: Line, ln: str, vocab: Vocabulary) -> bool:
    """
    Склеивать ли строку с предыдущей без пробела (перенос посреди слова).
    Перенос бывает, только когда прошлая строка заняла всю ширину ячейки и новая начинается со строчной;
    если новое слово само по себе известно («подготовка»), а склейки такой нет — это просто граница слов.
    """
    if not prev.split() or not ln.split():
        return False
    if prev.split()[-1].endswith("-") and len(prev.split()[-1]) > 2:
        return True
    if not getattr(prev, "full", False):
        return False
    last, first = prev.split()[-1], ln.split()[0]
    if last.endswith("-") and len(last) > 2:
        return True     # «Щербакова- / Шаблова», «Интернет- / приложений»
    if not first[:1].islower():
        return False
    return vocab.known(last + first) or not vocab.known(first)


def _join_title(lines: list[Line], vocab: Vocabulary) -> str:
    out = str(lines[0])
    for prev, ln in zip(lines, lines[1:]):
        out = out + ln if _join(prev, ln, vocab) else out + " " + ln
    return re.sub(r"\s+", " ", out).strip()


def _split_teachers(lines: list[Line]) -> list[str]:
    people: list[str] = []
    prev = None
    for ln in lines:
        toks = ln.split()
        if not toks:
            continue
        if people and (toks[0][:1].islower() or (prev is not None and getattr(prev, "full", False) and len(toks[0]) <= 2)):
            # «Соколовска / я Цедрик»: первое слово — окончание фамилии с прошлой строки
            people[-1] += toks[0]
            toks = toks[1:]
        people += toks
        prev = ln
    return [p.strip(",;") for p in people if p.strip(",;")]


def _rooms_tail(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Отрезает с конца строки аудитории: «Цедрик с/з» → (["Цедрик"], ["с/з"])."""
    rooms: list[str] = []
    toks = [t.strip(",;") for t in tokens if t.strip(",;")]
    while toks and (ROOM_TOKEN.match(toks[-1]) or toks[-1].lower() in ROOM_WORDS):
        t = toks.pop()
        if t.lower() not in ROOM_WORDS:
            rooms.insert(0, t)
    return toks, rooms


def parse_cell(lines: list[Line], vocab: Vocabulary):
    """Текст ячейки → (название, преподаватели, аудитории, метки, подробности, явное время)."""
    explicit = None
    tags: list[str] = []
    rest: list[Line] = []
    for ln in lines:
        text = str(ln)
        tr = _time_range(text)
        if tr:
            explicit = tr
            text = TIME_RANGE.sub("", text)
        m = SUBGROUP.search(text)
        if m:
            tags.append(f"{m[1]} подгруппа")
            text = SUBGROUP.sub("", text)
        text = re.sub(r"\s+", " ", text).strip(" ,;()")
        if text:
            nl = Line(text)
            nl.full = getattr(ln, "full", False)
            rest.append(nl)
    lines = rest
    if not lines:
        return None
    # аудитории — в конце последней строки («406а 406б», «с/з, а/з», «ауд 218», «Цедрик с/з»)
    toks, rooms = _rooms_tail(lines[-1].replace(",", " ").split())
    if rooms:
        if toks:
            nl = Line(" ".join(toks)); nl.full = getattr(lines[-1], "full", False); lines[-1] = nl
        else:
            lines = lines[:-1]
    if not lines:
        return None
    # название — первая строка и её продолжения: со строчной буквы или скобки, перенос посреди слова,
    # или если такое сочетание где-то стоит одной строкой («История Беларуси»); дальше — преподаватели
    n = 1
    while n < len(lines):
        nxt = lines[n]
        abbrev = all(ABBREV.match(w) for w in nxt.split())     # «Испытания и контроль качества / МЭУ»
        if nxt[:1].islower() or nxt[:1] in "(«\"" or abbrev or _join(lines[n - 1], nxt, vocab) or vocab.phrase(_join_title(lines[:n], vocab) + " " + nxt):
            n += 1
        else:
            break
    title = _join_title(lines[:n], vocab)
    # преподаватели — слова-фамилии; всё прочее («завод», «экскурсия», ОАО «Пеленг») — в подробности
    teachers: list[str] = []
    details: list[str] = []
    prev = lines[n - 1]
    for ln in lines[n:]:
        words = ln.split()
        if teachers and _join(prev, ln, vocab):
            teachers[-1] += words[0]
            words = words[1:]
        other = []
        for w in words:
            w = w.strip(",;")
            if TEACHER.match(w):
                teachers.append(w)
            elif w:
                other.append(w)
        if other:
            details.append(" ".join(other))
        prev = ln
    return title, teachers, rooms, tags, details, explicit


def feed_pdf(path: str, vocab: Vocabulary) -> None:
    """Первый проход: копим словарь по всем PDF прогона, прежде чем разбирать каждый."""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for t in page.find_tables()[:1]:
                for c in t.cells:
                    vocab.feed(_lines(page, c))


def parse_pdf(path: str, vocab: Vocabulary | None = None) -> DaySchedule:
    vocab = vocab or Vocabulary()
    warnings: list[str] = []
    lessons: list[Lesson] = []
    groups: list[str] = []
    day = None
    parity = None
    with pdfplumber.open(path) as pdf:
        pages = []
        for page in pdf.pages:
            if day is None:
                m = DAY_HEAD.search(page.extract_text() or "")
                if m:
                    day = date(int(m[3]), int(m[2]), int(m[1]))
                    parity = (m[4] or "").lower() or None
            tables = page.find_tables()
            if not tables:
                continue
            cells = [(c, _lines(page, c)) for c in tables[0].cells]
            pages.append((page, cells))
            for _, ls in cells:
                vocab.feed(ls)
        if day is None:
            raise ValueError("не нашёл дату в шапке")

        for page, cells in pages:
            # шапка: колонки пар и время звонков
            pairs = {}
            for c, ls in cells:
                if len(ls) == 1 and PAIR_HEAD.match(ls[0]):
                    pairs[int(PAIR_HEAD.match(ls[0])[1])] = [c[0], c[2], c[3], None]
            if not pairs:
                continue  # страница факультативов и т. п.
            head_bottom = max(v[2] for v in pairs.values())
            for c, ls in cells:
                if abs(c[1] - head_bottom) < 1 and ls and _time_range(ls[0]):
                    for n, v in pairs.items():
                        if _overlap(c[0], c[2], v[0], v[1]) > (v[1] - v[0]) * 0.6:
                            v[3] = _time_range(ls[0])
            # строка звонков под «N пара»; ниже начинаются группы
            table_body = max(c[3] for c, ls in cells if ls and _time_range(ls[0]) and abs(c[1] - head_bottom) < 1)
            left = min(v[0] for v in pairs.values())

            # группы — ячейки левой колонки
            labels = []
            for c, ls in cells:
                if c[2] <= left + 1 and c[1] >= table_body - 1 and ls:
                    name = " ".join(ls).strip()
                    labels.append((c[1], c[3], name))
                    if name not in groups:
                        groups.append(name)

            def group_of(c):
                cy = (c[1] + c[3]) / 2
                for y0, y1, name in labels:
                    if y0 - 0.5 <= cy <= y1 + 0.5:
                        return name
                return None

            body = [(c, ls) for c, ls in cells if c[0] >= left - 1 and c[1] >= table_body - 1 and ls]
            # узкие ячейки «13:40-14:00» над занятием задают ему время
            overrides = {}
            for c, ls in body:
                if len(ls) == 1 and _time_range(ls[0]) and TIME_RANGE.sub("", ls[0]).strip() == "":
                    overrides[c] = _time_range(ls[0])
            for c, ls in body:
                if c in overrides:
                    continue
                g = group_of(c)
                if g is None:
                    warnings.append(f"{day}: ячейка без группы: {' / '.join(ls)}")
                    continue
                covered = sorted(n for n, v in pairs.items() if _overlap(c[0], c[2], v[0], v[1]) > (v[1] - v[0]) * 0.25)
                if not covered:
                    continue
                parsed = parse_cell(ls, vocab)
                if not parsed:
                    continue
                title, teachers, rooms, tags, details, explicit = parsed
                # время: явное в тексте → узкая ячейка над → звонки покрытых пар
                over = next((t for oc, t in overrides.items() if _overlap(oc[0], oc[2], c[0], c[2]) > 1 and abs(oc[3] - c[1]) < 1.5), None)
                if explicit or over:
                    start, end = explicit or over
                else:
                    first, last = pairs[covered[0]][3], pairs[covered[-1]][3]
                    if not first or not last:
                        warnings.append(f"{day}: нет времени пары для {g}")
                        continue
                    start, end = first[0], last[1]
                # половина пары по ширине — подгруппа
                if len(covered) == 1 and not tags:
                    v = pairs[covered[0]]
                    width = v[1] - v[0]
                    if c[2] - c[0] < width * 0.75:
                        tags.append("1 подгруппа" if (c[0] + c[2]) / 2 < (v[0] + v[1]) / 2 else "2 подгруппа")
                lessons.append(Lesson(g, start, end, title, teachers, rooms, tags, details))
    lessons.sort(key=lambda l: (l.group, l.start, l.tags))
    return DaySchedule(day, parity, groups, lessons, warnings)
