
# PropertiesSchema


## Properties

Name | Type
------------ | -------------
`city` | string
`countrycode` | string
`name` | string
`showCanton` | boolean
`state` | string

## Example

```typescript
import type { PropertiesSchema } from ''

// TODO: Update the object below with actual values
const example = {
  "city": null,
  "countrycode": null,
  "name": null,
  "showCanton": null,
  "state": null,
} satisfies PropertiesSchema

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as PropertiesSchema
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


