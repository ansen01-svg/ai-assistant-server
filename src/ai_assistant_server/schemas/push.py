from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

# Only real browser push services, so the worker can't be pointed at internal URLs (SSRF)
PUSH_HOSTS = (
    "fcm.googleapis.com",  # Chrome, Edge (Android)
    "push.services.mozilla.com",  # Firefox
    "push.apple.com",  # Safari / iOS
    "notify.windows.com",  # Edge (Windows)
)


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class PushSubscriptionCreate(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def known_push_service(cls, v: str) -> str:
        url = urlparse(v)
        host = url.hostname or ""
        if url.scheme != "https" or not any(
            host == h or host.endswith("." + h) for h in PUSH_HOSTS
        ):
            raise ValueError("Unsupported push service")
        return v


class PushUnsubscribe(BaseModel):
    endpoint: str = Field(max_length=2000)
