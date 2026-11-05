"""Webová stránka Odborového zväzu Zboru väzenskej a justičnej stráže."""
import os

from flask import Flask, abort, render_template

import anketa
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
app.teardown_appcontext(close_db)
app.register_blueprint(anketa.bp)


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
    app.secret_key = anketa.secret_key(db)
    db.close()


@app.context_processor
def inject_globals():
    return {"nazov_zvazu": NAZOV_ZVAZU}


@app.route("/")
def index():
    db = get_db()
    organizacie = db.execute(
        "SELECT slug, mesto, nazov FROM zakladna_organizacia ORDER BY mesto"
    ).fetchall()
    return render_template(
        "index.html", organizacie=organizacie, ankety=anketa.aktivne_ankety(db)
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
