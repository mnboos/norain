/**
 * Minimal Plotly build.
 *
 * plotly.js@4 is CommonJS without an `exports`/`sideEffects` field, so the bundler
 * cannot tree-shake the full `plotly.js` entry point (~4.3 MB) — traces have to be
 * included by hand. `lib/core` already brings `scatter` plus annotations, shapes,
 * colorscale, colorbar, legend and modebar. `bar` is added for the rain bars in the
 * temperature chart; that is all `utils/forecastCharts.ts` draws.
 */
import Plotly from "plotly.js/lib/core";
import bar from "plotly.js/lib/bar";

Plotly.register([bar]);

export default Plotly;
