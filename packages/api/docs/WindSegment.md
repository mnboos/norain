
# WindSegment


## Properties

Name | Type
------------ | -------------
`bearing` | number
`crosswind` | number
`elapsedS` | number
`endM` | number
`feltAngle` | number
`feltCoverage` | number
`feltSpeed` | number
`headwind` | number
`lat` | number
`lon` | number
`riderSpeed` | number
`startM` | number
`windCoverage` | number
`windDir` | number
`windSpeed` | number

## Example

```typescript
import type { WindSegment } from ''

// TODO: Update the object below with actual values
const example = {
  "bearing": null,
  "crosswind": null,
  "elapsedS": null,
  "endM": null,
  "feltAngle": null,
  "feltCoverage": null,
  "feltSpeed": null,
  "headwind": null,
  "lat": null,
  "lon": null,
  "riderSpeed": null,
  "startM": null,
  "windCoverage": null,
  "windDir": null,
  "windSpeed": null,
} satisfies WindSegment

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as WindSegment
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


