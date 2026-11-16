"""Lecture de la configuration depuis backend/.env."""
import re
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env (ce fichier est dans backend/src/api)
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

_CHAMPS_ENTIERS = (
    "oracle_port",
    "oracle_pool_min",
    "oracle_pool_max",
    "oracle_pool_increment",
    "app_port",
    "refresh_interval_minutes",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Connexion Oracle ---
    oracle_user: str = ""
    oracle_password: str = ""
    oracle_host: str = ""
    oracle_port: int = 1521
    oracle_service_name: str = ""

    # --- Pool ---
    oracle_pool_min: int = 1
    oracle_pool_max: int = 4
    oracle_pool_increment: int = 1

    # --- App ---
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: str = "http://localhost:5174"
    log_level: str = "INFO"

    # --- Schéma Oracle source ---
    oracle_schema: str = "CRM_SNDE"

    refresh_interval_minutes: int = 5

    # --- Base locale DuckDB (chemin relatif au dossier backend) ---
    duckdb_path: str = "data/controle_versements.duckdb"

    @field_validator(*_CHAMPS_ENTIERS, mode="before")
    @classmethod
    def _vide_vers_defaut(cls, valeur, info):
        """Une ligne laissée vide dans le .env reprend la valeur par défaut."""
        if valeur is None or str(valeur).strip() == "":
            return cls.model_fields[info.field_name].default
        return valeur

    @field_validator("oracle_schema")
    @classmethod
    def _schema_valide(cls, valeur):
        valeur = (valeur or "CRM_SNDE").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$#]*", valeur):
            raise ValueError("ORACLE_SCHEMA doit être un nom de schéma Oracle simple")
        return valeur

    @property
    def dsn(self) -> str:
        return f"{self.oracle_host}:{self.oracle_port}/{self.oracle_service_name}"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def refresh_seconds(self) -> int:
        return max(1, self.refresh_interval_minutes) * 60

    @property
    def oracle_configure(self) -> bool:
        return bool(self.oracle_user and self.oracle_host and self.oracle_service_name)


@lru_cache
def get_settings() -> Settings:
    return Settings()
