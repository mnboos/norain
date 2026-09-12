
# RouteSection


## Properties

Name | Type
------------ | -------------
`condition` | string
`endKm` | number
`endTime` | string
`maxHeadwind` | number
`maxRainMm` | number
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
  "endKm": null,
  "endTime": null,
  "maxHeadwind": null,
  "maxRainMm": null,
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


