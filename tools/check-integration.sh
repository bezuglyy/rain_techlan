#!/usr/bin/env bash
# Проверка интеграции rain_techlan: синтаксис, JSON, манифест, юнит-тесты.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CC="$ROOT/custom_components/rain_techlan"
PY_BIN="${PY_BIN:-python3}"
fail=0; pass=0
ok() { pass=$((pass+1)); echo "  PASS  $1"; }
no() { fail=$((fail+1)); echo "  FAIL  $1"; }

echo "== 1. Синтаксис Python =="
if "$PY_BIN" -m compileall -q "$CC" >/dev/null 2>&1; then ok "py_compile ($(ls "$CC"/*.py | wc -l) файлов)"; else no "py_compile"; fi

echo "== 2. JSON =="
"$PY_BIN" - "$CC" <<'PY' && ok "manifest/strings/translations валидны" || no "JSON"
import json, pathlib, sys
cc = pathlib.Path(sys.argv[1])
for f in [cc / "manifest.json", cc / "strings.json"] + list((cc / "translations").glob("*.json")):
    json.loads(f.read_text(encoding="utf-8"))
PY

echo "== 3. Манифест =="
"$PY_BIN" - "$CC" <<'PY' && ok "поля манифеста на месте" || no "манифест"
import json, pathlib, sys
m = json.loads((pathlib.Path(sys.argv[1]) / "manifest.json").read_text(encoding="utf-8"))
assert m["domain"] == "rain_techlan", m
assert m["version"] and m["name"] and m["config_flow"] is True
assert "curl_cffi" in " ".join(m.get("requirements", []))
PY

echo "== 4. Юнит-тесты =="
for t in "$ROOT"/tools/tests/test_*.py; do
  name="$(basename "$t")"
  if out="$("$PY_BIN" "$t" 2>&1)"; then ok "$name ($(echo "$out" | tail -1 | tr -d '\n'))"; else no "$name"; echo "$out" | tail -6; fi
done

echo "== 5. Ключевые файлы =="
for f in __init__.py auth.py api.py coordinator.py vision.py camera.py views.py panel.py settings.py interlocks.py switch.py number.py image.py sensor.py binary_sensor.py frontend/panel.js; do
  [ -s "$CC/$f" ] && ok "$f" || no "$f отсутствует"
done

echo
echo "ИТОГ: PASS=$pass FAIL=$fail"
[ "$fail" -eq 0 ] || exit 1
