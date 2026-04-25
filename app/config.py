from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    log_level: str = "INFO"
    telegram_token: str = "8612287295:AAHB4et4s2y5_JBUz2w9ZC3YX2aNZOA0DIg"

    # Hardcode your GigaChat authorization key here.
    gigachat_credentials: str = "MDE5ZGIxNDYtMjQyNy03NDJlLTlhYjktN2UxNDZjYTJiNmMxOjk5ZDhhNDE0LTE2Y2YtNDI2MS1iOGVhLTIyODQyNzlmN2NkZQ=="
    gigachat_client_id: str = "019db146-2427-742e-9ab9-7e146ca2b6c1"
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat"
    gigachat_base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    gigachat_auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

    # Defaulting to False makes first-run setup easier on macOS and in RU setups
    # where the required root certificate is not installed yet.
    gigachat_verify_ssl_certs: bool = False
    gigachat_ca_bundle_file: str | None = None
    gigachat_log_balance: bool = True
    cache_enabled: bool = True
    cache_ttl_seconds: int = 60 * 60 * 3
    cache_db_path: str = "cinema_summary_cache.db"
    omdb_api_key: str = "PASTE_OMDB_API_KEY_HERE"
    tmdb_api_token: str = "PASTE_TMDB_API_TOKEN_HERE"

    @staticmethod
    def _is_missing_or_placeholder(value: str | None) -> bool:
        stripped = (value or "").strip()
        return not stripped or stripped.startswith("PASTE_")

    def has_telegram_token(self) -> bool:
        return not self._is_missing_or_placeholder(self.telegram_token)

    def has_gigachat_credentials(self) -> bool:
        return not self._is_missing_or_placeholder(self.gigachat_credentials)

    def has_omdb_api_key(self) -> bool:
        return not self._is_missing_or_placeholder(self.omdb_api_key)

    def has_tmdb_api_token(self) -> bool:
        return not self._is_missing_or_placeholder(self.tmdb_api_token)


settings = Settings()
