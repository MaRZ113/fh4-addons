# Аудит v1.4.5 и план v1.4.6 — Suzuki SX4 Sportback (CarID 12000)

Дата: 2026-11-22. Входные данные: `Expanded-Garage-v1.4.5-Native-Wheel-State-Physics-Fix.zip`,
`.scratch/expanded-garage-v1.4.5/verified-release/suzuki/gamedb.sqlite`, установленная игра
`game/ForzaHorizon4`, FM4 `Media/db/gamedb.slt`, скриншоты владельца (шоурум, вкладка Manufacturer Colors).

Статус по отчёту владельца: покупка проходит, машина видна в шоуруме, в катсцене покупки
(камера «садится» в машину) — фриз.

## 1. Найденные дефекты (по убыванию влияния на стабильность)

### D1. NULL в полях, которые у нативных машин не бывают NULL (критично)
Компилятор (`fh4_db_compiler.insert`) оставляет любой неотображённый столбец пустым и только
пишет его в `unresolved`. В v1.4.5 уже доказано: NULL в `List_AeroPhysics` читается игрой как
мусор (FLT_MAX), что дало NaN в физике. Те же NULL остались в других таблицах Suzuki:

| Таблица | Столбцы с NULL | Нативное значение (stock) |
| --- | --- | --- |
| `CameraOverrides` | `CamFollowLowMaxHeightOffset`, `CamFollowHighMaxHeightOffset` | 0.0 у 758 из 761 |
| `CameraOverrides` | `CamNoWheelCockpitOffsetX/Y/Z` | у каждой машины свои; ≈(−0.01…0.09, 0.03…0.08, 0.15…0.21) |
| `Data_Engine` | `FuelBurnPerHorsepowerHour` | 0.57 у 753 из 783 |
| `List_UpgradeBrakes` | `Front/RearBrakeDrumDepthMM` | 35 / 50 / 65 |
| `List_UpgradeDrivetrainTransmission` | `GearRatio9`, `GearRatio10` | 0.0 |
| `List_TorqueCurve` | `v186…v245` | 0.0 |

(во флотской сборке ещё `List_AntiSwayPhysics.BeamStiffness`, `List_SpringDamperPhysics.BumpStopHeight/UseProgressiveSpring`,
`List_UpgradeDrivetrainDriveline.Flex*`).

**Связь с фризом:** «камера садится в машину» = кокпитная камера → `CamNoWheelCockpitOffset*`.
В `POST_PURCHASE_STALL.md` уже зафиксирован NaN в `CPresentationCamera` (публикация 644)
→ мусорная терраин-сетка → вылет в My Cars; в зависшем цикле ветра — float-мусор `2.33e33`.
NULL в камере — наиболее вероятный общий источник обоих сбоев. Проверяется одной пересборкой.

Попутно: `CamHoodHeightOffset=0.61`, `CamHoodZOffset=+0.31` у Suzuki против ≈0.1…0.16 и
−0.6…−0.9 у нативных — похоже, FM4-значения перенесены без перевода системы координат (ось Z
в сборке отражается).

### D2. Нет нативных сценических ресурсов, нужных катсценам и камерам
В архиве `EG_SUZ_SX4_11.zip` нет файлов, которые есть у **всех** нативных (не-traffic) машин:
`IKAnchorBones.xml` (руки/ноги/посадка водителя), `Lights.bin`, `LightPresets.bin`,
`ManufacturerColors.bin`, `LiveryMasks/*`, `aovolumetexture.bin`, `Textures/AO/*`,
`Textures/Damage/*`, `.avpins`, `ProxyLOD.modelbin`, `Cinematics/*`.
В `Locators.xml` всего 9 локаторов; у нативных (Fiesta ST, 695 Abarth, Civic RS) общий набор
включает `carLocator_entryDoorLF`, `doorHandleLF/RF`, `doorLF/RF`, `seatCamRF`, `lookCloseLeft`,
`dashboard`, `gauges`, `hood`, `Engine`, `Exhaust_*`, `drivetrain`, `engineSmoke`, `Audio_*`.
Катсцена посадки водителя почти наверняка опирается на `entryDoor*`/`doorHandle*`/`seatCam*`
и IK-якоря — второй кандидат на фриз.

### D3. Пустые Manufacturer Colors
- В GameDB для 12000 нет строк `Combo_Colors`, хотя FM4 содержит 6 заводских цветов SX4
  (`Ordinal=1374`, ID 1374001…1374006, схема таблиц идентична) — компилятор их не переносит.
- В архиве нет `ManufacturerColors.bin`. Формат (Grub 1.1, блок `MNCL` v1.1): `u8 count`,
  затем на каждый цвет `u8 1, u32 0x1D, float R, float G, float B (= sRGB/255), u8 len, путь
  к .materialbin` (у Fiesta 7 записей, `carcolors_pbc/ford/*.materialbin`).

### D4. Машина не перекрашивается, стёкла и оптика «пустые»
`fh4_model.material()` пишет каждому материалу пустой `MTPR` (13 нулевых байт), а
`fh4_material_contract.surface_binding` сводит почти всё стекло к `exterior_glass/gls_clear`.
Нативные модели:
- лобовое — `exterior_glass/gls_windsheild`, тёмная кайма — `specificuse/blackglass`;
- рассеиватели фар/фонарей — `gls_ml_ch1_*`, `gls_colored` (красный), `radiosity/rad_smoothglass`
  **с непустыми MTPR (52–933 байт: цвет, тонировка, маски)**;
- корпуса фар — `exterior_lights/lights_ch1bump_ch1lmask` / `lbk_ch1_custom` с MTPR;
- кузов — `exterior_misc/carpaint` с пустым MTPR, но MatI называется `carpaint`
  (у нас — именем из FM4: `body`, `body_2`, `matte_colors`, `misc`).
Покраска: нет `LiveryMasks`, `ManufacturerColors.bin`, `Combo_Colors`; имя материала кузова
другое. Какое из этого обязательно для перекраски — проверить A/B-тестом (план, фаза 2).

### D5. Колёса огромные и стоят неверно (подтверждено измерением)
Все нативные модели колёс (Fiesta 17", Abarth 18", Civic 13", Chiron 22") **нормализованы
одинаково**: только диск (без шины), радиус 0.14, ширина по X от 0 (лицевая сторона) до 1
(внутренний край обода); игра сама масштабирует их по размеру диска/ширине и строит шину.
Наш `scene/wheel/wheel*.modelbin` — в метрах, **вместе с покрышкой**: радиус 0.317, X от −0.104
до +0.103, весь меш с материалом диска. Итог: диск растягивается до размера колеса и больше,
хром заполняет арку, вылет неверный. Кроме того, нативные колёса лежат в
`scene/_library/Scene/Wheels/<media>_WheelLF.modelbin` (одна модель + зеркалирование),
blur-меши названы `blurLip_*`/`blurRim_*`, LOD — `wheel_LODS0…LOD5`.

### D6. Процесс и инфраструктура
- Нет git: история — это архивы по 75–136 МБ в корне (≈1 ГБ) и копии `Suzuki_V1`,
  `Expanded-Garage-v1.4.1-…`, `.scratch/expanded-garage-v1.4.x`. Исследовательские скрипты
  (`replay-*.py`, `stall-native.py`) живут только в `.scratch`.
- README устарел (говорит о 1.3.0a1); команда тестов из README падает с ImportError.
  Рабочая: `PYTHONPATH="$PWD;$PWD/tools" python -m unittest discover -t . -s expanded_garage -p 'test*.py'`
  (33 теста, OK).
- `build.json` у Suzuki: `installable: false`, а `deployment.json`: `installable: true` —
  два источника истины.
- Валидатор проверяет структуру, но не проверяет «нативную полноту» (NULL, обязательные файлы,
  нормализацию колёс, набор локаторов).

## 2. Рефакторинг

1. **Политика заполнения столбцов вместо молчаливого NULL.** Таблица `native_defaults`:
   для каждого (таблица, столбец) — стратегия: `mode` нативных значений, `donor` (ближайшая
   нативная машина того же класса/кузова), `derived` (формула) или `zero`. `insert()` падает,
   если после всех стратегий остался NULL там, где у нативных его не бывает.
2. **Гейт «нативная полнота» в `verify`:** NULL-аудит против stock-базы (скрипт уже есть:
   `audit2.py`-подход), обязательный набор файлов архива, обязательные локаторы,
   bbox колёс `[0..1]×[−0.14..0.14]²`, непустой `ManufacturerColors.bin`.
3. **Материалы — данными, а не if-цепочкой.** Сгенерировать из нативных моделей таблицу
   «роль поверхности → materialbin + MTPR-блоб» (донор — нативный хэтчбек), хранить в JSON,
   `surface_binding` превратить в поиск по таблице. MTPR копировать байт-в-байт из донора.
4. **Донорская машина как источник «служебных» ресурсов.** Для `IKAnchorBones.xml`,
   `Lights.bin`, `LightPresets.bin`, AO/Damage-текстур, `Cinematics`, недостающих локаторов —
   брать нативный донор близкого размера (Fiesta ST 14 / Civic) и пересчитывать позиции по
   геометрии SX4.
5. **Отдельный писатель колёс** (`fh4_wheel_model.py`): отделить шину, нормализовать диск,
   писать одну LF-модель в нативный путь, blur-меши с нативными именами.
6. **Репозиторий:** `git init`, артефакты в `dist/` под `.gitignore`, `.scratch`-скрипты
   исследований перенести в `expanded_garage/research`, обновить README и команду тестов.

## 3. План работ

**Фаза 0 — гигиена (0.5 дня).** git init, обновить README/команду тестов, единый статус
installable, перенести replay/stall-скрипты в `research/`.

**Фаза 1 — стабильность покупки и выезда (главная цель).**
1. `native_defaults` + NULL-гейт; заполнить `CameraOverrides` (MaxHeight=0, NoWheelCockpit =
   значения донора или CamCockpit-смещения), `FuelBurn=0.57`, `DrumDepth` по размеру тормоза,
   `GearRatio9/10=0`, `v186+=0`.
2. Перепроверить систему координат `CameraOverrides` (Hood/Bumper/Cockpit) против нативных
   диапазонов; при выходе за диапазон — значения донора.
3. Добавить `IKAnchorBones.xml` и недостающие локаторы (`entryDoorLF`, `doorHandleLF/RF`,
   `doorLF/RF`, `seatCamRF`, `lookCloseLeft`, `dashboard`, `gauges`, `hood`, `Engine`,
   `Exhaust`, `Audio_*`), позиции — из геометрии/CarPartPositions SX4.
4. Пересборка → preflight → install на чистом профиле → тест: шоурум → покупка (катсцена до
   конца) → My Cars → выбор → выезд → газ/руль/тормоз → перезапуск → загрузка из гаража.
   Если фриз останется — пассивный наблюдатель на `CPresentationCamera+0x20..0x5F` в момент
   катсцены (инструментарий уже есть в `research/`).

**Фаза 2 — внешний вид.**
1. `Combo_Colors` из FM4 (6 цветов, ID 12000001…) + генератор `ManufacturerColors.bin`
   (материал — ближайший `carcolors/*` по цвету и metallic).
2. A/B на покраску: (a) MatI кузова → `carpaint`; (b) + `LiveryMasks` от донора; проверка
   Normal/Special colors.
3. Стёкла/оптика: таблица материалов с MTPR из донора (лобовое `gls_windsheild`, боковые
   `gls_window`, заднее `gls_rearglass`, кайма `blackglass`, рассеиватели `gls_ml_ch1_*`/
   `gls_colored` red, отражатели `lights_ch1bump_ch1lmask`).
4. Колёса: отделить шину, нормализовать диск (радиус 0.14, X 0→1), одна модель
   `scene/_library/Scene/Wheels/EG_SUZ_SX4_11_WheelLF.modelbin`, проверить ряд `List_Wheels` и
   ссылки в carbin.
5. Сверка скриншотами: шоурум, покраска, ночью фары, колёса в арках.

**Фаза 3 — после стабильного выезда.** AO/Damage-текстуры, Lights.bin/пресеты света, звук,
эмблемы/текстуры приборов, затем Honda/RUF на той же схеме.
