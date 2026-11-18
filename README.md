# SoloWay — данные расписаний

Конвертер, который превращает расписания учебных заведений в формат приложения [SoloWay](https://github.com/soloveiby/SoloWay-releases).
Работает на GitHub Actions каждые 30 минут — своего сервера нет.

```
сайт заведения (PDF, таблица…) ──▶ pipeline/run.py ──▶ feeds/<id>/<курс>.json ──▶ приложение
```

- `sources/<id>.json` — откуда брать расписание и каким адаптером разбирать
- `pipeline/adapters/` — адаптеры под форматы (сейчас: PDF-таблица на день)
- `pipeline/tests/` — проверка разбора на сохранённых файлах
- `feeds/<id>/index.json` — курсы, группы, дни, время обновления
- `feeds/<id>/<курс>.json` — расписание курса (формат `soloweek-schedule`)
- `state/<id>/` — скачанные файлы за последние 3 недели и их контрольные суммы

Подключено: Минский радиотехнический колледж (`mrk`).

Локально: `pip install -r pipeline/requirements.txt && python pipeline/run.py`
