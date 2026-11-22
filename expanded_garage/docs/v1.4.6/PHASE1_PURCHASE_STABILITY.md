# v1.4.6 фаза 1 — стабильность покупки и посадки водителя

## Что изменено

1. **Нативно-полные строки GameDB** (`fh4_native_defaults.py`, `database_graph.py`).
   Каждая вставляемая строка получает значение во всех столбцах, которые определены у всех
   строк базовой GameDB. Стратегия по умолчанию — самое частое нативное значение; для смещений
   камеры — медиана. Заполнения пишутся в `database-graph.json` (`native_defaults` у строки).
   При readback сборка падает, если в таком столбце остался NULL.
   Для Suzuki это закрыло: `CameraOverrides.CamFollow*MaxHeightOffset` (0),
   `CamNoWheelCockpitOffsetX/Y/Z` (0, 0.02, 0.25), `Data_Engine.FuelBurnPerHorsepowerHour`,
   `List_UpgradeBrakes.*DrumDepthMM`, `GearRatio9/10`, `List_TorqueCurve.v186+`. Во флотской
   сборке — также поля Honda/RUF (`BeamStiffness`, `BumpStopHeight`, `Flex*`).
2. **Камеры капота и кокпита.** На 197 машинах, которые есть в обеих играх, FH4-значения
   `CamHoodHeightOffset`, `CamHoodZOffset`, `CamCockpitOffsetX/Y/Z` перерисованы заново
   (корреляция с FM4 < 0.13). Они заменяются нативной медианой (у Suzuki: капот 0.61/+0.31 →
   0.20/−0.50). Остальные поля камеры совпадают в 86–100% и переносятся из FM4.
3. **Якоря водителя** (`fh4_driver_rig.py`, `fh4-driver-rig-v1.json`). Все нативные машины,
   кроме трафика, имеют в скелете `anchor_butt`, руки на спице руля, кости педалей с якорями
   ног и `anchor_shifter`, плюс `IKAnchorBones.xml`. У Suzuki их не было, а катсцена покупки
   сажает по ним водителя. Теперь 10 костей добавляются в конец скелета (индексы прежних костей
   не меняются) со смещениями донора `FOR_FiestaST_14` от спицы руля; для правого руля смещения
   зеркалятся. `IKAnchorBones.xml` копируется у донора и проверяется против скелета.
4. **Локаторы сцены.** К 9 авторским локаторам добавлены 51 донорский (`entryDoor*`,
   `doorHandle*`, `seatCam*`, `lookClose*`, `dashboard`, `gauges`, `hood`, `Engine`, `Exhaust_*`,
   `Audio_*`, `trunk`, `wing` …). Позиции: X и Z — аффинная подгонка по фарам, фонарям и рулю,
   Y — сдвиг. Кости дверей у нас нет, поэтому все привязаны к `<root>`.
5. Донорские данные извлекаются скриптом `research/extract_driver_rig.py`; сборка от
   установки игры не зависит.

## Сборка и установка (выполнено 2026-11-22)

```powershell
& .\expanded_garage\Build-IntegrationPreview.ps1 -Output '.scratch/expanded-garage-v1.4.6/rebuild' -V1 '.scratch/expanded-garage' ...
```

(остальные аргументы — как в `docs/v1.4.5/INSTALL_AND_ROLLBACK.md`). Результат: Suzuki
`offline-trial-eligible`, verify solo и флота пройден, NULL-аудит чистый.

v1.4.5 (транзакция `500d128b5ffe429798a9508abd01eebd`) откачена; v1.4.6 установлена в
`game/ForzaHorizon4` как транзакция **`c64d51bfd0294845820a44c302dc7e21`**.
Откат:

```powershell
python -m expanded_garage rollback --host C:\Forza_Research\game\ForzaHorizon4 --transaction c64d51bfd0294845820a44c302dc7e21
```

## Что проверить в игре

CarID остался 12000, поэтому профиль с уже купленной в v1.4.5 Suzuki совместим. Для чистого
результата по катсцене лучше начать с профиля до покупки.

1. Autoshow → Suzuki → покупка. **Катсцена должна дойти до конца** (раньше — фриз на пролёте
   камеры при посадке).
2. My Cars → выбрать Suzuki (раньше — вылет в терраин-стриминге из-за NaN камеры).
3. Выезд: газ, руль, тормоз; переключить камеры (капот, кокпит, бампер).
4. Перезапуск игры → загрузка Suzuki из гаража.

Если фриз останется: записать последний шаг и запустить пассивный наблюдатель камеры
(`research/`) на `CPresentationCamera+0x20..0x5F` во время катсцены.

## Не входит в фазу 1

Цвета производителя, покраска, стёкла и оптика, нормализация колёс — фаза 2
(см. `AUDIT_AND_PLAN.md`).
