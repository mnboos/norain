
# ForecastUncertainty


## Properties

Name | Type
------------ | -------------
`fetchedAt` | string
`forecastTime` | string
`metrics` | [{ [key: string]: EnsembleRange; }](EnsembleRange.md)
`models` | [Array&lt;EnsembleModelStatistics&gt;](EnsembleModelStatistics.md)
`pop` | number
`precipitationIntervalS` | number
`rainIfWet` | number
`requestedModels` | Array&lt;string&gt;
`source` | string

## Example

```typescript
import type { ForecastUncertainty } from ''

// TODO: Update the object below with actual values
const example = {
  "fetchedAt": null,
  "forecastTime": null,
  "metrics": null,
  "models": null,
  "pop": null,
  "precipitationIntervalS": null,
  "rainIfWet": null,
  "requestedModels": null,
  "source": null,
} satisfies ForecastUncertainty

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ForecastUncertainty
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


