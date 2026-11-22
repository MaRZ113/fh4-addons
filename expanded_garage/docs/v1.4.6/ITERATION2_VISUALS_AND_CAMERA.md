# v1.4.6, итерация 2 — внешний вид и захват NaN камеры

## Результат теста первой сборки (2026-11-22)

- Выезд и управление — нормально; водитель сидит в машине.
- Переключение камеры из салона — вылет `0xC0000005` по RVA `0x467A51F` (тот же адрес, что
  и в v1.4.5: воркер подгрузки террейна получает сетку из NaN-позиции камеры).
- В гараже при долгих пролётах камеры — зависание (AppHang), без вылета.
- Визуально: огромные хромированные колёса, прозрачные стёкла и фонари, пустые заводские
  цвета, машина не перекрашивается.

В строках GameDB Suzuki больше нет значений вне нативных диапазонов, которые относились бы
к камере. Значит, NaN камеры берётся из сцены машины. Кто записывает NaN, пока не доказано.

## Изменения

1. **Колесо по нативному соглашению** (`fh4_wheel_model.py`). Нативный Carbin не ссылается на
   модель колеса: игра грузит `scene/_library/scene/wheels/<media>_wheellf.modelbin` по строке
   `List_Wheels`, затем зеркалит её и масштабирует под размер диска. Модель нормализована:
   только диск, радиус обода 0.14, лицевая сторона X=0, внутренний край X=1, кости
   `<root>`/`spindle`/`root`. У Suzuki такого файла не было, а в Carbin стояли четыре колеса
   в метрах вместе с покрышкой. Теперь:
   - покрышка отрезается по радиусу обода (0.236 м, измерен по LOD1+), удалено 4199 треугольников;
   - диск нормализуется, `spindle` ставится на исходный центр FM4 (X=0.503);
   - колёс в Carbin больше нет;
   - гейт пробной установки требует именно эту модель.
2. **Стёкла и рассеиватели** (`fh4_material_contract.py`, `fh4-material-parameters-v1.json`).
   - лобовое — `gls_windsheild`, заднее — `gls_rearglass`, боковые — `gls_window`;
   - со стороны салона — `interior_glass/*`;
   - кайма — `blackglass`, обогрев — `defrostlines`;
   - красные рассеиватели фонарей — `gls_colored` с красным MTPR донора.

   MTPR донора (FOR_FiestaST_14) извлекается скриптом `research/extract_material_parameters.py`
   и ссылается только на общую библиотеку.
3. **Заводские цвета** (`fh4_paint.py`, `fh4-paint-palette-v1.json`). FM4 хранит 6 цветов SX4 в
   `Combo_Colors`. Теперь они попадают в IR (`fm4.py`), в GameDB (ID 12000001…) и в
   `ManufacturerColors.bin`. Материал краски для каждого цвета — ближайший по RGB нативный с
   тем же типом (металлик/солид) из палитры 2053 материалов 784 машин
   (`research/extract_paint_palette.py`). Контейнер воспроизводит нативный файл Fiesta байт-в-байт.
4. **Покраска.** Все поверхности кузова под `carpaint` теперь — один MatI с именем `carpaint`,
   как в нативных моделях. Вернёт ли это перекраску, покажет тест.
5. Скелет Modelbin получает нативный «пустой» бокс (min=+FLT_MAX, max=−FLT_MAX) вместо
   нулевого бокса в начале координат.
6. IR Suzuki извлечён заново (`.scratch/expanded-garage-v1.4.6/ir-suzuki`). Он отличается
   от прежнего только добавленной таблицей `Combo_Colors`.

## Сборка и установка

Входы: `.scratch/expanded-garage-v1.4.6/v1` — IR Honda и RUF прежние, Suzuki новый.
Сборка: `.scratch/expanded-garage-v1.4.6/rebuild2`, Suzuki `offline-trial-eligible`.
Установлено как транзакция **`cb027ce48ce84dbe924d89802abb1504`** (предыдущая `c64d51bf…` откачена).

## Захват NaN камеры

`research/watch_camera_nan.py` читает процесс только через ReadProcessMemory. Наблюдатель
держит кольцо из 240 снимков полного `CPresentationCamera` (0x480 байт) и объектов, на которые
он ссылается. Когда матрица камеры становится NaN, кольцо сбрасывается на диск со списком
смещений, ставших NaN на этом шаге. Порядок:

1. Запустить FH4 и дойти до свободной езды или до гаража.
2. Запустить наблюдатель (PID и базу образа подставляет агент).
3. Воспроизвести: камера салона → следующая камера, или подождать пролётов в гараже.

## Вылет при загрузке на сборке итерации 2

Сборка `cb027ce4` падает при обычной загрузке игры по RVA `0x3534115`
(2 из 2 запусков без инструментов; события Windows 11:11:04 и 11:17:45).
Код этой функции есть только в расшифрованном виде в памяти. Его пассивно сняли
(`research/dump_runtime_code.py`, `.scratch/.../capture/code`). Функция обнуляет
вложенную таблицу 6 × 11 × 3 ресурсных слотов-указателей объекта (`rcx+0xB340`/`+0x1B710`).
Запись падает, потому что сам объект невалиден. Камера за 1687 снимков перед вылетом
оставалась конечной, так что этот вылет не связан с NaN камеры.

Подключение отладчика (`research/capture_any_fault.py`) сразу после старта игры роняет её
защитой от отладки: RVA `0xE09614`, регистры обнулены, RSP нечётный. Отладчик к FH4 не
подключать.

### Бисекция

Переключатель `EXPANDED_GARAGE_WHEELS=carbin` собирает прежние четыре колеса в Carbin вместо
колеса по соглашению. Остальные правки итерации 2 остаются: стёкла, цвета, `carpaint`, бокс
скелета. Этот вариант установлен как транзакция `d0ff43de6fbe4f908b06450b96d5e51d`.

- Загрузка проходит → вылет вызывает колесо по соглашению.
- Вылет остаётся → виновник среди стёкол/MTPR, `carpaint`, бокса скелета и цветов;
  следующий вариант делит этот набор пополам.

### Результат шага 1 и шаг 2

Вариант с колёсами в Carbin загрузился. **Вылет при загрузке вызывает колесо по соглашению.**
Через пару секунд в гараже — новый вылет по RVA `0x333A9AC` (11:21:45).

Шаг 2 делит оставшиеся правки на два набора:
- **A** — «пустой» бокс скелета и объединённый MatI `carpaint`;
- **B** — стёкла с MTPR и заводские цвета.

Новые переключатели `EXPANDED_GARAGE_SKELETON_BOX=zero` и `EXPANDED_GARAGE_PAINT_MATI=source`
откатывают набор A. Сейчас установлен вариант «колёса в Carbin + только B», транзакция
`5c44181641074690b0240d43a9ebd425`.

### Природа вылета 0x333A9AC

Расшифрованный код (`capture/code2`) показывает, что `0x333A9AC` — штатная аварийная
остановка игры: `lea rcx, "Fatal Error"` → `call 0x3396E90` → `mov dword ptr [0], 0`.
Соседние строки в `.rdata`: «Out of Memory», «Video card», «Lifetime expired».
Игра сама обнаружила недопустимые данные и остановилась с числовым кодом ошибки.
Это не порча памяти. Код ошибки без регистров в момент вылета не виден.

### Захват NaN камеры на пролёте в гараже (вариант «колёса в Carbin + только B»)

Вылет — снова `0x467A51F`: в момент смены кадра пролёта NaN камеры доходит до
подгрузки террейна. Группа B (стёкла, цвета) на это не влияет. Наблюдатель записал переход
(`capture/cam-flyby.jsonl`, шаг 1209):

- шаг 1208 — начало нового кадра. Четыре опорные точки цели `+0x230…+0x260` построены вокруг
  позиции машины (−2749, 389, −4348), но их разброс всего ~0.2 м, а не размер кузова.
  Далее идут точки дальней плоскости в десятках километров;
- шаг 1209 — `+0x160` = 999.0 (та же заглушка, что в захвате v1.4.5), `+0x15C` = 100.0.
  Матрица `+0x20…+0x5F` и блок `+0x120…+0x14C` становятся NaN одновременно;
  дочерние объекты остаются конечными.

Вывод: кадр пролёта кадрирует машину по вырожденному объёму. В этом варианте бокс скелета
прежний (нулевой), так что дело не в нём.

### ProxyLOD

`scene/ProxyLOD.modelbin` есть в 399 из 400 нативных архивов. Carbin на него не ссылается,
игра грузит его по соглашению: упрощённый силуэт всей машины с боксом кузова. У Suzuki его
не было. `fh4_model.proxy_model` повторяет нативную структуру Fiesta:
- скелет из одной кости `<root>`;
- пустой MatI `shader`;
- одна сетка плюс тень с LOD-маской 0xFFFF и минимальным LOD 30;
- Modl с нулевыми LOD-флагами.

Сетка собирается из самых грубых LOD штатных внешних деталей. Валидатор проверяет структуру
и невырожденный бокс.

Установлено как транзакция `92d95cd6ba0f44e89bb7af5f6cc38d81`:
колёса в Carbin, группа A откачена, группа B и ProxyLOD включены.

### Результат ProxyLOD и отсутствующие строки GameDB

Сборка с ProxyLOD (`92d95cd6`) снова упала на `0x467A51F` (11:35:22). На этот раз матрица
камеры презентации не стала NaN: позиция застыла конечной, но на 2.4 км под землёй и в 3.3 км
от машины. В двух захватах эта камера держится в 4.7 км от машины на (1918, 144, −4787),
как и в v1.4.5. При смене кадра у неё FOV 32.3 → 37.3 и `+0x15C/+0x160` = 100 / 999, после
чего она уходит в NaN или под землю. Опорные точки цели с ProxyLOD не изменились (~0.2 м),
так что ProxyLOD их не задаёт. Он остаётся в сборке как часть нативной полноты.

Новый аудит (`scratchpad/missing.py`) ищет таблицы, где строки есть почти у всех нативных
машин, а у Suzuki их нет:

| Таблица | Нативное покрытие | Исправление |
| --- | --- | --- |
| `List_UpgradeCarBodyTrackSpacingFront` / `Rear` | 100% кузовов | штатная строка Spacing 0, Level 0 |
| `CarExceptions` | 89% машин | самый частый нативный шаблон (все флаги 0) |

`fh4_db_compiler.companion_rows` вставляет самый частый нативный набор строк для каждой такой
таблицы, если FM4 не даёт своих. После этого аудит пробелов чистый.
Установлено как транзакция `b5b63034910046c18b9c2abe30162ce2`
(колёса в Carbin, без группы A, группа B + ProxyLOD + сопутствующие строки).

### Эталон с нативной машиной и Autovista

Захват с Acura RSX (CarID 422, `cam-acura.jsonl`, 48 с пролётов в гараже): кадры с
дистанциями `+0x15C/+0x160` = 388.85 / 0.29…1.21, FOV 37–38. Камера стоит за километры от
машины, значит, это нормально. **Кадра 100/999 у Acura не было ни разу.** У Suzuki он появлялся
в каждом из трёх захватов, и после него камера уходила в NaN. Вывод: 100/999 — запасной кадр,
который игра выбирает, когда у машины нет данных для кадров презентации.

Такие данные хранит `<media>.avpins` — XML Autovista Points of Interest. В нём шаблон кузова,
виды из салона, точки интереса с радиусами дистанции камеры и углами. Он есть в 299 из 300
нативных архивов, у Suzuki его не было.

- Донор — `FOR_FocusRS_17`: пятидверный хэтчбек, шаблон `4Door_Sedan_root`, левый руль
  (`research/extract_autovista.py` → `fh4-autovista-v1.json`).
- `fh4_driver_rig.autovista` переносит 94 позиции в системе `root` подгонкой по фарам,
  фонарям и рулю (невязка 7 см). Позиции относительно других локаторов не меняются.
  Все упомянутые локаторы есть в `Locators.xml` Suzuki.
- Для машин с противоположным рулём файл пока не создаётся (Honda), это записано в отчёте сборки.

Установлено как транзакция `39a356e48a1f40d0a64bd71160db5bd7`
(колёса в Carbin, без группы A, группа B + ProxyLOD + сопутствующие строки + Autovista).

### Autovista не помог: кадр 100/999 бывает и у нативных машин

С Autovista (`39a356e4`) кадр 100/999 появился и у Acura (`cam-autovista.jsonl`, шаги
269…313). У Acura камера при нём осталась конечной (FOV 50, позиция (3810, −646, −3423)).
У Suzuki на шаге 4369 этот же кадр дал NaN, после чего игра упала в `VCRUNTIME140.dll+0x1E4FD`.
Это другое следствие того же испорченного состояния. Гипотеза про запасной кадр не
подтвердилась, но Autovista остаётся в сборке как часть нативной полноты.

Сравнение полей одного и того же кадра у двух машин:
- у Suzuki **цель `+0x110` — поза самой машины — становится NaN**;
- `+0x19C` = −6.05e35. Такой масштаб даёт мусор из FLT_MAX, как в аэродинамике v1.4.5.
  В первом захвате итерации 2 там тоже был мусор (−1.43e7);
- у Acura в этом кадре всё конечно.

Вывод: на смене кадра машину в гараже переставляют, и её физическое состояние взрывается.
Источник снова в данных физики машины.

### Правило нативного конверта

`NativeDefaults.complete` теперь, кроме заполнения NULL:
- ставит константу, если столбец одинаков у всех нативных строк (не меньше 20 строк);
- прижимает числовые значения к нативному [min, max].

Ключи, ID, `Ordinal`, `Level`, `IsStock`, цены и точки кривой момента не трогаются.
Все правки пишутся в `native_defaults` строки. Для Suzuki правило изменило ровно то, что
раньше показал аудит диапазонов:

| Таблица | Поле | Было | Стало |
| --- | --- | --- | --- |
| `List_SpringDamperPhysics` (обе оси) | `CamberChangeRatioCompression/Expansion` | 0.25 | 0 (у всех нативных 0) |
| `List_SpringDamperPhysics` (обе оси) | `BumpstopStiffness` | 200 | 180 (нативный максимум) |
| `List_UpgradeCarBody{Hood,RearBumper,SideSkirt}` | `Sequence` | 0 | 1 (нативный минимум) |

Наблюдатель теперь пишет позу и скорость машины в фокусе (`CarPresentation+0xA0`, `+0x280`).
Установлено как транзакция `4aa4d5f996c943ad9d51f430f495ebf3`.

## Locator identity (tx 3c7e5ff99ee84f2d8e968172ddcc1281)

Envelope build (tx 4aa4d5f9): showroom flyby still crashes at 0x467A51F. The owner sees the camera leave the
showroom (grey frame) for a moment at a cut, return, and the game crashes on a following cut. The capture shows
the cut to the 100/999 shot turning the camera matrix NaN in one step, with the focused car pose finite.

`Camera.zip` (decrypted with ForzaCryptoTool, method 22) holds the shot lists. `HomeSpaceCams.xml` and
`ColorSelectCams.xml` aim three shots at car parts (`RotAnimType="TargetPart"`, `RotAnimPartToTarget="headlightL"`,
`"taillightR"`); `CarRelativeCams.xml` and `carrevealcams.xml` target wheels. A part the camera cannot resolve gives
a zero aim vector, which normalises to NaN.

Native Locators.xml files use one GUID per locator name across the roster (785 cars: `carLocator_headlightL` is
`533329f7-...` on 398 of 400 sampled, `root` `55f9a81d-...`). The builder generated a GUID per car for every locator,
so no Suzuki locator had its native identity. `research/extract_locator_guids.py` writes
`fh4-locator-guids-v1.json` (57 names where one GUID covers at least 90% of carrying cars, 20 cars minimum);
`locator_xml` now uses it and keeps the generated GUID for other names.

Result: the GUID change alone did not fix it. Crash 0x467A51F at 14:35:24, about 15 s into the showroom; the
capture shows the same one-step NaN eye position at the first cut to the 100/999 shot (step 797), then a finite
shot, then the crash. The watcher now also flags a NaN eye (+0x110) and dumps the focused CarPresentation
(0x1000 bytes) and its scene (0x400 bytes), flushing when either gains a non-finite field. Working hypothesis:
the shots pose the car (`CarPositioning CarSteerAngle`) and frame it in `BoundingBoxSpace`, so a car-side value that
turns non-finite under steering or posing would reach the camera; the chase cameras after the cockpit view are
mostly `BoundingBoxSpace` too.

Run 14:38 (tx 3c7e5ff9): crash at a new offset, 0x46827F4, about 10 s into the showroom. No camera NaN was
sampled; the focused CarPresentation (4 KiB) and its scene carry no new non-finite field (the w lanes at +0x3EC and
+0x40C hold NaN on every sample, so they are a sentinel). The car-side NaN source is not in those objects.

Ruled out by comparison with ACU_RSXTypeS_02: CarPartPositions (ten rows, finite, same layout) and CameraOverrides.
The exe's camera part names (`headlightL ... wheelRM, driverHeadPos`) sit next to light-glass model names; native
archives carry `Lights.bin` and `LightPresets.bin` (784 of 784), which the Suzuki archive lacks.
Format of `Lights.bin`: u32 0xDEADBEEF, u32 version 1, u32 model count, per model u32 length + virtual path + u16 1,
then u32 light count and 76-byte records (u32 function, u16 group, u16 sub-index, u16 model index, u16 0,
two position+quaternion transforms). Function codes are not decoded yet.

Next: `research/dump_runtime_text.py` saves both decrypted executable sections from a running game for offline
disassembly of the presentation camera update.

Run 14:43 (tx 3c7e5ff9): crash 0x467A51F; camera NaN sampled at the third cut. The decrypted first `.text`
(79,393,280 bytes, `capture/text/text-00001000.bin`) was saved during this run.

Static reading of the dump (image base 0x140000000):
- CPresentationCamera vtable 0x5B72CE8 has 71 slots. Slot 50 (0x39070E0) copies a 4x4 matrix to +0xE0..+0x11F
  (+0x110 is the eye position row). Slot 37 (0x3907D30) writes a vec4 to +0x150 and two floats to +0x160/+0x164;
  the 100/999 pair lives there (Acura also shows +0x15C = 100 in some shots, so the pair is a shot state, not a fault).
- `RotAnimPartToTarget` is an enum of 12 parts (0x55A47A0: headlightL 0 ... driverHeadPos 11), parsed at 0x3286A57.
  A second table at 0x55A4860 maps model names to those parts: headlightL/glassLHL/lexanLHL -> 0,
  headlightR/glassRHL/lexanRHL -> 1, taillightL/glassLTL/lexanLTL -> 2, taillightR/glassRTL/lexanRTL -> 3,
  steering -> 4, wheelLF..wheelRM -> 5..10. No RIP-relative reference to it was found yet.
- Native carbins carry per-model AO swatch paths and material names (`Steering_plastic_2|steeringwheel_a`);
  the generated carbin leaves those fields empty.

Shot data layout from the parser (0x32869C0, called from the Cam reader at 0x28A181D): a Cam holds `Type` at
+0x24 and the Anim block at +0x80 (PosAnimType +0x80: WorldSpace 0, CarSpace 1, WorldOffsetCar 2, PartSpace 3,
BoundingBoxSpace 4; RotAnimType +0x84: TargetCar 0, TargetPart 1, WorldSpace 2, CarSpace 3; part index +0x88;
keys at +0xA0, 64 bytes each).

The skeleton Modelbin BBox is the empty sentinel (FLT_MAX, -FLT_MAX) in 60 of 60 sampled native cars; the bisection
builds since d0ff43de used a zero box (`EXPANDED_GARAGE_SKELETON_BOX=zero`). A zero box is a valid box of size zero
at the origin, which a box-normalised camera frame would divide by. The Fatal Error 0x333A9AC of d0ff43de came
with two changes at once (sentinel box and merged carpaint MatI). Variant tx f368e7cc: native sentinel box,
source paint MatI, carbin wheels, everything else as 3c7e5ff9.
