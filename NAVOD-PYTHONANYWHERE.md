# Zverejnenie stránky online cez PythonAnywhere

Výsledok: stránka bude dostupná pre všetkých na adrese `https://VASE-MENO.pythonanywhere.com`.
Bezplatný účet stačí na začiatok. Vlastnú doménu (napr. `oz-zvjs.sk`) sa dá pripojiť neskôr
s plateným účtom.

## 1. Založenie účtu

1. Otvorte <https://www.pythonanywhere.com> a kliknite na **Pricing & signup**.
2. Zvoľte **Create a Beginner account** (zadarmo).
3. Používateľské meno bude súčasťou adresy stránky, napr. `ozzvjs` → `ozzvjs.pythonanywhere.com`.
4. Potvrďte e-mail.

## 2. Nahranie súborov

1. Hore kliknite na **Files**.
2. Kliknite na **Upload a file** a nahrajte `oz-zvjs.zip`.
3. Hore kliknite na **Consoles** → **Bash** a napíšte:

```bash
unzip oz-zvjs.zip
ls oz-zvjs
```

Mali by ste vidieť `app.py`, `templates`, `static` a ďalšie súbory.

## 3. Vytvorenie webovej aplikácie

1. Hore kliknite na **Web** → **Add a new web app** → **Next**.
2. Zvoľte **Manual configuration** (nie „Flask“) a najnovší **Python 3.x**.
3. Na stránke Web nájdite riadok **WSGI configuration file** a kliknite na odkaz súboru.
4. Celý obsah súboru vymažte a vložte (`VASE-MENO` nahraďte svojím menom):

```python
import sys
sys.path.insert(0, "/home/VASE-MENO/oz-zvjs")

from app import app as application
```

5. Kliknite **Save**.
6. Na stránke **Web** v časti **Static files** pridajte:
   - URL: `/static/`
   - Directory: `/home/VASE-MENO/oz-zvjs/static`
7. Hore na stránke **Web** kliknite na zelené tlačidlo **Reload**.

Stránka teraz beží na `https://VASE-MENO.pythonanywhere.com`.

## 4. Aktualizácia po zmenách

Kód stránky je na GitHube: <https://github.com/ajvenfx-droid/oz-zvjs>.
Zmeny sa na stránku dostanú dvoma spôsobmi:

- **Automaticky raz denne** – naplánovaná úloha na karte **Tasks** spúšťa
  `bash /home/OZZVJS/oz-zvjs/aktualizuj.sh`.
- **Hneď** – v **Bash** konzole spustite `bash ~/oz-zvjs/aktualizuj.sh`.

Skript stiahne najnovšiu verziu z GitHubu a reštartuje stránku, Reload netreba.
Databáza `oz_zvjs.db` na GitHub nejde, takže sa údaje pri aktualizácii neprepíšu.

## Dôležité pri bezplatnom účte

- Raz za mesiac sa treba prihlásiť a na stránke **Web** kliknúť
  **Run until 1 month from today**, inak sa stránka vypne.
- Ak sa niečo pokazí, chybové hlásenia sú na stránke **Web** v odkaze **Error log**.
