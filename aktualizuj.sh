#!/bin/bash
# Stiahne najnovšiu verziu stránky z GitHubu a reštartuje ju na PythonAnywhere.
# Spustenie v Bash konzole:  bash ~/oz-zvjs/aktualizuj.sh
set -e
cd "$(dirname "$0")"
git pull --ff-only
# Knižnica na čítanie starých súborov Excel (.xls) pre evidenciu členov.
for py in python3.10 python3.11 python3.12 python3.13; do
  if command -v "$py" >/dev/null && ! "$py" -c "import xlrd" 2>/dev/null; then
    "$py" -m pip install --user --quiet xlrd || true
  fi
done
touch /var/www/ozzvjs_pythonanywhere_com_wsgi.py
echo "Stránka je aktualizovaná."
