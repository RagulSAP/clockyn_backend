"""Route dependency for the owner-only routes of `auth_policy.OWNER_ONLY_RULES` (WP12).

`dependencies=[Depends(require_owner)]` on a route: when a valid token identifies the caller, the user id in the path must
be the caller's own, otherwise 403. Without a token nothing is checked here (the global guard has already answered 401
when REQUIRE_AUTH is on; with it off the route behaves exactly as before). Whether a route is owner-only, and through
which path parameter, is DATA in core_functions/auth_policy.py, not repeated here.
"""
import logging

from fastapi import HTTPException, Request

from core_functions import auth_policy as policy

logger = logging.getLogger("auth")


def require_owner(request: Request) -> None:
    identity = getattr(request.state, "identity", None)
    path = request.scope.get("path", "/")
    key = policy.owner_violation(identity, request.method.upper(), path, request.path_params)
    if key is not None:
        logger.warning("auth denied: actor=%s role=%s %s %s reason=owner only key=%s", identity.user_id, identity.role_id,
                       request.method, path, key)
        raise HTTPException(status_code=403, detail="Forbidden")
