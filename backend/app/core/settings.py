from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    allow_dev_identity: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap",
            ),
            allow_dev_identity=os.getenv("IAP_ALLOW_DEV_IDENTITY", "false").lower()
            in {"1", "true", "yes"},
        )


settings = Settings.from_env()
