
# ForecastMapDetailOut

The route line and wind arrows at one detail level.

## Properties

Name | Type
------------ | -------------
`line` | Array&lt;Array&lt;number&gt;&gt;
`windArrows` | [Array&lt;WindArrow&gt;](WindArrow.md)

## Example

```typescript
import type { ForecastMapDetailOut } from ''

// TODO: Update the object below with actual values
const example = {
  "line": null,
  "windArrows": null,
} satisfies ForecastMapDetailOut

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ForecastMapDetailOut
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


