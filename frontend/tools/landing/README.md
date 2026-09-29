# Landing-page screenshots

From `frontend/`:

```sh
npm ci
npx playwright install chromium # once per machine
npm run screenshots:landing
```

This starts a temporary Vite server on port 4317, captures seven images in German
and English at 2× resolution, and closes the browser and server. The resulting
PNGs in `public/landing/` are used by `src/pages/welcome.vue`; commit them alongside
source changes. No backend, account, API key, or live weather is needed. Internet
access to the app's CARTO basemap services is required. If Chromium is installed
elsewhere, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to its executable.

## The example

**Vevey, Place du Marché → La Tour-de-Peilz → Montreux, Quai du Casino.**
A lakeside setting with a distinctive shoreline and a bend towards Montreux.
The demo assumes 7.2 km / 24 minutes, weekday departure at 08:00 and return at
17:00. `demo.ts` contains hand-authored, simplified illustrative geometry and a
fixed fictional forecast: dry, a brief light shower, then dry again (17–20 °C).
It is a marketing example, not a navigable or verified cycling itinerary.
Wind distances sum to the example's total distance; the curved wind graphic is
an explanatory schematic, not a geographic map or computed wind simulation.

## What is captured

- `route`: the real `NiceMap` component, with the app's basemap and attribution.
- `schedule`: a curated weekly schedule illustration using the actual `WeekdaySelector` component, Quasar icons and demo departure times. It is a marketing composition rather than a screenshot of the full form.
- `forecast`: the real `ForecastSummaryCard` and `WeatherSections` components.
- `wind`: the real `WindDistributionBar` plus a small explanatory SVG diagram.
- `temperature`: the real `WeatherChart`, with temperature and precipitation.
- `elevation`: the real `ElevationChart`, using an in-memory demo query result.
- `places`: the real `NiceMap` with example drinking-water and food stop markers.

Elevations and POI locations are illustrative, not surveyed heights or verified
amenities. The landing page identifies POIs as a tour-planning feature and the
step-three screenshot as a summary within the larger forecast.

`LandingStudio.vue` arranges these components for legibility in small landing-page cards.
The studio is a separate Vite HTML entry under `tools/`; it is not a production
route and is not included in the normal application build. To preview manually,
run `npm run dev` and open `/tools/landing/index.html?lang=de` (or `en`).

The capture waits for fonts, rendered Plotly charts and MapLibre's idle event, uses a fixed locale,
timezone, light theme, viewport and reduced motion, and stages images before
replacing the published files. Tile content can still change upstream. Keep
map attribution visible. Do not run two capture processes at the same time.
