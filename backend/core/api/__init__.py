from ninja import NinjaAPI

from ..auth.backend import session_auth
from .places import router as places_router
from .recurring_route import router as routes_router
from .route_weather import router as weather_router

api = NinjaAPI(auth=session_auth, title="NoRain API")
api.add_router("", weather_router)
api.add_router("", routes_router)
api.add_router("", places_router)
