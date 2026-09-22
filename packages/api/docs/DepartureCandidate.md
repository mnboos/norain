
# DepartureCandidate


## Properties

Name | Type
------------ | -------------
`arrivalTime` | string
`available` | boolean
`departureTime` | string
`rideLabel` | string
`rideScore` | number

## Example

```typescript
import type { DepartureCandidate } from ''

// TODO: Update the object below with actual values
const example = {
  "arrivalTime": null,
  "available": null,
  "departureTime": null,
  "rideLabel": null,
  "rideScore": null,
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


