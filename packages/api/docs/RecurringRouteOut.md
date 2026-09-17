
# RecurringRouteOut


## Properties

Name | Type
------------ | -------------
`id` | string
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
`totalSeconds` | number
`totalDistanceM` | number
`hasGeometry` | boolean
`nextDeparture` | string
`forecastAvailable` | boolean
`createdAt` | Date
`updatedAt` | Date
`thumbnail` | [RouteThumbnail](RouteThumbnail.md)

## Example

```typescript
import type { RecurringRouteOut } from ''

// TODO: Update the object below with actual values
const example = {
  "id": null,
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
  "totalSeconds": null,
  "totalDistanceM": null,
  "hasGeometry": null,
  "nextDeparture": null,
  "forecastAvailable": null,
  "createdAt": null,
  "updatedAt": null,
  "thumbnail": null,
} satisfies RecurringRouteOut

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as RecurringRouteOut
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


