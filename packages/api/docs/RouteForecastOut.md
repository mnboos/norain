
# RouteForecastOut

A finished forecast as the job endpoint and the WebSocket serve it.  Slimmer than what the job stores (see ``core.jobs.forecast_view``): ``line`` is the coarse route line, ``wind_arrows`` are about 2 km apart and the samples carry no per-model breakdown. The chart figures, finer map detail and one sample\'s breakdown each come from their own endpoint, keyed by ``job_id``; ``version`` changes whenever the job is recomputed under the same id.

## Properties

Name | Type
------------ | -------------
`jobId` | string
`version` | string
`routeId` | string
`departureTime` | string
`line` | Array&lt;Array&lt;number&gt;&gt;
`totalSeconds` | number
`totalDistanceM` | number
`samples` | [Array&lt;ForecastSampleOut&gt;](ForecastSampleOut.md)
`summary` | [RouteWeatherSummary](RouteWeatherSummary.md)
`windArrows` | [Array&lt;WindArrow&gt;](WindArrow.md)
`sections` | [Array&lt;RouteSection&gt;](RouteSection.md)
`uncertaintyPartial` | boolean
`departureComparison` | [DepartureComparison](DepartureComparison.md)

## Example

```typescript
import type { RouteForecastOut } from ''

// TODO: Update the object below with actual values
const example = {
  "jobId": null,
  "version": null,
  "routeId": null,
  "departureTime": null,
  "line": null,
  "totalSeconds": null,
  "totalDistanceM": null,
  "samples": null,
  "summary": null,
  "windArrows": null,
  "sections": null,
  "uncertaintyPartial": null,
  "departureComparison": null,
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


