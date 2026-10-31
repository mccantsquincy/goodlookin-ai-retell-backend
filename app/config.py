from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    database_url: str
    retell_api_key: str = ""
    retell_signature_bypass: bool = False
    business_config_path: Path = Path("business.local.json")
    demo_dashboard_username: str = ""
    demo_dashboard_password: SecretStr = SecretStr("")
    calendar_provider: Literal["fake", "calcom"] = "fake"
    calcom_api_key: SecretStr = SecretStr("")
    calcom_event_type_id: int | None = None
    calcom_provisioning_api_key: SecretStr = SecretStr("")
    calcom_schedules_api_version: str = ""
    calcom_event_types_api_version: str = ""
    provisioning_admin_password: SecretStr = SecretStr("")
    provisioning_admin_origin: str = ""

    @model_validator(mode="after")
    def safe_signature_configuration(self) -> "Settings":
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif self.database_url.startswith("postgres://"):
            self.database_url = self.database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        if self.app_env in {"staging", "production"} and self.retell_signature_bypass:
            raise ValueError("signature bypass cannot be enabled in staging or production")
        if self.app_env in {"staging", "production"} and self.retell_api_key in {
            "",
            "your_retell_api_key_here",
        }:
            raise ValueError("RETELL_API_KEY is required in staging and production")
        if self.calendar_provider == "calcom" and (
            not self.calcom_api_key.get_secret_value() or not self.calcom_event_type_id or self.calcom_event_type_id < 1
        ):
            raise ValueError("CALCOM_API_KEY and CALCOM_EVENT_TYPE_ID are required for Cal.com calendar")
        if self.provisioning_admin_origin:
            from urllib.parse import urlsplit

            parsed = urlsplit(self.provisioning_admin_origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("PROVISIONING_ADMIN_ORIGIN must be an exact HTTPS origin without path")
        admin_password = self.provisioning_admin_password.get_secret_value()
        if admin_password and len(admin_password) < 32:
            raise ValueError("PROVISIONING_ADMIN_PASSWORD must contain at least 32 characters")
        password = self.demo_dashboard_password.get_secret_value()
        if bool(self.demo_dashboard_username) != bool(password) or (password and len(password) < 16):
            raise ValueError("DEMO_DASHBOARD_USERNAME and DEMO_DASHBOARD_PASSWORD (16+ characters) are both required")
        return self
