
# RouteThumbnailSample

Weather inputs for one point in a route-list thumbnail.

## Properties

Name | Type
------------ | -------------
`headwind` | number
`i` | number
`precipitationIntervalS` | number
`rainMm` | number
`rainRateMmH` | number
`temp` | number

## Example

```typescript
import type { RouteThumbnailSample } from ''

// TODO: Update the object below with actual values
const example = {
  "headwind": null,
  "i": null,
  "precipitationIntervalS": null,
  "rainMm": null,
  "rainRateMmH": null,
  "temp": null,
} satisfies RouteThumbnailSample

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as RouteThumbnailSample
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


