from enum import Enum


class TrustLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ChannelKind(str, Enum):
    CLI = "cli"
    WEB = "web"
    MOBILE_WEB = "mobile_web"
    WEIXIN = "weixin"
    CRON = "cron"
    EXTERNAL = "external"
