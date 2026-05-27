"""In-memory pending-confirmation store (Step 4).

Holds `{confirmation_id: SSE-session-handle}` so the `/agent/confirm`
endpoint can resume the right runner. Empty placeholder until Step 4.
"""

from __future__ import annotations
