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
chk "кадр обновляется по метке прохода" '"heat_" : "raw_"'
chk "кнопка тепловой карты" 'Тепловая карта'
chk "троттлинг обновлений"            "_lastLoad"
# нет литеральных подстановок в id (частая причина пустой страницы)
if grep -qE 'this\.\$\("\$\{' "$JS"; then echo "  FAIL  литеральные подстановки в id"; fail=$((fail+1)); else echo "  PASS  нет литеральных подстановок в id"; fi
# все id, используемые в JS, есть в разметке
python3 - "$JS" <<'PYEOF' || fail=$((fail+1))
import re, sys, pathlib
src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
html = src[src.index("this.innerHTML"): src.index("this.$ = (id)")]
ids_html = set(re.findall(r'id="([A-Za-z0-9_]+)"', html))
ids_js = set(re.findall(r'this\.\$\(\s*["\']([^"\']+)["\']\s*\)', src))
missing = sorted(i for i in ids_js if i not in ids_html)
print("  PASS  id разметки ↔ JS" if not missing else f"  FAIL  нет в разметке: {missing}")
sys.exit(0 if not missing else 1)
PYEOF

exit $fail
