# RouteWeatherApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**coreApiRouteWeatherForecastJob**](RouteWeatherApi.md#coreapirouteweatherforecastjob) | **GET** /api/forecast_jobs/{job_id} | Forecast Job |
| [**coreApiRouteWeatherForecastJobFigures**](RouteWeatherApi.md#coreapirouteweatherforecastjobfigures) | **GET** /api/forecast_jobs/{job_id}/figures | Forecast Job Figures |
| [**coreApiRouteWeatherForecastJobMapDetail**](RouteWeatherApi.md#coreapirouteweatherforecastjobmapdetail) | **GET** /api/forecast_jobs/{job_id}/map_detail | Forecast Job Map Detail |
| [**coreApiRouteWeatherForecastJobSampleUncertainty**](RouteWeatherApi.md#coreapirouteweatherforecastjobsampleuncertainty) | **GET** /api/forecast_jobs/{job_id}/samples/{index}/uncertainty | Forecast Job Sample Uncertainty |
| [**coreApiRouteWeatherRouteWeather**](RouteWeatherApi.md#coreapirouteweatherrouteweather) | **GET** /api/route_weather | Route Weather |



## coreApiRouteWeatherForecastJob

> ForecastJobOut coreApiRouteWeatherForecastJob(jobId)

Forecast Job

Poll one forecast job.  The WebSocket is the primary channel; this exists so a client behind a proxy that drops upgrades still makes progress, and so tests can assert without a socket.

### Example

```ts
import {
  Configuration,
  RouteWeatherApi,
} from '';
import type { CoreApiRouteWeatherForecastJobRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RouteWeatherApi();

  const body = {
    // string
    jobId: 38400000-8cf0-11bd-b23e-10b96e4ef00d,
  } satisfies CoreApiRouteWeatherForecastJobRequest;

  try {
    const data = await api.coreApiRouteWeatherForecastJob(body);
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
| **jobId** | `string` |  | [Defaults to `undefined`] |

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

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## coreApiRouteWeatherForecastJobFigures

> Array&lt;{ [key: string]: any | null; }&gt; coreApiRouteWeatherForecastJobFigures(jobId)

Forecast Job Figures

The Plotly chart figures of a finished job, for the pages that draw charts.

### Example

```ts
import {
  Configuration,
  RouteWeatherApi,
} from '';
import type { CoreApiRouteWeatherForecastJobFiguresRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RouteWeatherApi();

  const body = {
    // string
    jobId: 38400000-8cf0-11bd-b23e-10b96e4ef00d,
  } satisfies CoreApiRouteWeatherForecastJobFiguresRequest;

  try {
    const data = await api.coreApiRouteWeatherForecastJobFigures(body);
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
| **jobId** | `string` |  | [Defaults to `undefined`] |

### Return type

**Array<{ [key: string]: any | null; }>**

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


## coreApiRouteWeatherForecastJobMapDetail

> ForecastMapDetailOut coreApiRouteWeatherForecastJobMapDetail(jobId, detail)

Forecast Job Map Detail

The route line and wind arrows at more detail than the job result carries.  The map asks for this only once it is zoomed in far enough to show the difference.

### Example

```ts
import {
  Configuration,
  RouteWeatherApi,
} from '';
import type { CoreApiRouteWeatherForecastJobMapDetailRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RouteWeatherApi();

  const body = {
    // string
    jobId: 38400000-8cf0-11bd-b23e-10b96e4ef00d,
    // 'medium' | 'full'
    detail: detail_example,
  } satisfies CoreApiRouteWeatherForecastJobMapDetailRequest;

  try {
    const data = await api.coreApiRouteWeatherForecastJobMapDetail(body);
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
| **jobId** | `string` |  | [Defaults to `undefined`] |
| **detail** | `medium`, `full` |  | [Defaults to `undefined`] [Enum: medium, full] |

### Return type

[**ForecastMapDetailOut**](ForecastMapDetailOut.md)

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


## coreApiRouteWeatherForecastJobSampleUncertainty

> ForecastUncertainty coreApiRouteWeatherForecastJobSampleUncertainty(jobId, index)

Forecast Job Sample Uncertainty

One sample\&#39;s full ensemble spread, including the per-model breakdown.  &#x60;&#x60;null&#x60;&#x60; when the sample has none -- which includes every sample of a free account, since the stored result is stripped before storage.

### Example

```ts
import {
  Configuration,
  RouteWeatherApi,
} from '';
import type { CoreApiRouteWeatherForecastJobSampleUncertaintyRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RouteWeatherApi();

  const body = {
    // string
    jobId: 38400000-8cf0-11bd-b23e-10b96e4ef00d,
    // number
    index: 56,
  } satisfies CoreApiRouteWeatherForecastJobSampleUncertaintyRequest;

  try {
    const data = await api.coreApiRouteWeatherForecastJobSampleUncertainty(body);
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
| **jobId** | `string` |  | [Defaults to `undefined`] |
| **index** | `number` |  | [Defaults to `undefined`] |

### Return type

[**ForecastUncertainty**](ForecastUncertainty.md)

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


## coreApiRouteWeatherRouteWeather

> ForecastJobOut coreApiRouteWeatherRouteWeather(startLat, startLon, destLat, destLon, profile, departureTime, intervalSeconds)

Route Weather

Start (or join) the forecast for an ad-hoc route.  Returns 200 with the payload when an identical forecast is already computed and still fresh, otherwise 202 and a job to watch over &#x60;wsUrl&#x60;. The routing call and every provider fetch happen on workers -- this endpoint never blocks on them.

### Example

```ts
import {
  Configuration,
  RouteWeatherApi,
} from '';
import type { CoreApiRouteWeatherRouteWeatherRequest } from '';

async function example() {
  console.log("🚀 Testing  SDK...");
  const api = new RouteWeatherApi();

  const body = {
    // number
    startLat: 8.14,
    // number
    startLon: 8.14,
    // number
    destLat: 8.14,
    // number
    destLon: 8.14,
    // string
    profile: profile_example,
    // string
    departureTime: departureTime_example,
    // number (optional)
    intervalSeconds: 56,
  } satisfies CoreApiRouteWeatherRouteWeatherRequest;

  try {
    const data = await api.coreApiRouteWeatherRouteWeather(body);
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
| **startLat** | `number` |  | [Defaults to `undefined`] |
| **startLon** | `number` |  | [Defaults to `undefined`] |
| **destLat** | `number` |  | [Defaults to `undefined`] |
| **destLon** | `number` |  | [Defaults to `undefined`] |
| **profile** | `string` |  | [Defaults to `undefined`] |
| **departureTime** | `string` |  | [Defaults to `undefined`] |
| **intervalSeconds** | `number` |  | [Optional] [Defaults to `300`] |

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

