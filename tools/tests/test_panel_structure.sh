#!/usr/bin/env bash
# Структурные проверки панели (без браузера): раскладка, выбор камеры, режим правки, меню.
set -u
JS="$(cd "$(dirname "$0")/../.." && pwd)/custom_components/rain_techlan/frontend/panel.js"
fail=0
chk() { if grep -q "$2" "$JS"; then echo "  PASS  $1"; else echo "  FAIL  $1"; fail=$((fail+1)); fi; }
chk "раскладка 1/2/4 (классы)"        "frames n"
chk "поле выбора раскладки"           'id="layout"'
chk "сохранение раскладки"            "rain_layout"
chk "выбор камеры в раскладке 1"      "rain_one_cam"
chk "селектор камеры"                 'id="camsel"'
chk "кнопка правки зон"               "Редактировать зоны"
chk "режим правки (флаг)"             "rain_zone_edit"
chk "перетаскивание зон"              "pointerdown"
chk "ручка размера зон"               "nwse-resize"
chk "рисование новой зоны"            "Добавить зону"
chk "меню (выход)"                    "mLogout"
chk "чистый кадр для панели"          "raw_"
chk "кадр обновляется по метке прохода" 'raw_${encodeURIComponent'
chk "троттлинг обновлений"            "_lastLoad"
exit $fail
