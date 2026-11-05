"""Webová stránka Odborového zväzu Zboru väzenskej a justičnej stráže."""
import os

from flask import Flask, abort, g, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import anketa
import obsah
import ucet
from db import connect, close_db, get_db

NAZOV_ZVAZU = "ODBOROVÝ ZVÄZ ZBORU VÄZENSKEJ A JUSTIČNEJ STRÁŽE"

# Základné organizácie vložené pri prvom spustení (slug, mesto, plný názov).
POCIATOCNE_ZO = [
    (
        "banska-bystrica",
        "Banská Bystrica",
        "ZÁKLADNÁ ORGANIZÁCIA ODBOROVÉHO ZVÄZU ZBORU VÄZENSKEJ A JUSTIČNEJ STRÁŽE "
        "PRI ÚSTAVE NA VÝKON VÄZBY A ÚSTAVE NA VÝKON TRESTU ODŇATIA SLOBODY "
        "BANSKÁ BYSTRICA",
    ),
]

app = Flask(__name__)
# PythonAnywhere beží za proxy; vďaka tomu majú odkazy v e-mailoch správne https://.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Mení sa pri každej zmene style.css, aby si prehliadače stiahli nový vzhľad.
VERZIA_STYLOV = int(os.path.getmtime(os.path.join(app.static_folder, "style.css")))
app.teardown_appcontext(close_db)
app.register_blueprint(anketa.bp)
app.register_blueprint(ucet.bp)
app.register_blueprint(obsah.bp)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # nahrávané dokumenty


def init_db():
    db = connect()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS zakladna_organizacia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            mesto TEXT NOT NULL,
            nazov TEXT NOT NULL,
            popis TEXT NOT NULL DEFAULT ''
        )
        """
    )
    db.executemany(
        "INSERT OR IGNORE INTO zakladna_organizacia (slug, mesto, nazov) VALUES (?, ?, ?)",
        POCIATOCNE_ZO,
    )
    db.commit()
    anketa.init_db(db)
    ucet.init_db(db)
    obsah.init_db(db)
    app.secret_key = anketa.secret_key(db)
    db.close()


def zostav_menu():
    """Hlavné menu ako na ozzvjs.sk; aktívna položka sa určí podľa adresy."""
    clen = getattr(g, "clen", None)
    polozky = [
        ("Domov", url_for("index"), []),
        ("Aktuality", url_for("obsah.clanky", typ="aktuality"), []),
        ("O nás", url_for("obsah.o_nas"), [
            ("O zväze", url_for("obsah.o_nas")),
            ("Základné organizácie", url_for("obsah.o_nas") + "#organizacie"),
        ]),
        ("Venujte 2 % z daní", url_for("obsah.stranka", slug="venujte-2-percenta"), []),
        ("Výkon kontroly BOZP", url_for("obsah.stranka", slug="bozp"), []),
        ("Dokumenty", url_for("obsah.dokumenty"), [
            (nazov, url_for("obsah.dokumenty", kategoria_slug=slug))
            for slug, nazov, _ in obsah.KATEGORIE_DOKUMENTOV
        ]),
        ("Pre členov", url_for("obsah.pre_clenov"), [
            ("Benefity", url_for("obsah.stranka", slug="benefity")),
            ("Podporný fond", url_for("obsah.stranka", slug="podporny-fond")),
            ("Ankety a dokumenty pre členov", url_for("obsah.pre_clenov")),
            ("Moje konto", url_for("ucet.moje_konto")) if clen
            else ("Prihlásiť sa", url_for("ucet.prihlasenie")),
        ]),
        ("Tlačové správy", url_for("obsah.clanky", typ="tlacove-spravy"), []),
        ("Kontakty", url_for("obsah.stranka", slug="kontakty"), [
            ("Kontakty", url_for("obsah.stranka", slug="kontakty")),
            ("Predsedníctvo", url_for("obsah.stranka", slug="predsednictvo")),
            ("Rada predsedov", url_for("obsah.stranka", slug="rada-predsedov")),
            ("Revízna komisia", url_for("obsah.stranka", slug="revizna-komisia")),
        ]),
    ]
    cesta = request.path
    menu = []
    for nazov, url, podmenu in polozky:
        adresy = [url] + [u.split("#")[0] for _, u in podmenu]
        if url == "/":
            aktivna = cesta == "/"
        else:
            aktivna = any(cesta == a or cesta.startswith(a.rstrip("/") + "/") for a in adresy)
        menu.append({
            "nazov": nazov, "url": url, "aktivna": aktivna,
            "podmenu": [{"nazov": n, "url": u} for n, u in podmenu],
        })
    return menu


@app.context_processor
def inject_globals():
    return {
        "nazov_zvazu": NAZOV_ZVAZU,
        "verzia_stylov": VERZIA_STYLOV,
        "logo_subor": "logo.png",
        "menu": zostav_menu() if request else [],
    }


@app.route("/")
def index():
    db = get_db()
    return render_template(
        "index.html",
        uvod=obsah.nacitaj_stranku("uvod"),
        ankety=anketa.aktivne_ankety(db),
        aktuality=obsah.najnovsie_clanky(db, "aktuality"),
        tlacove=obsah.najnovsie_clanky(db, "tlacove-spravy"),
    )


@app.route("/zo/<slug>")
def organizacia(slug):
    zo = get_db().execute(
        "SELECT * FROM zakladna_organizacia WHERE slug = ?", (slug,)
    ).fetchone()
    if zo is None:
        abort(404)
    return render_template("organizacia.html", zo=zo)


@app.errorhandler(404)
def nenajdene(exc):
    return render_template("404.html"), 404


init_db()

if __name__ == "__main__":
    # 0.0.0.0 = dostupné aj z ostatných počítačov vo vnútornej sieti.
    host = os.environ.get("OZ_HOST", "0.0.0.0")
    port = int(os.environ.get("OZ_PORT", "5000"))
    app.run(host=host, port=port, debug=os.environ.get("OZ_DEBUG") == "1")
