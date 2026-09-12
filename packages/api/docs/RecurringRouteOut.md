
# RecurringRouteOut


## Properties

Name | Type
------------ | -------------
`active` | boolean
`createdAt` | Date
`description` | string
`destLat` | number
`destLon` | number
`destName` | string
`forecastAvailable` | boolean
`hasGeometry` | boolean
`id` | string
`name` | string
`nextDeparture` | string
`profile` | string
`scheduleCron` | string
`scheduleDescription` | string
`startLat` | number
`startLon` | number
`startName` | string
`thumbnail` | [RouteThumbnail](RouteThumbnail.md)
`totalDistanceM` | number
`totalSeconds` | number
`updatedAt` | Date

## Example

```typescript
import type { RecurringRouteOut } from ''

// TODO: Update the object below with actual values
const example = {
  "active": null,
  "createdAt": null,
  "description": null,
  "destLat": null,
  "destLon": null,
  "destName": null,
  "forecastAvailable": null,
  "hasGeometry": null,
  "id": null,
  "name": null,
  "nextDeparture": null,
  "profile": null,
  "scheduleCron": null,
  "scheduleDescription": null,
  "startLat": null,
  "startLon": null,
  "startName": null,
  "thumbnail": null,
  "totalDistanceM": null,
  "totalSeconds": null,
  "updatedAt": null,
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


