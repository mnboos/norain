# Maintain the translations (German and English)

Meteolane has two languages: **German** and **English**. German is the main language. Every
text is written in German first, and then translated to English.

The texts live in **two places**:

| Part | What it contains | Where the texts are |
| --- | --- | --- |
| **Frontend** (the Vue app) | Everything you see in the app: buttons, labels, messages, map and chart texts | `frontend/src/locales/de.json` (German) and `frontend/src/locales/en.json` (English) |
| **Backend** (Django) | Error messages from the API, e-mails, ride briefings | German: directly in the Python code and the mail templates. English: `backend/core/locale/en/LC_MESSAGES/django.po` |

There is also a third kind of text: **codes**. The backend sometimes sends a short code
instead of a sentence, for example `"heavy"` for heavy rain or `"no_route"` for a planning
error. The frontend turns the code into words in the user's language. More about codes
[below](#codes-sent-by-the-backend).

The rules behind this design are in `CLAUDE.md`, section "Internationalisation".

## Which language does a user see?

1. Signed in: the language saved in the account (changed with the DE/EN switch in the header or
   on the account page).
2. Not signed in: the last choice made in this browser.
3. Otherwise: the browser's language. German browsers get German, English browsers get
   English, all other browsers (French, Italian, …) get English.

The frontend sends the chosen language to the backend with every request, so API messages come
back in the same language. E-mails and briefings use the language saved in the account.

## Where does my new text go?

- **You see it in the app, and the frontend builds it** → frontend
  ([how](#frontend-add-or-change-a-text)).
- **It is an error message from the API** → backend ([how](#backend-add-or-change-a-text)).
- **It is in an e-mail, a briefing or a push message** → backend.
- **The backend calculates it, and it is saved, or shared by several users, or the frontend
  must react to its value** → a code ([how](#codes-sent-by-the-backend)).

If you are unsure, ask: "Could a German user and an English user see this same result?" If yes,
the backend must send a code, not a sentence.

## Frontend: add or change a text

1. Add the text to **both** files, with the **same key**:

   `frontend/src/locales/de.json`

   ```json
   "routes": { "notFound": "Route nicht gefunden." }
   ```

   `frontend/src/locales/en.json`

   ```json
   "routes": { "notFound": "Route not found." }
   ```

   Look for an existing key first. For example, `common.cancel`, `common.save` and
   `common.close` already exist.
2. Use the key in the code:
   - In a `.vue` file:

     ```ts
     const { t } = useI18n();
     ```

     ```vue
     {{ t("routes.notFound") }}
     <q-btn :label="t('common.save')" />
     ```

   - In a `.ts` file: `import { t } from "@/i18n";` and call `t(...)` **inside a function**.
     Do not call it at the top level of the file. A text made when the file loads stays in the
     old language when the user switches.
3. Special cases:
   - **A value inside the text:** `"Route „{name}“ löschen?"` → `t("…", { name: route.name })`.
   - **One or many:** `"{n} Tag | {n} Tage"` → `t("journeys.days", count)`.
   - **The characters `{ } @ $ |`** have a special meaning. To show one of them as normal
     text, write it like this: `{'@'}`. Example: `"deine{'@'}adresse.ch"`.
   - **No HTML** inside the texts.
   - **Dates and numbers:** use `intlLocale()` and `dateFnsLocale()` from `@/i18n`. Never
     write `"de-CH"` in the code.
4. Check your work (in the `frontend` folder):

   ```sh
   npm run type-check
   npx eslint .
   npm run test:unit
   npm run build-only
   ```

   These checks tell you when a key is missing, when `de.json` and `en.json` do not have the
   same keys, or when a text has a syntax error.

To rename or delete a key, change it in both files and in the code. `npx eslint .` shows the
places you missed.

**Exception:** the landing page `src/pages/welcome.vue` has its texts in the file itself, in a
German part and an English part. Change both parts.

## Backend: add or change a text

### One-time setup: install GNU gettext

The backend command that updates the English file needs a free tool called **GNU gettext**.
You install it once per computer. (The app itself runs without it.)

| System | Command |
| --- | --- |
| Windows | `winget install mlocati.GetText` |
| Debian / Ubuntu | `sudo apt install gettext` |
| macOS | `brew install gettext` |

**Windows:** after the install, **close the terminal and open a new one** (also restart your
IDE or Claude Code session). The new terminal knows where gettext is; the old one does not.

Check that it works:

```sh
msguniq --version
```

If this prints a version number, you are ready.

### Steps

1. Write the German text in the code and wrap it in `gettext(...)`:

   ```python
   from django.utils.translation import gettext

   raise HttpError(404, gettext("Route nicht gefunden."))
   ```

   A text with a value inside:

   ```python
   gettext("Höchstens %(n)s Fotos pro Route.") % {"n": limit}
   ```

   Rules:
   - Always write the **German** text in the code.
   - Use `%(name)s` for values. **Do not use f-strings** (`f"…{x}"`): they cannot be
     translated.
   - Write `gettext`, not `_`.
   - For a list of texts at the top of a file, use `gettext_lazy` instead of `gettext`.
2. In an e-mail template (in `backend/core/templates/`):

   ```django
   {% load i18n %}
   {% translate "Dein Anmeldecode" %}
   {% blocktranslate with site_name=current_site.name %}Dein Anmeldecode für {{ site_name }}:{% endblocktranslate %}
   ```

3. Code that runs **without a request** (a background task, an e-mail, a Stripe webhook) must
   pick the language itself:

   ```python
   from django.utils import translation

   with translation.override(user.language):
       text = gettext("…")
   ```

4. Update the English file:

   ```sh
   just messages
   ```

5. Open `backend/core/locale/en/LC_MESSAGES/django.po`. Your new German texts are now in the
   file. Each one has an empty English line under it:

   ```
   msgid "Route nicht gefunden."
   msgstr ""
   ```

   Write the English text between the quotes of `msgstr`. Also look for lines marked
   `#, fuzzy`: the German text changed there, and gettext guessed the English. Check the
   English and delete the `#, fuzzy` line. (Texts marked fuzzy are not used: English users
   would see German.)
6. Run `just messages` **again**. This writes your English texts into `django.mo`, the file the
   app actually reads.
7. Commit **both** files: `django.po` and `django.mo`.
8. Check your work (in the `backend` folder):

   ```sh
   .venv/Scripts/python.exe manage.py test core
   ```

   The tests run in German. `core/test_i18n.py` also checks that the English file is there and
   up to date.

If you change the German wording of an existing text, it counts as a new text: do steps 4–7
again.

## Codes sent by the backend

Some values come from the backend as short codes, and the frontend shows them as words:

| What | Example codes | Frontend keys |
| --- | --- | --- |
| Ride quality | `very_good`, `poor` | `levels.band.*`, `levels.cause.*` |
| Rain or frost level | `light`, `moderate`, `heavy` | `levels.impact.*` |
| Wind effort | `tailwind`, `high` | `levels.windEffort.*` |
| Weather type (WMO code) | `61`, `95` | `weather.wmo.*` |
| Why a departure is recommended | `less_rain` | `departures.explanation.*` |
| Why a journey variant ranks where it does | `day_limit`, `detour` | `journeys.reason.*` |
| Why a forecast failed | `weather_failed` | `errors.job.*` |
| Why a journey could not be planned | `no_route` | `errors.plan.*` |

To add a new code:

1. **Backend:** add the new code to the list of allowed values (a Python `Literal`, for example
   `ImpactLevel` in `core/ride_quality.py`) and send it.
2. **API client:** if you changed a `Literal`, regenerate the client:
   `just export-openapi-schema && just update-api--build-only`.
3. **Frontend:** add the words for the new code to `de.json` and `en.json`, under the key from
   the table.
4. Update the tests on both sides.

If the words are missing, the app shows the code itself. That looks bad, but nothing breaks.

## Add a third language

This needs changes on both sides:

- **Backend:** add the language to `LANGUAGES` in `backend/backend/settings/base.py`, create a
  migration (`just manage makemigrations core`), create and translate the new `.po` file
  (`just manage makemessages --locale fr`), and add the language to the `messages` recipe in
  `.justfile`.
- **Frontend:** a new file `src/locales/fr.json` with the same keys, and the new language in
  `src/i18n/index.ts`, `src/composables/useLocale.ts` (the Quasar texts),
  `components/LanguageSwitcher.vue`, the test `src/locales/__tests__/catalogs.spec.ts` and the
  landing page `src/pages/welcome.vue`.

## Problems

### `just messages` says "GNU gettext is not installed (or not on PATH)"

GNU gettext is missing. Install it (see [one-time setup](#one-time-setup-install-gnu-gettext)),
then open a **new** terminal and try again.

### `CommandError: Can't find msguniq. Make sure you have GNU gettext tools 0.19 or newer installed.`

Same problem, same fix. You see this message when you run `manage.py makemessages` directly
instead of `just messages`.

### I installed gettext, but it still says it is missing

The terminal was open before the install. Close it and open a new one. If you use an IDE or
Claude Code, restart it too. Then check with `msguniq --version`.

### `ModuleNotFoundError: No module named '_cffi_backend'`

This is not a gettext problem: the Python environment of the backend is broken. It can
happen on Windows when `uv` updates packages while the backend or a worker is running (a
running program locks some files, so the update stops half-way). Fix:

1. Stop the backend (`just backend`) and all workers (`just worker`).
2. In the `backend` folder, reinstall the broken package:

   ```sh
   uv pip install --reinstall cffi
   ```

   If another module is missing, run `uv sync --reinstall` instead: it reinstalls everything.
3. Run `just messages` again.

### An English user sees a German text

- **Frontend:** the key is missing in `en.json`. Run `npm run test:unit`: the catalog test
  shows missing keys.
- **Backend:** the English text is missing in `django.po`, is marked `#, fuzzy`, or
  `django.mo` was not updated. Fill in the text, remove `#, fuzzy`, run `just messages`, and
  commit both files.

## Checklist before you commit

- [ ] Every new frontend key is in `de.json` **and** `en.json`.
- [ ] `npm run test:unit` and `npm run build-only` pass.
- [ ] New backend texts are German, in `gettext(...)`, with `%(name)s` for values.
- [ ] `just messages` ran, `django.po` has no empty `msgstr ""` and no `#, fuzzy`.
- [ ] `django.po` **and** `django.mo` are committed.
- [ ] The backend sends codes, not sentences, for anything saved or shared between users.

[Documentation index](../README.md)
