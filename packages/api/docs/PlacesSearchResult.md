
# PlacesSearchResult


## Properties

Name | Type
------------ | -------------
`geometry` | [GeometrySchema](GeometrySchema.md)
`properties` | [PropertiesSchema](PropertiesSchema.md)
`type` | string

## Example

```typescript
import type { PlacesSearchResult } from ''

// TODO: Update the object below with actual values
const example = {
  "geometry": null,
  "properties": null,
  "type": null,
} satisfies PlacesSearchResult

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as PlacesSearchResult
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


