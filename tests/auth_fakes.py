"""Shared helpers for the authentication tests (no database, no heavy imports).

`FakeDirectory` stands in for the three database lookups of core_functions/auth_dependency.py (user state, owner of an
id, manager permissions) and counts every call, so tests can prove the in-process cache.
`make_app()` is a small FastAPI app that installs the real global guard and mounts a few dummy routes that echo what the
handler sees.
"""
import copy
import sys
from pathlib import Path

from fastapi import FastAPI, Form, Request, WebSocket
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_functions import auth_dependency as ad  # noqa: E402
from core_functions.auth_dependency import UserState  # noqa: E402
from utils import auth_token as T  # noqa: E402

KEY = "t" * 48


class FakeDirectory:
    """users: {user_id: dict(role, org, status=1, epoch=0, orgs=None)}; permissions: {user_id: {names}}."""

    def __init__(self, users=None, permissions=None, orgs_of_org_ids=()):
        self.users = copy.deepcopy(dict(users or {}))
        self.permissions = dict(permissions or {})
        self.org_ids = set(orgs_of_org_ids)        # organisation ids that exist (for ids that are org ids)
        self.calls = {"user_state": 0, "entity_org": 0, "permissions": 0}
        self.fail = False

    def _maybe_fail(self, name):
        self.calls[name] += 1
        if self.fail:
            raise RuntimeError("database down")

    def user_state(self, user_id):
        self._maybe_fail("user_state")
        u = self.users.get(user_id)
        if u is None:
            return None
        orgs = u.get("orgs")
        if orgs is None:
            orgs = {u["org"]} if u.get("org") else set()
        return UserState(u.get("epoch", 0), u.get("status", 1), u["role"], u.get("org") or "", frozenset(orgs))

    def entity_org(self, entity_id):
        self._maybe_fail("entity_org")
        u = self.users.get(entity_id)
        if u is not None:
            return u.get("org") or ""
        return entity_id if entity_id in self.org_ids else None

    def perms(self, user_id, org_id):
        self._maybe_fail("permissions")
        return frozenset(self.permissions.get(user_id, ()))

    def install(self):
        ad.reset_state()
        ad.set_loaders(user_state=self.user_state, entity_org=self.entity_org, permissions=self.perms)
        return self


def token_for(user_id, org, role, epoch=0, **kw):
    return T.issue_token(user_id, org, role, epoch, key=KEY, **kw)[0]


def auth(user_id, org, role, epoch=0):
    return {"Authorization": "Bearer " + token_for(user_id, org, role, epoch)}


class Body(BaseModel):
    org_id: str = None
    user_id: str = None
    staff_id: str = None
    note: str = None


def make_app() -> FastAPI:
    app = FastAPI()
    ad.install(app)

    def ident(request: Request):
        i = getattr(request.state, "identity", None)
        return None if i is None else {"user": i.user_id, "org": i.org_id, "role": i.role_id}

    @app.get("/health")
    def health(request: Request):
        return {"ok": True, "identity": ident(request)}

    @app.get("/auth/config")
    def config(request: Request):
        return {"ok": True, "identity": ident(request)}

    @app.get("/me")
    def me(request: Request):
        return {"identity": ident(request)}

    @app.get("/home/dashboard-status")
    def dashboard(request: Request, org_id: str = None):
        return {"org_id": org_id, "identity": ident(request)}

    @app.get("/addstaff/{user_id}")
    def staff_get(user_id: str, request: Request):
        return {"user_id": user_id, "identity": ident(request)}

    @app.get("/api/leads/employee/{org_id}/{user_id}")
    def leads_employee(org_id: str, user_id: str):
        return {"org_id": org_id, "user_id": user_id}

    @app.post("/api/leads/create")
    def leads_create(body: Body):
        return {"body": body.model_dump()}

    @app.post("/raw/json")
    async def raw_json(request: Request):
        # reads the body itself AFTER the guard: it must still be readable
        data = await request.json()
        again = await request.body()
        return {"json": data, "bytes": len(again)}

    @app.post("/raw/stream")
    async def raw_stream(request: Request):
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
        return {"bytes": total}

    @app.post("/support/submit")
    async def support_submit(user_id: str = Form(...), org_id: str = Form(...), message: str = Form(...)):
        return {"user_id": user_id, "org_id": org_id, "message": message}

    @app.post("/orders/create")
    def orders_create():
        return {"ok": True}

    @app.post("/branch/add")
    def branch_add(body: Body):
        return {"ok": True}

    @app.get("/gen-dashboard/stats")
    def gen_stats():
        return {"ok": True}

    @app.post("/requests/device-change/submit")
    def device_change(body: Body, request: Request):
        return {"ok": True, "identity": ident(request)}

    @app.post("/requests/update-status")
    def update_status(body: dict):
        return {"ok": True}

    @app.websocket("/ws/chat/{user_id}")
    async def ws_chat(websocket: WebSocket, user_id: str):
        if not await ad.authenticate_websocket(websocket, user_id):
            return
        await websocket.accept()
        await websocket.send_text("hello " + user_id)
        await websocket.close()

    @app.get("/k/org/{org_id}")
    def k_org(org_id: str):
        return {"ok": True}

    @app.get("/k/user/{user_id}")
    def k_user(user_id: str):
        return {"ok": True}

    @app.get("/k/staff/{staff_id}")
    def k_staff(staff_id: str):
        return {"ok": True}

    @app.get("/k/employee/{employee_id}")
    def k_employee(employee_id: str):
        return {"ok": True}

    @app.get("/chat/messages/{user_id}/{other_user_id}")
    def chat_messages(user_id: str, other_user_id: str):
        return {"ok": True}

    return app
