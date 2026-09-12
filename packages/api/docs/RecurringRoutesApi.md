# RecurringRoutesApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**coreApiRecurringRouteCreateRoute**](RecurringRoutesApi.md#coreapirecurringroutecreateroute) | **POST** /api/routes | Create Route |
| [**coreApiRecurringRouteDeleteRoute**](RecurringRoutesApi.md#coreapirecurringroutedeleteroute) | **DELETE** /api/routes/{route_id} | Delete Route |
| [**coreApiRecurringRouteGetRoute**](RecurringRoutesApi.md#coreapirecurringroutegetroute) | **GET** /api/routes/{route_id} | Get Route |
| [**coreApiRecurringRouteListRoutes**](RecurringRoutesApi.md#coreapirecurringroutelistroutes) | **GET** /api/routes | List Routes |
| [**coreApiRecurringRouteRouteForecast**](RecurringRoutesApi.md#coreapirecurringrouterouteforecast) | **GET** /api/routes/{route_id}/forecast | Route Forecast |
| [**coreApiRecurringRouteUpdateRoute**](RecurringRoutesApi.md#coreapirecurringrouteupdateroute) | **PUT** /api/routes/{route_id} | Update Route |



## coreApiRecurringRouteCreateRoute

> RecurringRouteOut coreApiRecurringRouteCreateRoute(recurringRouteIn)

Create Route

Create a new recurring route. Enqueues a background task to fetch route geometry.

### Example

```ts
import {
  Configuration,
  RecurringRoutesApi,
} from '';
import type { CoreApiRecurringRouteCreateRouteRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RecurringRoutesApi();

  const body = {
    // RecurringRouteIn
    recurringRouteIn: ...,
  } satisfies CoreApiRecurringRouteCreateRouteRequest;

  try {
    const data = await api.coreApiRecurringRouteCreateRoute(body);
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
| **recurringRouteIn** | [RecurringRouteIn](RecurringRouteIn.md) |  | |

### Return type

[**RecurringRouteOut**](RecurringRouteOut.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | OK |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## coreApiRecurringRouteDeleteRoute

> coreApiRecurringRouteDeleteRoute(routeId)

Delete Route

Delete a recurring route.

### Example

```ts
import {
  Configuration,
  RecurringRoutesApi,
} from '';
import type { CoreApiRecurringRouteDeleteRouteRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RecurringRoutesApi();

  const body = {
    // string
    routeId: 38400000-8cf0-11bd-b23e-10b96e4ef00d,
  } satisfies CoreApiRecurringRouteDeleteRouteRequest;

  try {
    const data = await api.coreApiRecurringRouteDeleteRoute(body);
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
| **routeId** | `string` |  | [Defaults to `undefined`] |

### Return type

`void` (Empty response body)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: Not defined


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **204** | No Content |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## coreApiRecurringRouteGetRoute

> RecurringRouteOut coreApiRecurringRouteGetRoute(routeId)

Get Route

Get a single recurring route by ID.

### Example

```ts
import {
  Configuration,
  RecurringRoutesApi,
} from '';
import type { CoreApiRecurringRouteGetRouteRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RecurringRoutesApi();

  const body = {
    // string
    routeId: 38400000-8cf0-11bd-b23e-10b96e4ef00d,
  } satisfies CoreApiRecurringRouteGetRouteRequest;

  try {
    const data = await api.coreApiRecurringRouteGetRoute(body);
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
| **routeId** | `string` |  | [Defaults to `undefined`] |

### Return type

[**RecurringRouteOut**](RecurringRouteOut.md)

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


## coreApiRecurringRouteListRoutes

> Array&lt;RecurringRouteOut&gt; coreApiRecurringRouteListRoutes()

List Routes

List the current account\&#39;s active recurring routes by next departure.

### Example

```ts
import {
  Configuration,
  RecurringRoutesApi,
} from '';
import type { CoreApiRecurringRouteListRoutesRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RecurringRoutesApi();

  try {
    const data = await api.coreApiRecurringRouteListRoutes();
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters

This endpoint does not need any parameter.

### Return type

[**Array&lt;RecurringRouteOut&gt;**](RecurringRouteOut.md)

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


## coreApiRecurringRouteRouteForecast

> ForecastJobOut coreApiRecurringRouteRouteForecast(routeId, date, time)

Route Forecast

Start (or join) the forecast for one departure of a saved route.  Returns 200 with the finished payload -- weather, Plotly figures and sections -- when an identical forecast is already computed and still fresh, otherwise 202 and a job to watch over &#x60;wsUrl&#x60;. Cell fetching and figure rendering both happen on workers; neither is allowed on this path.

### Example

```ts
import {
  Configuration,
  RecurringRoutesApi,
} from '';
import type { CoreApiRecurringRouteRouteForecastRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RecurringRoutesApi();

  const body = {
    // string
    routeId: 38400000-8cf0-11bd-b23e-10b96e4ef00d,
    // string
    date: date_example,
    // string
    time: time_example,
  } satisfies CoreApiRecurringRouteRouteForecastRequest;

  try {
    const data = await api.coreApiRecurringRouteRouteForecast(body);
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
| **routeId** | `string` |  | [Defaults to `undefined`] |
| **date** | `string` |  | [Defaults to `undefined`] |
| **time** | `string` |  | [Defaults to `undefined`] |

### Return type

[**ForecastJobOut**](ForecastJobOut.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | OK |  -  |
| **202** | Accepted |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## coreApiRecurringRouteUpdateRoute

> RecurringRouteOut coreApiRecurringRouteUpdateRoute(routeId, recurringRouteIn)

Update Route

Update a recurring route. Re-fetches geometry if start, destination, or profile changed.

### Example

```ts
import {
  Configuration,
  RecurringRoutesApi,
} from '';
import type { CoreApiRecurringRouteUpdateRouteRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RecurringRoutesApi();

  const body = {
    // string
    routeId: 38400000-8cf0-11bd-b23e-10b96e4ef00d,
    // RecurringRouteIn
    recurringRouteIn: ...,
  } satisfies CoreApiRecurringRouteUpdateRouteRequest;

  try {
    const data = await api.coreApiRecurringRouteUpdateRoute(body);
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
| **routeId** | `string` |  | [Defaults to `undefined`] |
| **recurringRouteIn** | [RecurringRouteIn](RecurringRouteIn.md) |  | |

### Return type

[**RecurringRouteOut**](RecurringRouteOut.md)

### Authorization

No authorization required

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | OK |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

