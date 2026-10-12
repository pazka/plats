import ipaddress
import warnings

import pyotp
import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

from fastapi.testclient import TestClient  # noqa: E402

from app import db, services  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.config import DEFAULT_TRUSTED_PROXIES, Settings  # noqa: E402
from app.main import create_app  # noqa: E402

ADMIN_PASSWORD = "correct horse battery staple"
TOTP_SECRET = pyotp.random_base32()
_PW_HASH = hash_password(ADMIN_PASSWORD)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        secret_key="test-secret-key",
        ip_salt="test-ip-salt",
        admin_password_hash=_PW_HASH,
        admin_totp_secret=TOTP_SECRET,
        cookie_secure=True,
        trusted_proxies=[ipaddress.ip_network(p) for p in DEFAULT_TRUSTED_PROXIES.split(",")],
        pow_difficulty=8,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def conn(app):
    c = db.connect(app.state.settings.db_path)
    yield c
    c.close()


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def make_client(app):
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def dishes(conn):
    titles = ["Quiche lorraine", "Cassoulet", "Croque-monsieur", "Ratatouille", "Gratin dauphinois",
              "Soupe à l'oignon", "Pot-au-feu", "Brie de Meaux"]
    return [services.create_dish(conn, title=t, description=f"{t} : un plat décrit assez longuement.",
                                 source="manual") for t in titles]


@pytest.fixture
def no_think(conn):
    db.set_setting(conn, "min_think_ms", "0")
    db.set_setting(conn, "rl_vote", "100/1s")


def totp_now():
    return pyotp.TOTP(TOTP_SECRET).now()
