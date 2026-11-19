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
DAYS_EN = ["mo", "tu", "we", "th", "fr", "sa"]


def day_of(raw: str) -> int | None:
    """День из ячейки первой колонки: «П/о/н/е…» в любом порядке букв (вертикальный текст), «Понедел ьник», «Mo»."""
    letters = re.sub(r"[^а-яёa-z]", "", raw.lower())
    for i, n in enumerate(DAYS):
        if sorted(letters) == sorted(n) or letters.startswith(n[:5]) or letters[::-1].startswith(n[:5]):
            return i
    for i, n in enumerate(DAYS_EN):
        if letters[:2] == n or letters[:3] in ("mon", "tue", "wed", "thu", "fri", "sat")[i:i + 1]:
            return i
    return None
GROUP = re.compile(r"^Группа\s*(\d+\s*[А-ЯA-Z]?)\s*(\((?:в|вост)\))?", re.I)
COURSE = re.compile(r"(\d)\s*(?:-?\s*й\s*)?курс|(\d)(?:st|nd|rd|th|d)\s+Academic year", re.I)
SPEC = re.compile(r"(?:Специальность|Speciality)\s*([\d\s\-–—]+?)\s*([А-ЯЁA-Za-zа-яё«\"].+)")
FROM = re.compile(r"(?:^|\s)(?:[СсCc]|Since|since)\s*(\d{1,2})\.(\d{1,2})\s*[-–—]?\s*")
RANGE = re.compile(r"^\s*(?:[СсCc]|Since|since)\s*(\d{1,2})\.(\d{1,2})\s*[-–—]\s*(\d{1,2})\.(\d{1,2})\s*[-–—]?\s*")
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


def _t(h, m):
    try: return time(int(h), int(m))
    except ValueError: return None       # «14.70» — опечатка в расписании


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
        rg = RANGE.match(body)
        if rg:
            since, until = (int(rg[1]), int(rg[2])), (int(rg[3]), int(rg[4])); body = body[rg.end():]
        for sk in SKIP.finditer(body):
            skip += dates_list(sk.group(1) or sk.group(2))
        body = SKIP.sub(" ", body)
        zm = re.match(r"^\s*(\d{1,2}\.\d{1,2})\s*(зач[её]т|экзамен)\s+", body, re.I)
        if zm: notes.append(f"{zm.group(1)} — {zm.group(2).lower()}"); body = body[zm.end():]
        zw = re.match(r"^\s*(зач[её]т|экзамен)\s+", body, re.I)        # «зачет С 24.11 – …»
        if zw:
            notes.append(zw.group(1).lower()); body = body[zw.end():]
            rg = RANGE.match(body)
            if rg: since, until = (int(rg[1]), int(rg[2])), (int(rg[3]), int(rg[4])); body = body[rg.end():]
        om = ONLY.search(body.strip()) or re.match(r"^\(?\s*(" + DATES + r")\s+(?=[А-ЯЁA-Z])", body.strip())
        if om and "." in om.group(1):
            only = dates_list(om.group(1)); body = body.strip()[om.end():]
        for n in NOTE.findall(body):
            if re.search(r"\d{1,2}\.\d{1,2}|недел|семинар|лекци|зачет|ЗАЧЕТ|ДО\b", n):
                notes.append(n.strip("() ")); body = body.replace(n, " ")
        f = FROM.search(body) if since is None else None
        if f and f.start() <= 2 or (f and header is None and body[:f.start()].strip() == ""):
            since = (int(f.group(1)), int(f.group(2))); body = body[:f.start()] + " " + body[f.end():]
        u = UNTIL.search(body)
        if u: until = (int(u.group(1)), int(u.group(2))); body = body[:u.start()] + " " + body[u.end():]
        o = OWN_TIME.search(body)
        if o:
            body = body[:o.start()] + " " + body[o.end():]
            if _t(o[1], o[2]) and _t(o[3], o[4]): own = (_t(o[1], o[2]), _t(o[3], o[4]))
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


def parse_cell_clean(text: str):
    """parse_cell + дочистка каждой записи."""
    return [e for e in (tidy_entry(x) for x in merge_fragments(parse_cell(text))) if e is not None]


# ───────── дочистка записи: то, что не снялось в разборе ячейки (19.11, проверка перед показом в БГУ) ─────────

LEAD_DATES = re.compile(r"^\(?\s*((?:\d{1,2}(?:[./]\d{1,2})?\s*(?:,|;|и)\s*)*\d{1,2}[./]\d{1,2})\.?\s*\)?\s*[-–—]?\s*(?=\S)")
LEAD_TIME = re.compile(r"^(\d{1,2})[.:](\d{2})(?:\s*[-–—]\s*(\d{1,2})[.:](\d{2}))?\s*[-–—]?\s*(?=\S)")
LANG_ONLY = re.compile(r"^(?:ауд\.?\s*)?\d*\)?\s*\d?\s*(англ|нем|фр|исп|пол|кит|кор|рус|ит|араб|яп|тур|чеш|порт|перс|вьет)[а-я]*\.?\s*(?:\([а-я.]+\))?\s*\d*\s*(?:\((?:в|вост)\))?$", re.I)
PDF_URL = re.compile(r"\S*/\S*\.(?:pdf|php|html?)\S*\)?|\S*(?:urce|ourse|content)/\S*")
ZACHET_TAIL = re.compile(r"\s*[–—-]\s*(ЗАЧ[ЕЁ]Т|ЭКЗАМЕН)\s*$", re.I)


def _is_time(h: str, m: str) -> bool:
    """«12.50» — время (минуты > 12 или часы > 12 при круглых минутах «9.00»), «16.11» — дата."""
    return int(m) > 12 or (int(m) in (0, 30) and 7 <= int(h) <= 21) or int(h) > 31


def scrambled(title: str) -> bool:
    """Наложенные строки PDF: «Н Исе пм . . Д Ч ве ор рн кия…», «интеСлл 1е8к.т1у1а…»."""
    toks = title.split()
    if len(toks) >= 5 and sum(1 for t in toks if len(t) <= 2) / len(toks) > 0.4:
        return True
    # буквы вперемешку с цифрами внутри слова: «1е8к.т1у1а»
    return len(re.findall(r"[а-яёa-z]\d[а-яёa-z]|\d[а-яё]\d", title)) >= 2


def unmix_url(title: str) -> tuple[str, str]:
    """Ссылка наложилась на название: «обеспеhчtеtнpsи:е//» → кириллица отдельно, ссылка отдельно."""
    toks = title.split()
    mixed = [t for t in toks if re.search(r"[а-яё]", t, re.I) and re.search(r"[a-z]", t, re.I)]
    linky = any(re.search(r"t\W*t\W*p|:\W*/\W*/", t, re.I) for t in mixed)
    if len(mixed) < 2 and not linky:
        return title, ""
    url = "".join(re.sub(r"[а-яёА-ЯЁ–—]", "", t) for t in toks if re.search(r"[a-z]", t, re.I))
    url = url[:1].lower() + url[1:]
    cyr = " ".join(re.sub(r"[a-zA-Z0-9:/?=._]", "", t) for t in toks)
    return re.sub(r"\s+", " ", cyr).strip(" –—-"), (url if url.startswith("http") else "")


def tidy_entry(e):
    """(title, teachers, rooms, tags, since, until, own, only, skip, note) → то же, но без дат/времени/мусора в названии; None — выбросить."""
    title, teachers, rooms, tags, since, until, own, only, skip, note = e
    notes = [note] if note else []
    t = PDF_URL.sub(" ", title)
    t, url = unmix_url(t)
    if url: notes.append(url)
    t = re.sub(r"^[oо•·]\s+(?=[А-ЯЁA-Z])", "", t.strip())                 # «o Механизм…» — маркер списка
    for _ in range(4):
        t = t.lstrip(" ,;)")
        before = t
        zw = re.match(r"^(зач[её]т|экзамен)\s+", t, re.I)
        if zw: tags = tags + [zw.group(1).lower().replace("зачет", "зачёт")]; t = t[zw.end():]
        sm = re.match(r"^[СсCc]\s*(\d{1,2})\.(\d{1,2})\s*[-–—]?\s*", t)
        if sm and since is None: since = (int(sm[1]), int(sm[2])); t = t[sm.end():]
        tm = LEAD_TIME.match(t)
        if tm and _is_time(tm[1], tm[2]):
            st = _t(tm[1], tm[2]); en = _t(tm[3], tm[4]) if tm[3] else None
            if st and own is None: own = (st, en) if en else (st, None)
            t = t[tm.end():]
        dm = LEAD_DATES.match(t)
        if dm and not (re.fullmatch(r"\d{1,2}\.\d{2}", dm.group(1)) and _is_time(*dm.group(1).split("."))):
            ds = dates_list(dm.group(1).replace("/", "."))
            if ds: only = (only or []) + ds; t = t[dm.end():]
        if t == before: break
    # «International Economics - Chen Zhen» — преподаватель латиницей через дефис
    en = re.match(r"^(.+?)\s+-\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})$", t)
    if en and not teachers: t, teachers = en.group(1), [en.group(2)]
    zt = ZACHET_TAIL.search(t)
    if zt: tags = tags + [zt.group(1).lower().replace("зачет", "зачёт")]; t = t[:zt.start()]
    t = _clean(t)
    if scrambled(t):
        t = "Занятие (текст в расписании не читается)"
        notes.append("смотри PDF на fir.bsu.by")
    lm = LANG_ONLY.match(t)
    if lm:
        tags = tags + [re.sub(r"^(?:ауд\.?\s*)?\d*\)?\s*", "", t).replace(" ", "")]
        t = "Иностранный язык"
    # оторванный хвост соседней ячейки: «споров», «семинары» — не отдельная пара
    if t and t[0].islower() and len(t.split()) <= 2 and not LANG_ONLY.match(t):
        return None
    if t and t[0].islower() and len(t.split()) >= 2 and not re.match(r"^[а-яё]+\s*$", t):
        t = t[0].upper() + t[1:]                                        # «русский язык»
    if len(t) < 4 or re.fullmatch(r"[\d\s.,:;()–—/-]*", t) or t[0] in ")(,.;/" or re.fullmatch(r"(?:[А-ЯЁA-Z]\.\s?){1,3}", t):
        return None
    return (t, fix_teachers(teachers), rooms, tags, since, until, own, only, skip, "; ".join(n for n in notes if n))


def fix_teachers(teachers: list[str]) -> list[str]:
    out = []
    for raw in teachers:
        for p in re.split(r"\s*/\s*", raw):                              # «Радько А.А/Сюе Юйбо»
            p = re.sub(r"^\(?пр\.?\)?\s*\d*\s*", "", p.strip())           # «(пр.)2 Кузьмина-Мамедова»
            p = re.sub(r"^\d+\s+", "", p)                                  # «3 Швайба О.Г.»
            p = re.sub(r"^([А-ЯЁ])\s([а-яё]{2,})", r"\1\2", p)            # «Ш вайба О.Г.»
            p = re.sub(r"([а-яё])([А-ЯЁ]\.)", r"\1 \2", p)                # «ШиловаМ.В.», «АбазидА.М.»
            p = re.sub(r"\s+\.", ".", p)                                   # «Хромой А.В .»
            p = re.sub(r"^([А-ЯЁ][а-яё]+)\s([а-яё]{1,3})(?=\s+[А-ЯЁ]\.)", r"\1\2", p)   # «Пуньк о Т.Н.»
            p = re.sub(r"^([А-ЯЁ][а-яё]+)\s([а-яё]{3,})(?=\s+[А-ЯЁ]\.)", r"\1\2", p)     # «Реш етникова А.Н.»
            p = re.sub(r"^([А-ЯЁ]\.\s?[А-ЯЁ]\.)\s*([А-ЯЁ][а-яё-]+)$", r"\2 \1", p)       # «И.В. Жилинская»
            p = re.sub(r"^([А-ЯЁ][а-яё-]+)\.\s+", r"\1 ", p)                               # «Дрозд. А.Ф.»
            p = re.sub(r"\s([А-ЯЁ])([А-ЯЁ])\.?$", r" \1.\2.", p)                          # «Чернявский АА.»
            p = re.sub(r"(\s[А-ЯЁ]\.[А-ЯЁ])$", r"\1.", p)                                  # «Широканова Н.И»
            p = re.sub(r"\s+", " ", p).strip(" ,;–—-")
            if re.search(r"\d", p):                                          # «А З.АВЧ. –Е 8Т1 0- Сачава П.Д.» — берём последнее нормальное ФИО
                found = TEACHER.findall(p)
                if not found: continue
                p = found[-1]
            if not re.search(r"[A-Za-zА-ЯЁа-яё]{3,}", p):                                   # «В 8 .0 С2 .», «Е.В.»
                continue
            if p not in out: out.append(p)
    return out


def merge_fragments(entries: list) -> list:
    """Хвост названия, оторванный в отдельную запись («споров», «безопасности»), — к предыдущей записи."""
    out = []
    for e in entries:
        if out and e[0][:1].islower() and len(e[0].split()) <= 3 and not e[1]:
            prev = out[-1]
            out[-1] = (prev[0] + " " + e[0],) + prev[1:]
            continue
        out.append(e)
    return out


# тип пары: в PDF ФМО его почти не пишут — берём только явные пометки, и иностранный язык (всегда практика)
KIND_MARK = re.compile(r"\s*(?:\((л|лк|лек|с|сем|пр|лаб)\.?\)|[–—-]\s*(семинары?|лекции?|практика))\s*$", re.I)


def kind_of(title: str) -> tuple[str, str]:
    """«Психология конфликта (л)» → («Психология конфликта», "LECTURE")."""
    m = KIND_MARK.search(title)
    if m:
        w = (m.group(1) or m.group(2)).lower()
        kind = "LECTURE" if w.startswith("л") and not w.startswith("лаб") else "SEMINAR" if w.startswith("с") else "LAB" if w.startswith("лаб") else "PRACTICE"
        return title[:m.start()].strip(" –—-"), kind
    if re.match(r"(?:иностранный|восточный иностранный|второй иностранный|русский|белорусский) язык", title, re.I):
        return title, "PRACTICE"
    return title, "OTHER"


def parse_pdf(path: str, vocab: Vocabulary, single: str | None = None) -> list[Course]:
    """[single] — имя «группы» для PDF с одной колонкой (магистратура: одна программа на файл)."""
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
            if not cm and single and cur is None:
                cm = COURSE.search(text)        # магистратура: «(3-й семестр, 2-й курс)», «2nd Academic year»
            number = int(cm.group(1) or cm.group(2)) if cm else (int(code.group(1)) if code else (1 if single and cur is None else None))
            if number is not None and (cur is None or cur.number != number):
                spec = (sm.group(2) if sm else (cur.specialty if cur else ""))
                spec = re.sub(r"_+\s*\d{0,4}\s*г?\.?|\s*\((?:\d|1 поток|2 поток)[^)]*\)|\s{2,}.*$", "", spec).strip(" ,.–-«»\"")
                spec = spec[:1].upper() + spec[1:].lower() if spec.isupper() else spec
                # хвосты из соседней колонки шапки: подпись проректора, «…and Educational Innovations»
                spec = re.sub(r"^Code:.*?Speciality\s*[–—-]\s*", "", spec)
                spec = re.sub(r"\s+and Educational Innovations.*$|\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.\s*[А-ЯЁ][а-яё]+$|\s+(?:Проректор|Vice-Rector).*$", "", spec).strip(" ,.–-")
                cur = Course(number, spec, [], [])
                courses.append(cur)
                groups_x = []
            if cur is None: continue
            if not heads and single:
                # одна колонка: самая широкая ячейка шапки над расписанием
                top = [c for c, ls in cells if ls and re.search(r"Профилиз|Profiling|Name of subject|Специальность|Speciality", " ".join(ls))]
                if top:
                    c = max(top, key=lambda c: c[2] - c[0])
                    heads = [(c, single)]
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
                    d = day_of("".join(str(l) for l in ls))
                    if d is not None and not TIME.search(" ".join(ls)): days.append((c[1], c[3], d)); continue
                    tm = TIME.search(" ".join(ls))
                    if tm and _t(tm[1], tm[2]) and _t(tm[3], tm[4]): slots.append((c[1], c[3], _t(tm[1], tm[2]), _t(tm[3], tm[4])))
            for c, ls in cells:
                if not ls or c[0] < gx0 - 1: continue
                cy = (c[1] + c[3]) / 2
                day = next((d for y0, y1, d in days if y0 - 1 <= cy <= y1 + 1), None)
                rows = [s for s in slots if s[0] < c[3] - 1 and s[1] > c[1] + 1]
                if day is None or not rows: continue
                start, end = min(r[2] for r in rows), max(r[3] for r in rows)
                covered = [g for x0, x1, g in groups_x if min(x1, c[2]) - max(x0, c[0]) > (x1 - x0) * 0.5]
                if not covered: continue
                for title, teachers, rooms, tags, since, until, own, only, skip, note in parse_cell_clean(cell_text(ls, vocab)):
                    if own and own[1] is None: own = (own[0], end)          # «12.50 – …»: своё начало, конец пары
                    s, e = own or (start, end)
                    if not (s < e): s, e = start, end
                    for g in covered:
                        cur.lessons.append(Lesson(g, day, s, e, title, teachers, rooms, list(tags), since, until, only, list(skip), note))
    return courses
