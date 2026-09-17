
# DepartureCandidate


## Properties

Name | Type
------------ | -------------
`departureTime` | string
`arrivalTime` | string
`available` | boolean
`rideScore` | number
`rideLabel` | string

## Example

```typescript
import type { DepartureCandidate } from ''

// TODO: Update the object below with actual values
const example = {
  "departureTime": null,
  "arrivalTime": null,
  "available": null,
  "rideScore": null,
  "rideLabel": null,
} satisfies DepartureCandidate

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as DepartureCandidate
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


