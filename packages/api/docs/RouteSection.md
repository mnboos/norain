
# RouteSection


## Properties

Name | Type
------------ | -------------
`condition` | string
`endIndex` | number
`endKm` | number
`endTime` | string
`frostLevel` | string
`maxHeadwind` | number
`maxRainMm` | number
`startIndex` | number
`startKm` | number
`startTime` | string
`tempMax` | number
`tempMin` | number

## Example

```typescript
import type { RouteSection } from ''

// TODO: Update the object below with actual values
const example = {
  "condition": null,
  "endIndex": null,
  "endKm": null,
  "endTime": null,
  "frostLevel": null,
  "maxHeadwind": null,
  "maxRainMm": null,
  "startIndex": null,
  "startKm": null,
  "startTime": null,
  "tempMax": null,
  "tempMin": null,
} satisfies RouteSection

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as RouteSection
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


