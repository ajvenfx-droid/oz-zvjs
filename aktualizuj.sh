#!/bin/bash
# Stiahne najnovšiu verziu stránky z GitHubu a reštartuje ju na PythonAnywhere.
# Spustenie v Bash konzole:  bash ~/oz-zvjs/aktualizuj.sh
set -e
cd "$(dirname "$0")"
git pull --ff-only
touch /var/www/ozzvjs_pythonanywhere_com_wsgi.py
echo "Stránka je aktualizovaná."
