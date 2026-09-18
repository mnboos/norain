
# RouteForecastOut

A finished forecast as the job endpoint and the WebSocket serve it.  Slimmer than what the job stores (see ``core.jobs.forecast_view``): ``line`` is the coarse route line, ``wind_arrows`` are about 2 km apart and the samples carry no per-model breakdown. Finer map detail and one sample\'s breakdown each come from their own endpoint, keyed by ``job_id``; ``version`` changes whenever the job is recomputed under the same id. The frontend draws the charts from ``samples``.

## Properties

Name | Type
------------ | -------------
`departureComparison` | [DepartureComparison](DepartureComparison.md)
`departureTime` | string
`jobId` | string
`line` | Array&lt;Array&lt;number&gt;&gt;
`routeId` | string
`samples` | [Array&lt;ForecastSampleOut&gt;](ForecastSampleOut.md)
`sections` | [Array&lt;RouteSection&gt;](RouteSection.md)
`summary` | [RouteWeatherSummary](RouteWeatherSummary.md)
`totalDistanceM` | number
`totalSeconds` | number
`uncertaintyPartial` | boolean
`version` | string
`windArrows` | [Array&lt;WindArrow&gt;](WindArrow.md)

## Example

```typescript
import type { RouteForecastOut } from ''

// TODO: Update the object below with actual values
const example = {
  "departureComparison": null,
  "departureTime": null,
  "jobId": null,
  "line": null,
  "routeId": null,
  "samples": null,
  "sections": null,
  "summary": null,
  "totalDistanceM": null,
  "totalSeconds": null,
  "uncertaintyPartial": null,
  "version": null,
  "windArrows": null,
} satisfies RouteForecastOut

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as RouteForecastOut
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


