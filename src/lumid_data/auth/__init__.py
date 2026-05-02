"""Lumid OAuth federation plugin (auth-parity mirror of Lumilake's plugin)."""

from ..server.hooks import IDENTITY_PROVIDERS
from .lumid_introspect import LumidIdentityProvider


def install() -> None:
    IDENTITY_PROVIDERS.append(LumidIdentityProvider())


__all__ = ["LumidIdentityProvider", "install"]
