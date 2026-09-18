
# RecurringRouteIn


## Properties

Name | Type
------------ | -------------
`active` | boolean
`departureFlexAfterMinutes` | number
`departureFlexBeforeMinutes` | number
`description` | string
`destLat` | number
`destLon` | number
`destName` | string
`name` | string
`profile` | string
`returnScheduleCron` | string
`returnScheduleDescription` | string
`scheduleCron` | string
`scheduleDescription` | string
`startLat` | number
`startLon` | number
`startName` | string

## Example

```typescript
import type { RecurringRouteIn } from ''

// TODO: Update the object below with actual values
const example = {
  "active": null,
  "departureFlexAfterMinutes": null,
  "departureFlexBeforeMinutes": null,
  "description": null,
  "destLat": null,
  "destLon": null,
  "destName": null,
  "name": null,
  "profile": null,
  "returnScheduleCron": null,
  "returnScheduleDescription": null,
  "scheduleCron": null,
  "scheduleDescription": null,
  "startLat": null,
  "startLon": null,
  "startName": null,
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


