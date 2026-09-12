
# ForecastUncertaintySummary

A sample\'s ensemble spread without the per-model breakdown.  The breakdown is most of the bytes and only the details panel shows it, for one point at a time, so it comes from ``/forecast_jobs/{id}/samples/{index}/uncertainty``.

## Properties

Name | Type
------------ | -------------
`fetchedAt` | string
`forecastTime` | string
`metrics` | [{ [key: string]: EnsembleRange; }](EnsembleRange.md)
`pop` | number
`precipitationIntervalS` | number
`rainIfWet` | number
`source` | string

## Example

```typescript
import type { ForecastUncertaintySummary } from ''

// TODO: Update the object below with actual values
const example = {
  "fetchedAt": null,
  "forecastTime": null,
  "metrics": null,
  "pop": null,
  "precipitationIntervalS": null,
  "rainIfWet": null,
  "source": null,
} satisfies ForecastUncertaintySummary

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ForecastUncertaintySummary
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


