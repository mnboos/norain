
# RouteThumbnail

The route-list glyph: the simplified path and the ride quality of its worst sample.  The stored blob keeps the raw sample weather; only the verdict leaves the server, scored when the list is served (see ``core.ride_quality``).

## Properties

Name | Type
------------ | -------------
`computedAt` | string
`departure` | string
`path` | Array&lt;Array&lt;number&gt;&gt;
`rideLabel` | string
`rideScore` | number

## Example

```typescript
import type { RouteThumbnail } from ''

// TODO: Update the object below with actual values
const example = {
  "computedAt": null,
  "departure": null,
  "path": null,
  "rideLabel": null,
  "rideScore": null,
} satisfies RouteThumbnail

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as RouteThumbnail
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


