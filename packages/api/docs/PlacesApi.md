# PlacesApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**coreApiPlacesSearch**](PlacesApi.md#coreapiplacessearch) | **GET** /api/search | Search |



## coreApiPlacesSearch

> Array&lt;PlacesSearchResult&gt; coreApiPlacesSearch(query, zoom, lat, lon)

Search

### Example

```ts
import {
  Configuration,
  PlacesApi,
} from '';
import type { CoreApiPlacesSearchRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new PlacesApi();

  const body = {
    // string
    query: query_example,
    // number
    zoom: 8.14,
    // number
    lat: 8.14,
    // number
    lon: 8.14,
  } satisfies CoreApiPlacesSearchRequest;

  try {
    const data = await api.coreApiPlacesSearch(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **query** | `string` |  | [Defaults to `undefined`] |
| **zoom** | `number` |  | [Defaults to `undefined`] |
| **lat** | `number` |  | [Defaults to `undefined`] |
| **lon** | `number` |  | [Defaults to `undefined`] |

### Return type

[**Array&lt;PlacesSearchResult&gt;**](PlacesSearchResult.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | OK |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

