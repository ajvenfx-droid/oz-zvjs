"""Automatická aktualizácia stránky z GitHubu (webhook).

Po každom pushnutí do repozitára GitHub zavolá /nasadenie/github. Stránka overí podpis
tajným kľúčom, stiahne novú verziu (git pull) a reštartuje sa (dotkne sa WSGI súboru).
"""
import glob
import hashlib
import hmac
import os
import secrets
import subprocess
from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from anketa import len_spravca, nastav, nastavenie
from db import get_db

bp = Blueprint("nasadenie", __name__)

ADRESAR = os.path.dirname(os.path.abspath(__file__))


def _wsgi_subor():
    subory = glob.glob("/var/www/*_wsgi.py")
    return subory[0] if subory else None


def _tajomstvo(db):
    t = nastavenie(db, "webhook_tajomstvo")
    if not t:
        t = secrets.token_hex(20)
        nastav(db, "webhook_tajomstvo", t)
        db.commit()
    return t


def aktualizuj():
    """Stiahne novú verziu a reštartuje stránku. Vráti (ok, výpis)."""
    try:
        p = subprocess.run(["git", "pull", "--ff-only"], cwd=ADRESAR, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return False, str(e)
    vypis = (p.stdout + p.stderr).strip()
    if p.returncode != 0:
        return False, vypis
    wsgi = _wsgi_subor()
    if wsgi and "Already up to date" not in vypis:
        Path(wsgi).touch()
    return True, vypis


@bp.route("/nasadenie/github", methods=["POST"])
def github():
    db = get_db()
    podpis = request.headers.get("X-Hub-Signature-256", "")
    ocakavany = "sha256=" + hmac.new(_tajomstvo(db).encode(), request.get_data(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(podpis, ocakavany):
        abort(403)
    if request.headers.get("X-GitHub-Event") == "ping":
        return "ok"
    ok, vypis = aktualizuj()
    nastav(db, "posledne_nasadenie", vypis[-500:])
    db.commit()
    return (vypis, 200 if ok else 500, {"Content-Type": "text/plain; charset=utf-8"})


@bp.route("/spravca/aktualizacia", methods=["GET", "POST"])
@len_spravca
def spravca():
    db = get_db()
    if request.method == "POST" and request.form.get("akcia") == "novy_kluc":
        nastav(db, "webhook_tajomstvo", secrets.token_hex(20))
        db.commit()
        flash("Vytvorený nový tajný kľúč. Vložte ho na GitHube do nastavení webhooku (Edit → Secret).")
        return redirect(url_for("nasadenie.spravca"))
    if request.method == "POST":
        ok, vypis = aktualizuj()
        flash(("Stránka je aktualizovaná. " if ok else "Aktualizácia zlyhala: ") + vypis[-300:])
        return redirect(url_for("nasadenie.spravca"))
    return render_template(
        "nasadenie.html",
        adresa=url_for("nasadenie.github", _external=True),
        tajomstvo=_tajomstvo(db),
        posledne=nastavenie(db, "posledne_nasadenie") or "",
    )
