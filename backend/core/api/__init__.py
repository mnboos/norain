from ninja import NinjaAPI

from ..auth.backend import session_auth
from .community import owner_router as sharing_router
from .community import public_router
from .elevation import router as elevation_router
from .gpx import router as gpx_router
from .journey import router as journey_router
from .places import router as places_router
from .recurring_route import router as routes_router
from .route_weather import router as weather_router
from .system import router as system_router

api = NinjaAPI(auth=session_auth, title="MeteoLane API")
api.add_router("", elevation_router)
api.add_router("", weather_router)
api.add_router("", routes_router)
api.add_router("", places_router)
api.add_router("", gpx_router)
api.add_router("", journey_router)
api.add_router("", sharing_router)
api.add_router("", public_router)
api.add_router("/system", system_router)
