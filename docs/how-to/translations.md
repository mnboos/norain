# Maintain the translations (German and English)

Meteolane speaks German (the source language) and English. The texts live in **two
places**, one per side of the app, and a third kind of text — codes — joins them:

| Where | What | Files | Tooling |
| --- | --- | --- | --- |
| Frontend | Everything the SPA draws: labels, buttons, messages, chart and map texts | `frontend/src/locales/de.json` (source), `en.json` | vue-i18n; the Vite plugin compiles the catalogs, ESLint checks the keys, a unit test checks both files match |
| Backend | Prose the server writes: API error `detail`s, validation messages, mails, ride briefings, default names ("… – Rückfahrt") | German in the Python/template source itself; English in `backend/core/locale/en/LC_MESSAGES/django.po` (+ compiled `django.mo`) | Django gettext, `just messages` |
| Both | **Codes** the server sends and the SPA words: ride band and cause, rain/frost/wind-effort levels, the departure explanation, journey reasons, forecast-job and planning errors, weather codes | Python `Literal`s in `core/ride_quality.py`, `core/forecast_schemas.py`, `core/api/journey.py`, `core/tasks.py`; words in the frontend catalogs | The generated API client carries the codes as enums |

The rules behind this split are in CLAUDE.md, "Internationalisation". In short: text the
server stores, caches, sends from a worker, or that the SPA branches on, is a **code**, and
only the frontend words it. `gettext` is only for text built inside a request, and for mails
and briefings.

## Decide where a new text goes

1. **It is on screen and the SPA builds it** → a frontend key ([below](#frontend-add-or-change-a-text)).
2. **It is a message the API sends back in a request** (`HttpError`, a `detail`, a validator
   error) → backend `gettext` ([below](#backend-add-or-change-a-text)).
3. **It goes out without a request** (mail, briefing, push) → backend `gettext`, rendered under
   `translation.override(user.language)`.
4. **The server computes it and it is stored, reused across readers, sent over the WebSocket,
   or the SPA has to tell its values apart** → a code ([below](#add-a-code-the-spa-words)).
   Never send German (or English) words for these: one forecast job serves readers in both
   languages.

When in doubt: if the same answer could be read by a German and an English user, it must be a
code.

## Frontend: add or change a text

1. Add the key to **both** `frontend/src/locales/de.json` and `en.json`, in the same place.
   Group keys by area (`routes.*`, `journeyDay.*`, `map.*`, …); reuse `common.*` (cancel, save,
   close, …) instead of adding another "Abbrechen".
2. Use it:
   - in a component: `const { t } = useI18n();` then `{{ t("routes.notFound") }}` or
     `:label="t('common.save')"`;
   - in a `.ts` module: `import { t } from "@/i18n";` — **inside a function**, never at module
     scope. A constant built at import time keeps the language the page loaded with. For
     lists of options, use a function or a getter (see `utils/poiCategories.ts`,
     `utils/bikeProfiles.ts`, `utils/randomRides.ts` `headingOptions()`), or a `computed` in
     the component.
3. Message syntax:
   - Placeholders: `"Route „{name}“ löschen?"` → `t("…", { name })`.
   - Plurals: `"{n} Tag | {n} Tage"` → `t("journeys.days", n)`. Three forms mean zero | one |
     many: `"Keine Route | Route gespeichert. | {n} Routen gespeichert."`.
   - `{ } @ $ |` are special. Write a literal one as `{'@'}` (e.g. an email placeholder
     `"deine{'@'}adresse.ch"`). The build fails on a malformed message, which is how you find
     out.
   - No HTML in messages. Where the code builds HTML (map popups in `NiceMap.vue`), only the
     text parts come from `t()`.
4. Dates and numbers: pass `intlLocale()` to `Intl.*` / `toLocale*`, and `dateFnsLocale()` to
   date-fns, both from `@/i18n`, inside a `computed` or render so a language switch reformats.
   Never hard-code `"de-CH"`.
5. Check:

   ```sh
   cd frontend
   npm run type-check          # keys are typed from de.json
   npx eslint .                # no-missing-keys (error), no-raw-text (warning)
   npm run test:unit           # includes src/locales/__tests__/catalogs.spec.ts
   npm run build-only          # compiles every message
   ```

   The catalog test fails if the two files differ in keys, placeholders or the number of plural
   forms. Unit tests render in German, so German texts in test expectations stay valid.

To rename or remove a key, change both files and every `t("…")` that uses it; ESLint's
`no-missing-keys` finds the ones you missed.

`src/pages/welcome.vue` is the one exception: it keeps its own `de`/`en` copy object in the
component. Edit both halves there.

## Backend: add or change a text

1. Write the **German** text in the code and wrap it:

   ```python
   from django.utils.translation import gettext

   raise HttpError(404, gettext("Route nicht gefunden."))
   raise HttpError(402, gettext("Höchstens %(n)s Fotos pro Route.") % {"n": limit})
   ```

   - Import `gettext` by name, not as `_`: several modules use `_` as a throwaway variable.
   - Use named `%(name)s` placeholders, never f-strings — an f-string is formatted before the
     lookup and never matches the catalog. Write a literal `%` as `%%` in a message with
     placeholders.
   - A long message may be split into adjacent string literals inside `gettext(...)`; it is
     still one message.
   - For module-level constants (a dict of messages, like `EXPLANATIONS` in
     `core/briefings.py`) use `gettext_lazy`, so the text is looked up when it is used.
2. In a mail template (`core/templates/**`):

   ```django
   {% load i18n %}
   {% translate "Dein Anmeldecode" %}
   {% blocktranslate with site_name=current_site.name %}Dein Anmeldecode für {{ site_name }}:{% endblocktranslate %}
   ```

   `blocktranslate` takes plain variables only: bind attribute lookups with `with`.
3. Text sent without a request (a task, a webhook) must choose the language itself:

   ```python
   from django.utils import translation

   with translation.override(user.language):
       body = gettext("…")
   ```

   `AccountAdapter.send_mail` already does this for allauth's mails, using the recipient's
   account if the address has one.
4. Update the English catalog and compile it:

   ```sh
   just messages
   ```

   This runs `makemessages --locale en` (adds new msgids to `django.po`, marks changed ones
   `fuzzy`) and `compilemessages`. Open `backend/core/locale/en/LC_MESSAGES/django.po`, fill
   every empty `msgstr ""`, check every `#, fuzzy` entry and remove the `fuzzy` flag, then run
   `just messages` once more so the `.mo` has your translations. Django ignores fuzzy entries,
   so an English user would still see German.

   `just messages` needs GNU gettext on the machine: `apt install gettext` (Debian/Ubuntu),
   `brew install gettext` (macOS) or `winget install mlocati.GetText` (Windows). The app itself
   does not need it at runtime, because the compiled `.mo` is committed.
5. Commit **both** `django.po` and `django.mo`. A `.po` without a fresh `.mo` means English
   users see German.
6. Check:

   ```sh
   cd backend
   .venv/Scripts/python.exe manage.py test core.test_i18n   # the .mo is there and current
   .venv/Scripts/python.exe manage.py test core
   ```

   Backend tests run in German. A test that asserts on a message asserts the German text; to
   test the English one, wrap the request in `translation.override("en")`, send
   `HTTP_ACCEPT_LANGUAGE="en"`, or sign in a user with `language="en"` (see
   `core/test_i18n.py`).

Changing the German wording of an existing message changes its msgid: `just messages` marks the
old English entry fuzzy, so review it as in step 4.

## Add a code the SPA words

For example a new ride-quality level, a new journey reason or a new job error.

1. **Backend:** add the value to the `Literal` that types it (`RideBand`, `WindEffortLevel`,
   `ImpactLevel` in `core/ride_quality.py`; `DepartureExplanation` in `core/forecast_schemas.py`;
   `JourneyReasonOut.kind` in `core/api/journey.py`), and return it from the code that decides
   it. Errors stored by workers are plain strings: see the codes in `core/tasks.py`
   (`"weather_failed"`, `"no_route"`, …).
2. **Client:** regenerate it when a `Literal` changed:
   `just export-openapi-schema && just update-api--build-only`.
3. **Frontend:** add the words to both catalogs under the matching prefix:

   | Code | Catalog keys | Worded by |
   | --- | --- | --- |
   | ride band / cause | `levels.band.*`, `levels.cause.*` | `utils/levels.ts` `rideLabelText` |
   | rain / frost level | `levels.impact.*` | `utils/levels.ts` `impactText` |
   | wind effort | `levels.windEffort.*` | `utils/levels.ts`, `utils/wind.ts` |
   | WMO weather code | `weather.wmo.<code>` | `utils/levels.ts` `weatherCodeText` |
   | departure explanation | `departures.explanation.*` | `DepartureComparison.vue` |
   | journey reason `kind` | `journeys.reason.*` | `utils/journeys.ts` `reasonText` |
   | forecast job error | `errors.job.*` | `utils/serverErrors.ts` `jobErrorText` |
   | journey plan error | `errors.plan.*` | `utils/serverErrors.ts` `planErrorText` |

   An unknown code shows as the code itself (or, for errors, the stored text), so a new code
   deployed before its words is ugly but not broken.
4. If the briefing mentions it (the departure explanation does), add the words on the backend
   too (`EXPLANATIONS` in `core/briefings.py`) and run `just messages`.
5. Update tests on both sides: backend tests assert codes, frontend tests assert the German
   words.

## Add a language

Both sides and the account model need it:

1. **Backend:** add it to `LANGUAGES` in `backend/backend/settings/base.py`. `User.language`
   takes its choices from there, so `just manage makemigrations core` creates a migration. Run
   `just manage makemessages --locale <code>`, translate
   `core/locale/<code>/LC_MESSAGES/django.po`, then `just manage compilemessages`. Add it to
   the `messages` recipe in `.justfile`.
2. **Frontend:**
   - `src/locales/<code>.json` with the same keys as `de.json`, imported in `src/i18n/index.ts`
     and added to `messages`, `AppLocale`, `LOCALES` and `isAppLocale`;
   - `intlLocale()` and `dateFnsLocale()` in `src/i18n/index.ts`;
   - Quasar's language pack in `QUASAR_LANGS` (`src/composables/useLocale.ts`);
   - an option in `components/LanguageSwitcher.vue`;
   - the catalog test (`src/locales/__tests__/catalogs.spec.ts`) compares `de` with `en` only;
     add the new file there;
   - the landing page's own copy object in `pages/welcome.vue`.
3. Decide the fallback: `browserLocale()` in `useLocale.ts` sends browsers that name no
   supported language to English.

## Checklist before a commit that touches texts

- [ ] New frontend keys are in both `de.json` and `en.json`; `npm run test:unit` and
      `npm run build-only` pass.
- [ ] New backend strings are wrapped in `gettext` with German msgids and named placeholders.
- [ ] `just messages` ran, `django.po` has no empty `msgstr` and no `fuzzy`, and both `django.po`
      and `django.mo` are staged.
- [ ] Nothing the server stores or sends from a worker is a sentence; new codes have words in
      both catalogs.
