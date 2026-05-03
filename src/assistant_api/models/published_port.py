from __future__ import annotations

import ipaddress
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PublishedPort:
    host_port: int
    host: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.host_port, int) or self.host_port < 1 or self.host_port > 65535:
            raise ValueError("host_port must be an integer TCP port")
        if self.host is None:
            return
        try:
            ipaddress.ip_address(self.host)
        except ValueError as error:
            raise ValueError("host must be an IP address literal") from error
