# fh4-addons

Конвейер переноса машин из Forza Motorsport 4 в Forza Horizon 4 (Steam 1.478.564.0).
Первая цель — Suzuki SX4 Sportback 2011 (`SUZ_SX4_11`, CarID 12000): стабильная покупка,
выбор в гараже и выезд. Контрольные машины: Honda Integra Type R (12002) и RUF CTR2 (12001).

## Состав репозитория

| Путь | Что там |
| --- | --- |
| `expanded_garage/` | Пакет-компилятор: FM4 → Vehicle IR → архив FH4, GameDB, установщик, валидатор, тесты |
| `expanded_garage/docs/<версия>/` | Отчёты по версиям; актуальный план — [v1.4.6/AUDIT_AND_PLAN.md](expanded_garage/docs/v1.4.6/AUDIT_AND_PLAN.md) |
| `expanded_garage/research/` | Исследовательские скрипты (наблюдатели, реплеи физики, разбор дампов) |
| `tools/*.py`, `tools/*.ps1` | Ранние пробники форматов (carbin, modelbin, gamedb) — нужны пакету как модули |
| `docs/research/` | Отчёты ранних фаз исследования (форматы, загрузка, PoC) |

В репозиторий **не попадают** игровые файлы и всё, что из них получено: установка игры
(`game/`), распакованная FM4, сборки и handoff-архивы (`*.zip`), GameDB (`*.slt`, `*.sqlite`),
дампы и профили (`.scratch/`, `private-*`). Сторонние инструменты (`tools/*/`) тоже
не хранятся — их нужно положить рядом локально (см. `docs/research/DEPENDENCIES.md`).

## Требования

Python 3.12+, NumPy, Pillow, локальный checkout `tools/driversf-forza-master`,
`ForzaCryptoTool.exe` для упаковки GameDB, распакованный диск FM4 и копия FH4 для тестов.

## Запуск

```powershell
.\expanded-garage.cmd --help
```

Тесты (запускать из корня репозитория):

```powershell
$env:PYTHONPATH = "$PWD;$PWD\tools"
python -m unittest discover -t . -s expanded_garage -p 'test*.py'
```

Сборка, установка и откат описаны в [expanded_garage/README.md](expanded_garage/README.md)
и в документах версии. `build.json` хранит статус до валидации (`installable: false` всегда);
решение об установке принимает валидатор и записывает его в `deployment.json` пакета.

## Лицензия

MIT, см. [LICENSE](LICENSE). Код ForzaTech Studio, на котором основаны части разбора
форматов, — см. `expanded_garage/LICENSE-FORZATECH-STUDIO.txt`.
