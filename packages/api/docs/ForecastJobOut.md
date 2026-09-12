
# ForecastJobOut

A forecast being computed in the background, and its progress.

## Properties

Name | Type
------------ | -------------
`cellsSettled` | number
`cellsTotal` | number
`error` | string
`jobId` | string
`result` | [RouteForecastOut](RouteForecastOut.md)
`status` | string
`wsUrl` | string

## Example

```typescript
import type { ForecastJobOut } from ''

// TODO: Update the object below with actual values
const example = {
  "cellsSettled": null,
  "cellsTotal": null,
  "error": null,
  "jobId": null,
  "result": null,
  "status": null,
  "wsUrl": null,
} satisfies ForecastJobOut

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ForecastJobOut
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


