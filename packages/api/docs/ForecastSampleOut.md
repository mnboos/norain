
# ForecastSampleOut


## Properties

Name | Type
------------ | -------------
`crosswind` | number
`elapsedS` | number
`eta` | string
`headwind` | number
`lat` | number
`lon` | number
`pop` | number
`precipitationIntervalS` | number
`probabilitySource` | string
`rainIfWet` | number
`rainMm` | number
`rainRateMmH` | number
`rideLabel` | string
`rideScore` | number
`sampleIndex` | number
`stationCount` | number
`temp` | number
`uncertainty` | [ForecastUncertaintySummary](ForecastUncertaintySummary.md)
`weatherCode` | number
`weatherDesc` | string
`windCoverage` | number
`windDir` | number
`windEffortLevel` | string
`windGust` | number
`windPowerW` | number
`windSpeed` | number

## Example

```typescript
import type { ForecastSampleOut } from ''

// TODO: Update the object below with actual values
const example = {
  "crosswind": null,
  "elapsedS": null,
  "eta": null,
  "headwind": null,
  "lat": null,
  "lon": null,
  "pop": null,
  "precipitationIntervalS": null,
  "probabilitySource": null,
  "rainIfWet": null,
  "rainMm": null,
  "rainRateMmH": null,
  "rideLabel": null,
  "rideScore": null,
  "sampleIndex": null,
  "stationCount": null,
  "temp": null,
  "uncertainty": null,
  "weatherCode": null,
  "weatherDesc": null,
  "windCoverage": null,
  "windDir": null,
  "windEffortLevel": null,
  "windGust": null,
  "windPowerW": null,
  "windSpeed": null,
} satisfies ForecastSampleOut

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ForecastSampleOut
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


