"""
Обновляет расписания всех источников из sources/*.json и пишет готовые файлы для приложения в feeds/<id>/.

Запуск: python pipeline/run.py [--offline]   (--offline — без скачивания, только пересобрать из того, что уже есть)
GitHub Actions запускает его каждые 30 минут (см. .github/workflows/update.yml).

Что лежит в репозитории:
  sources/<id>.json          — откуда и как брать расписание (страница, шаблон ссылок, адаптер)
  state/<id>/pdf/<дата>.pdf   — скачанные файлы (чтобы не качать заново и было на чём проверить разбор)
  state/<id>/sources.json     — дата → ссылка и контрольная сумма файла
  feeds/<id>/index.json       — что есть: курсы, группы, дни, когда обновлено
  feeds/<id>/<курс>.json      — расписание курса в формате приложения (soloweek-schedule)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time as _time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapters.pdf_daily_grid import DaySchedule, Vocabulary, feed_pdf, parse_pdf  # noqa: E402

UA = "SoloWay-schedule-bot (+https://github.com/soloveiby/SoloWay-data)"
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def fetch(url: str, tries: int = 4) -> bytes:
    """Сайты вузов бывают медленными: несколько попыток с растущей паузой."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception:
            if attempt == tries - 1: raise
            _time.sleep(10 * (attempt + 1))
    raise RuntimeError("unreachable")


class Unreachable(Exception):
    """Сайт не ответил — это не поломка разбора: оставляем прошлые данные и пробуем в следующий раз."""


GRACE_HOURS = 36   # столько терпим недоступность сайта, прежде чем прогон покраснеет и придёт письмо


def network(sid: str, meta_path: Path, fn):
    """Сетевую часть источника выполняем «мягко»: ошибка сети → предупреждение, пока сайт молчит меньше GRACE_HOURS."""
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    try:
        result = fn()
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else meta
        meta["lastOk"] = datetime.now(timezone.utc).isoformat()
        dump(meta_path, meta)
        return result
    except (OSError, urllib.error.URLError, TimeoutError) as e:
        ok = meta.get("lastOk")
        hours = (datetime.now(timezone.utc) - datetime.fromisoformat(ok)).total_seconds() / 3600 if ok else 0
        if ok and hours > GRACE_HOURS:
            raise RuntimeError(f"{sid}: сайт недоступен уже {hours:.0f} ч ({e})")
        raise Unreachable(f"{sid}: сайт не ответил ({e}) — оставляем прошлые данные")


def dump(path: Path, data, compact: bool = False) -> bool:
    """Пишет JSON, только если содержимое поменялось (иначе каждая проверка давала бы коммит)."""
    text = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(data, ensure_ascii=False, indent=1)) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def course_of(group: str, cfg: dict, today: date) -> int | None:
    """«6К9091» → 1 курс: первая цифра — год поступления (2026), учебный год начинается 1 сентября."""
    rule = cfg.get("courses")
    if not rule:
        return None
    m = re.match(rule["pattern"], group)
    if not m:
        return None
    year = rule["yearBase"] + int(m[1])
    start = today.year if today.month >= 9 else today.year - 1
    n = start - year + 1
    return n if 1 <= n <= 6 else None


def week_title(days: list[date]) -> str:
    a, b = days[0], days[-1]
    if a.month == b.month:
        return f"{a.day}–{b.day} {MONTHS[b.month - 1]}"
    return f"{a.day} {MONTHS[a.month - 1]} – {b.day} {MONTHS[b.month - 1]}"


def lesson_json(l, shared: list[int]) -> dict:
    return {
        "start": l.start.strftime("%H:%M"), "end": l.end.strftime("%H:%M"),
        "title": l.title, "type": "OTHER",
        "teachers": l.teachers, "rooms": l.rooms, "tags": l.tags, "details": l.details,
        "cancelled": False, "sharedWith": shared,
    }


def build_feed(days: list[DaySchedule], groups: list[str]) -> dict:
    """Дни → формат приложения: недели с понедельника, группы по колонкам, как у таблиц вузов."""
    index = {g: i for i, g in enumerate(groups)}
    group_list = [{"index": i, "name": g, "code": "", "specialty": "", "profiles": []} for i, g in enumerate(groups)]
    by_week: dict[date, list[DaySchedule]] = {}
    for d in sorted(days, key=lambda d: d.date):
        by_week.setdefault(d.date - timedelta(days=d.date.weekday()), []).append(d)
    weeks = []
    for monday, ds in sorted(by_week.items()):
        out_days = []
        for d in ds:
            per: dict[str, list] = {}
            for l in d.lessons:
                if l.group in index:
                    per.setdefault(str(index[l.group]), []).append(lesson_json(l, [index[l.group]]))
            for v in per.values():
                v.sort(key=lambda x: (x["start"], x["tags"]))
            out_days.append({"date": d.date.isoformat(), "parity": d.parity, "lessons": per})
        weeks.append({"title": week_title([d.date for d in ds]), "groups": group_list, "days": out_days})
    return {"format": "soloweek-schedule", "version": 1, "weeks": weeks}


def run_pdf_daily(cfg: dict, offline: bool) -> list[str]:
    sid = cfg["id"]
    state = ROOT / "state" / sid
    pdf_dir = state / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    known_path = state / "sources.json"
    known = json.loads(known_path.read_text(encoding="utf-8")) if known_path.exists() else {}
    log = []
    today = datetime.now(timezone.utc).date()

    # 1. какие файлы сейчас на странице
    if not offline:
        try:
            html = network(sid, state / "net.json", lambda: fetch(cfg["page"])).decode("utf-8", "replace")
        except Unreachable as e:
            log.append(f"⚠ {e}")
            offline = True
    if not offline:
        links = {}
        for m in re.finditer(cfg["link"], html):
            dd, mm, yyyy = m["date"].split(".")
            links[f"{yyyy}-{mm}-{dd}"] = cfg["base"] + m[0]
        if not links:
            raise RuntimeError(f"{sid}: на странице не нашлось ни одной ссылки — сайт поменялся?")
        # 2. новые и заменённые файлы (старше окна хранения не качаем вовсе)
        horizon = today - timedelta(days=cfg.get("keepDays", 21))
        for day, url in sorted(links.items()):
            if date.fromisoformat(day) < horizon:
                continue
            if known.get(day, {}).get("url") == url and (pdf_dir / f"{day}.pdf").exists():
                continue
            data = fetch(url)
            sha = hashlib.sha256(data).hexdigest()
            if known.get(day, {}).get("sha") != sha:
                (pdf_dir / f"{day}.pdf").write_bytes(data)
                log.append(f"{sid}: {'обновлён' if day in known else 'новый'} день {day}")
            known[day] = {"url": url, "sha": sha}
            _time.sleep(1)
        dump(known_path, known)

    # 3. старое не храним: окно в keepDays дней назад
    horizon = today - timedelta(days=cfg.get("keepDays", 21))
    for f in pdf_dir.glob("*.pdf"):
        if date.fromisoformat(f.stem) < horizon:
            f.unlink()
            known.pop(f.stem, None)
    dump(known_path, known)

    # 4. разбор: сначала копим словарь по всем файлам, потом разбираем каждый
    files = sorted(pdf_dir.glob("*.pdf"))
    vocab = Vocabulary()
    for f in files:
        feed_pdf(str(f), vocab)
    days: list[DaySchedule] = []
    warnings: list[str] = []
    for f in files:
        try:
            d = parse_pdf(str(f), vocab)
            days.append(d)
            warnings += d.warnings
        except Exception as e:  # один битый файл не должен ломать остальные дни
            warnings.append(f"{f.name}: не разобрался: {e}")

    # 5. раскладываем по курсам и пишем файлы для приложения
    out = ROOT / "feeds" / sid
    by_course: dict[int, set[str]] = {}
    for d in days:
        for g in d.groups:
            c = course_of(g, cfg, today)
            if c is not None:
                by_course.setdefault(c, set()).add(g)
    courses = []
    changed = False
    for c, groups in sorted(by_course.items()):
        gl = sorted(groups)
        feed = build_feed(days, gl)
        if dump(out / f"{c}.json", feed, compact=True):
            changed = True
            log.append(f"{sid}: обновлён {c} курс")
        courses.append({"name": f"{c} курс", "id": f"{c}", "groups": gl})
    index = {
        "format": "soloweek-feed", "version": 1, "id": sid,
        "days": [d.date.isoformat() for d in sorted(days, key=lambda d: d.date)],
        "courses": courses, "warnings": warnings,
    }
    old = json.loads((out / "index.json").read_text(encoding="utf-8")) if (out / "index.json").exists() else {}
    index["updatedAt"] = old.get("updatedAt") if not changed and old.get("days") == index["days"] else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dump(out / "index.json", index)
    return log + [f"⚠ {w}" for w in warnings]


# ───────────────────────── БГЭУ: внутренний API сайта ─────────────────────────

def run_bseu(cfg: dict, offline: bool) -> list[str]:
    from adapters import bseu_api as B
    sid = cfg["id"]
    state = ROOT / "state" / sid
    gdir = state / "groups"
    gdir.mkdir(parents=True, exist_ok=True)
    meta_path = state / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    log: list[str] = []
    now = datetime.now(timezone.utc)
    today = (now + timedelta(hours=3)).date()              # Минск
    # семестр меняется редко — полный обход раз в everyHours, остальные запуски пропускаем
    last = meta.get("lastRun")
    if not offline and last and (now - datetime.fromisoformat(last)).total_seconds() < cfg.get("everyHours", 11) * 3600:
        return []
    groups_meta: dict[str, dict] = meta.get("groups", {})
    def crawl():
        nonlocal groups_meta
        c = B.Client(delay=cfg.get("delay", 0.4))
        seen: dict[str, dict] = {}
        failures = 0
        for fac_id, fac in cfg["faculties"].items():
            for fm in c.forms(fac_id):
                if fm["text"] not in cfg.get("forms", ["Дневная"]):
                    continue
                for co in c.courses(fac_id, fm["value"]):
                    for g in c.groups(fac_id, fm["value"], co["value"]):
                        gid = g["value"]
                        name, spec = B.parse_group_title(g["text"])
                        try:
                            page = c.schedule_html(fac_id, fm["value"], co["value"], gid)
                            cur, lessons = B.parse_schedule(page)
                        except Exception as e:
                            failures += 1
                            log.append(f"⚠ {sid}: {name}: не скачалась ({e}) — оставляем прошлое")
                            if gid in groups_meta: seen[gid] = groups_meta[gid]
                            continue
                        if cur: meta["currentWeek"] = cur; meta["currentWeekAt"] = today.isoformat()
                        data = [dict(d=l.weekday, s=l.start.strftime("%H:%M"), e=l.end.strftime("%H:%M"), w=l.weeks, t=l.title,
                                     k=l.type, p=l.teachers, r=l.rooms, g=l.tags) for l in lessons]
                        if dump(gdir / f"{gid}.json", data): log.append(f"{sid}: обновлена {name}")
                        seen[gid] = {"name": name, "spec": spec, "fac": fac_id, "course": int(co["value"])}
        if failures and not any(groups_meta.get(g) is not v for g, v in seen.items()):
            raise OSError(f"ни одна группа не скачалась ({failures})")      # сайт лёг — это сеть, не разбор
        if failures > len(seen) // 2:
            raise RuntimeError(f"{sid}: слишком много ошибок ({failures}) — сайт поменялся?")
        # группы, которых больше нет на сайте, убираем
        for gid in set(groups_meta) - set(seen):
            (gdir / f"{gid}.json").unlink(missing_ok=True)
        groups_meta = seen
        meta["groups"] = groups_meta
        meta["lastRun"] = now.isoformat()
        dump(meta_path, meta)

    if not offline:
        try:
            network(sid, meta_path, crawl)
        except Unreachable as e:
            log.append(f"⚠ {e}")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else meta
        groups_meta = meta.get("groups", groups_meta)

    cur, cur_at = meta.get("currentWeek"), meta.get("currentWeekAt")
    if not cur:
        raise RuntimeError(f"{sid}: не знаю текущую учебную неделю")
    week1 = B.week_one_monday(cur, date.fromisoformat(cur_at))
    # раскладываем по факультетам и курсам
    by_fc: dict[tuple[str, int], list[str]] = {}
    for gid, g in groups_meta.items():
        by_fc.setdefault((g["fac"], g["course"]), []).append(gid)
    index = {"format": "soloweek-feed", "version": 1, "id": sid, "week1": week1.isoformat(), "faculties": []}
    for fac_id, fac in cfg["faculties"].items():
        courses = []
        for (f, course), gids in sorted(by_fc.items()):
            if f != fac_id: continue
            gids.sort(key=lambda g: groups_meta[g]["name"])
            groups = [{"index": i, "name": groups_meta[g]["name"], "code": "", "specialty": groups_meta[g]["spec"], "profiles": []} for i, g in enumerate(gids)]
            per_group = [json.loads((gdir / f"{g}.json").read_text(encoding="utf-8")) if (gdir / f"{g}.json").exists() else [] for g in gids]
            max_week = max([w for lessons in per_group for l in lessons for w in l["w"]] or [1])
            weeks = []
            for wk in range(1, max_week + 1):
                monday = week1 + timedelta(weeks=wk - 1)
                days = []
                for d in range(6):
                    per: dict[str, list] = {}
                    for gi, lessons in enumerate(per_group):
                        for l in lessons:
                            if l["d"] == d and wk in l["w"]:
                                per.setdefault(str(gi), []).append({
                                    "start": l["s"], "end": l["e"], "title": l["t"], "type": l["k"],
                                    "teachers": l["p"], "rooms": l["r"], "tags": l["g"], "details": [],
                                    "cancelled": False, "sharedWith": [gi]})
                    for v in per.values(): v.sort(key=lambda x: (x["start"], x["tags"]))
                    days.append({"date": (monday + timedelta(days=d)).isoformat(), "lessons": per})
                weeks.append({"title": f"Неделя {wk} · " + week_title([monday, monday + timedelta(days=5)]), "groups": groups, "days": days})
            if dump(ROOT / "feeds" / f"{sid}-{fac['slug']}" / f"{course}.json", {"format": "soloweek-schedule", "version": 1, "weeks": weeks}, compact=True):
                log.append(f"{sid}: {fac['name']}, {course} курс — файл обновлён")
            courses.append({"name": f"{course} курс", "id": f"{sid}-{fac['slug']}/{course}", "groups": len(gids)})
        index["faculties"].append({"id": fac_id, "slug": fac["slug"], "name": fac["name"], "courses": courses})
    old = json.loads((ROOT / "feeds" / sid / "index.json").read_text(encoding="utf-8")) if (ROOT / "feeds" / sid / "index.json").exists() else {}
    index["updatedAt"] = old.get("updatedAt") if not log and old.get("faculties") == index["faculties"] else now.strftime("%Y-%m-%dT%H:%M:%SZ")
    dump(ROOT / "feeds" / sid / "index.json", index)
    return log


# ───────────────────────── PDF-сетка на семестр (ФМО БГУ) ─────────────────────────

def run_week_grid(cfg: dict, offline: bool) -> list[str]:
    from adapters import pdf_week_grid as W
    from adapters.pdf_daily_grid import Vocabulary, feed_pdf
    sid = cfg["id"]
    state = ROOT / "state" / sid
    pdf_dir = state / "pdf"; pdf_dir.mkdir(parents=True, exist_ok=True)
    meta_path = state / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    log: list[str] = []
    now = datetime.now(timezone.utc)
    last = meta.get("lastRun")
    if not offline and last and (now - datetime.fromisoformat(last)).total_seconds() < cfg.get("everyHours", 6) * 3600:
        return []
    def download():
        # магистратура: файлы и названия программ берём из ссылок на странице факультета
        if cfg.get("mastersLink"):
            page = fetch(cfg["page"]).decode("utf-8", "replace")
            labels = {}
            for m in re.finditer(cfg["mastersLink"], page):
                text = re.sub(r"<[^>]+>|&nbsp;", " ", m.group("text"))
                text = re.sub(r"\s+", " ", text).strip()
                prof = re.search(r"«([^»]+)", text)                     # профилизация в «ёлочках»
                spec = re.search(r"(?:[Сс]пециальность|Speciality)\s*\"([^\"]+)\"", text)
                label = prof.group(1).strip() if prof else (spec.group(1) if spec else text)
                if text.startswith("Speciality"):
                    pr = re.search(r"Profiling:?\s*[«\"]?([^»\"]+)", text)
                    label = (pr.group(1) if pr else (spec.group(1) if spec else text))
                    label = re.sub(r"\s*,?\s*\([^()]*(?:Semester|Academic)[^()]*\)?", "", label).strip(" ,.") + " (EN)"
                labels[m.group("file")] = label[:80]
            meta["masters"] = labels
        files_all = list(cfg["files"]) + list(meta.get("masters", {}))
        # PDF лежат по постоянным адресам и перезаливаются — сверяем содержимое
        for name in files_all:
            url = cfg["base"] + (cfg["pattern"].format(name=name) if name in cfg["files"] else cfg["mastersPattern"].format(name=name))
            data = fetch(url)
            sha = hashlib.sha256(data).hexdigest()
            if meta.get("sha", {}).get(name) != sha:
                (pdf_dir / f"{name}.pdf").write_bytes(data)
                meta.setdefault("sha", {})[name] = sha
                log.append(f"{sid}: обновлён {name}")
            _time.sleep(1)
        meta["lastRun"] = now.isoformat()
        dump(meta_path, meta)
    if not offline:
        try:
            network(sid, meta_path, download)
        except Unreachable as e:
            log.append(f"⚠ {e}")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else meta
    masters = meta.get("masters", {})
    files = [pdf_dir / f"{n}.pdf" for n in cfg["files"] if (pdf_dir / f"{n}.pdf").exists()]
    mfiles = [(pdf_dir / f"{n}.pdf", label) for n, label in masters.items() if (pdf_dir / f"{n}.pdf").exists()]
    vocab = Vocabulary()
    for f in files + [m[0] for m in mfiles]: feed_pdf(str(f), vocab)
    start, end = date.fromisoformat(cfg["semester"]["start"]), date.fromisoformat(cfg["semester"]["end"])
    def d(dm):
        """(день, месяц) → дата в этом семестре; кривые («12.50» — это время) — None."""
        if not dm: return None
        y = start.year if dm[1] >= start.month else start.year + 1
        try: return date(y, dm[1], dm[0])
        except ValueError: return None
    by_course: dict[str, dict[str, dict]] = {}
    jobs = [(f, None, "") for f in files] + [(f, label, "m") for f, label in mfiles]
    for f, single, prefix in jobs:
        for c in W.parse_pdf(str(f), vocab, single=single):
            groups = by_course.setdefault(f"{prefix}{c.number}", {})
            for g in c.groups:
                groups.setdefault(g, {"spec": c.specialty, "lessons": []})
            for l in c.lessons:
                groups.setdefault(l.group, {"spec": c.specialty, "lessons": []})["lessons"].append(l)
    out = ROOT / "feeds" / sid
    courses = []
    for course, groups in sorted(by_course.items(), key=lambda kv: (kv[0].startswith("m"), kv[0])):
        names = sorted(groups, key=lambda g: (int(re.search(r"\d+", g).group(0)) if re.match(r"Группа", g) else 999, g))
        glist = [{"index": i, "name": g, "code": "", "specialty": groups[g]["spec"], "profiles": []} for i, g in enumerate(names)]
        weeks = []
        monday = start - timedelta(days=start.weekday())
        while monday <= end:
            days = []
            for wd in range(6):
                day = monday + timedelta(days=wd)
                per: dict[str, list] = {}
                if start <= day <= end:
                    for gi, g in enumerate(names):
                        for l in groups[g]["lessons"]:
                            if l.weekday != wd: continue
                            only = {x for x in map(d, l.only or []) if x}
                            if l.only is not None and only and day not in only: continue
                            if l.only is None and d(l.since) and day < d(l.since): continue
                            if d(l.until) and day > d(l.until): continue
                            if day in {d(x) for x in l.skip}: continue
                            per.setdefault(str(gi), []).append({
                                "start": l.start.strftime("%H:%M"), "end": l.end.strftime("%H:%M"), "title": l.title,
                                "type": "OTHER", "teachers": l.teachers, "rooms": l.rooms, "tags": l.tags,
                                "details": [l.note] if l.note else [], "cancelled": False, "sharedWith": [gi]})
                    for v in per.values(): v.sort(key=lambda x: (x["start"], x["tags"]))
                days.append({"date": day.isoformat(), "lessons": per})
            weeks.append({"title": week_title([monday, monday + timedelta(days=5)]), "groups": glist, "days": days})
            monday += timedelta(weeks=1)
        if dump(out / f"{course}.json", {"format": "soloweek-schedule", "version": 1, "weeks": weeks}, compact=True):
            log.append(f"{sid}: {course} курс — файл обновлён")
        title = f"Магистратура · {course[1:]} курс" if course.startswith("m") else f"{course} курс"
        courses.append({"name": title, "id": f"{sid}/{course}", "groups": len(names)})
    old = json.loads((out / "index.json").read_text(encoding="utf-8")) if (out / "index.json").exists() else {}
    index = {"format": "soloweek-feed", "version": 1, "id": sid, "courses": courses}
    index["updatedAt"] = old.get("updatedAt") if not log and old.get("courses") == courses else now.strftime("%Y-%m-%dT%H:%M:%SZ")
    dump(out / "index.json", index)
    return log


ADAPTERS = {"pdf_daily_grid": run_pdf_daily, "bseu_api": run_bseu, "pdf_week_grid": run_week_grid}


def main():
    offline = "--offline" in sys.argv
    only = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), None)
    skip = {x for a in sys.argv if a.startswith("--except=") for x in a.split("=", 1)[1].split(",")}
    failed = False
    for path in sorted((ROOT / "sources").glob("*.json")):
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if (only and cfg["id"] != only) or cfg["id"] in skip:
            continue
        try:
            for line in ADAPTERS[cfg.get("adapter", "pdf_daily_grid")](cfg, offline):
                print(line)
        except Exception as e:
            failed = True
            print(f"✖ {cfg.get('id')}: {e}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
