from .application import router as application_router
from .channel_mirror import router as channel_mirror_router
from .navigation import router as navigation_router
from .payments import router as payments_router

routers = [
    channel_mirror_router,
    navigation_router,
    application_router,
    payments_router,
]
