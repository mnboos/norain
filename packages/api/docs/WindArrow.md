
# WindArrow

One felt-wind arrow for the map: only segments with complete felt-wind data.

## Properties

Name | Type
------------ | -------------
`bearing` | number
`feltAngle` | number
`feltSpeed` | number
`lat` | number
`lon` | number

## Example

```typescript
import type { WindArrow } from ''

// TODO: Update the object below with actual values
const example = {
  "bearing": null,
  "feltAngle": null,
  "feltSpeed": null,
  "lat": null,
  "lon": null,
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


