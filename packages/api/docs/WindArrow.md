
# WindArrow

One real-wind arrow for the map: only segments with complete ground-wind data.

## Properties

Name | Type
------------ | -------------
`bearing` | number
`lat` | number
`lon` | number
`windDir` | number
`windEffort` | number
`windEffortLevel` | string
`windPowerW` | number
`windSpeed` | number

## Example

```typescript
import type { WindArrow } from ''

// TODO: Update the object below with actual values
const example = {
  "bearing": null,
  "lat": null,
  "lon": null,
  "windDir": null,
  "windEffort": null,
  "windEffortLevel": null,
  "windPowerW": null,
  "windSpeed": null,
} satisfies WindArrow

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as WindArrow
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


