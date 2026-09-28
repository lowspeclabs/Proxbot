"""ProxBot — Proxmox VE AI assistant toolkit.

Layered modules:
  - ``proxbot.common``  shared config, logging, redaction, client, rendering, safety guards
  - ``proxbot.cli``     the agent-facing CLI command/parser surface
  - ``proxbot.chat``    the interactive LLM assistant (chat.py backend)
  - ``proxbot.console`` the brokered VNC/serial console broker
"""

from .common.constants import REPO_ROOT  # noqa: F401
