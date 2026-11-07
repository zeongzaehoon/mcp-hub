import os
import uuid

import httpx
import pytest

FRONTEND = os.environ.get("E2E_FRONTEND", "http://frontend:5173")
# what the browser bundle calls (VITE_API_BASE) -> where the backend really is from this container
API_PUBLIC = os.environ.get("E2E_API_PUBLIC", "http://localhost:18090")
API_INTERNAL = os.environ.get("E2E_API_INTERNAL", "http://backend:8080")
MASTER = (os.environ.get("MASTER_EMAIL", "master@example.com"), os.environ.get("MASTER_PASSWORD", "master-dev-password-123"))


@pytest.fixture(scope="session")
def api():
    with httpx.Client(base_url=f"{API_INTERNAL}/api/v1", timeout=10) as c:
        tok = c.post("/auth/login", json={"email": MASTER[0], "password": MASTER[1]}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        yield c


@pytest.fixture(scope="session")
def pg_server(api):
    items = api.get("/servers", params={"server_type": "postgres"}).json()["items"]
    return items[0] if items else api.post("/servers", json={"server_type": "postgres", "name": "mcp-postgres", "endpoint": "http://mcp-postgres:9001/mcp"}).json()


@pytest.fixture
def uid():
    return uuid.uuid4().hex[:6]


@pytest.fixture
def app(page):
    """Page with API calls rerouted to the backend container and the master logged in."""
    page.route(f"{API_PUBLIC}/**", lambda route: route.continue_(url=route.request.url.replace(API_PUBLIC, API_INTERNAL, 1)))
    page.goto(f"{FRONTEND}/login")
    page.fill("#login-email", MASTER[0])
    page.fill("#login-password", MASTER[1])
    page.click("button[type=submit]")
    page.wait_for_url("**/companies", timeout=15000)
    return page
