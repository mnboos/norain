
# RouteWeatherSummary


## Properties

Name | Type
------------ | -------------
`firstRainEta` | string
`firstRainPlace` | string
`maxFrostLevel` | string
`maxHeadwind` | number
`maxRainMm` | number
`maxWindEffortLevel` | string
`maxWindPowerW` | number
`rainAmount` | number
`rainProbability` | number
`source` | string
`stationCorrected` | boolean
`willRain` | boolean
`windDistribution` | [WindDistribution](WindDistribution.md)

## Example

```typescript
import type { RouteWeatherSummary } from ''

// TODO: Update the object below with actual values
const example = {
  "firstRainEta": null,
  "firstRainPlace": null,
  "maxFrostLevel": null,
  "maxHeadwind": null,
  "maxRainMm": null,
  "maxWindEffortLevel": null,
  "maxWindPowerW": null,
  "rainAmount": null,
  "rainProbability": null,
  "source": null,
  "stationCorrected": null,
  "willRain": null,
  "windDistribution": null,
} satisfies RouteWeatherSummary

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as RouteWeatherSummary
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


