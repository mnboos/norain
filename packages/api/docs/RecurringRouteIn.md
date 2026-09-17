
# RecurringRouteIn


## Properties

Name | Type
------------ | -------------
`name` | string
`description` | string
`startLat` | number
`startLon` | number
`startName` | string
`destLat` | number
`destLon` | number
`destName` | string
`profile` | string
`scheduleCron` | string
`scheduleDescription` | string
`departureFlexBeforeMinutes` | number
`departureFlexAfterMinutes` | number
`active` | boolean

## Example

```typescript
import type { RecurringRouteIn } from ''

// TODO: Update the object below with actual values
const example = {
  "name": null,
  "description": null,
  "startLat": null,
  "startLon": null,
  "startName": null,
  "destLat": null,
  "destLon": null,
  "destName": null,
  "profile": null,
  "scheduleCron": null,
  "scheduleDescription": null,
  "departureFlexBeforeMinutes": null,
  "departureFlexAfterMinutes": null,
  "active": null,
} satisfies RecurringRouteIn

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as RecurringRouteIn
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


