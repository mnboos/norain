
# EnsembleModelStatistics


## Properties

Name | Type
------------ | -------------
`metrics` | [{ [key: string]: EnsembleRange; }](EnsembleRange.md)
`model` | string
`pop` | number
`rainIfWet` | number

## Example

```typescript
import type { EnsembleModelStatistics } from ''

// TODO: Update the object below with actual values
const example = {
  "metrics": null,
  "model": null,
  "pop": null,
  "rainIfWet": null,
} satisfies EnsembleModelStatistics

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as EnsembleModelStatistics
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


