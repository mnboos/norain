
# WindDistribution


## Properties

Name | Type
------------ | -------------
`calmM` | number
`crosswindM` | number
`feltCoveredM` | number
`headwindM` | number
`maxFeltSpeed` | number
`meanFeltSpeed` | number
`tailwindM` | number
`timingSource` | string
`unknownM` | number

## Example

```typescript
import type { WindDistribution } from ''

// TODO: Update the object below with actual values
const example = {
  "calmM": null,
  "crosswindM": null,
  "feltCoveredM": null,
  "headwindM": null,
  "maxFeltSpeed": null,
  "meanFeltSpeed": null,
  "tailwindM": null,
  "timingSource": null,
  "unknownM": null,
} satisfies WindDistribution

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as WindDistribution
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


