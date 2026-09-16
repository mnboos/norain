
# RouteThumbnail

The route-list glyph: the simplified path, the ride quality of its worst sample, and the rain and frost the row shows beside it.  The stored blob keeps the raw sample weather; only verdicts and two aggregates leave the server, scored when the list is served (see ``core.ride_quality``). The rain and frost levels are the *worst point of the ride*, not the worst-scoring sample: \"will it rain on my ride\" is a different question from \"what spoils it\".

## Properties

Name | Type
------------ | -------------
`computedAt` | string
`departure` | string
`frostLevel` | string
`maxRainRateMmH` | number
`path` | Array&lt;Array&lt;number&gt;&gt;
`rainLevel` | string
`rainProbability` | number
`rideLabel` | string
`rideScore` | number
`tempMin` | number

## Example

```typescript
import type { RouteThumbnail } from ''

// TODO: Update the object below with actual values
const example = {
  "computedAt": null,
  "departure": null,
  "frostLevel": null,
  "maxRainRateMmH": null,
  "path": null,
  "rainLevel": null,
  "rainProbability": null,
  "rideLabel": null,
  "rideScore": null,
  "tempMin": null,
} satisfies RouteThumbnail

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as RouteThumbnail
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


