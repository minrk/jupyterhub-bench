import asyncio
import json
import concurrent.futures
import logging
import os, sys
import random
import time
from functools import partial
import threading
from binascii import b2a_hex
from concurrent.futures import ProcessPoolExecutor
from subprocess import Popen
from tempfile import TemporaryDirectory

from jupyterhub import orm, roles
from jupyterhub.app import JupyterHub
from jupyterhub.spawner import Spawner
from jupyterhub.proxy import Proxy
from jupyterhub.utils import wait_for_http_server
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.ioloop import IOLoop
from traitlets.config import Config

logging.getLogger('alembic').parent = None


def init_db(db_url):
    # first-run initialize
    hub = JupyterHub(db_url=db_url, spawner_class=FakeSpawner, authenticator_class="null", log_level=logging.ERROR)
    asyncio.run(hub.initialize([]))

http_port = 12345

def add_users(db, nusers, nrunning):
    """Fill a jupyterhub database with users

    There will be nusers total, and nrunning will have a running server
    """
    running_indices = set(random.choices(range(nusers), k=nrunning))
    user_role = orm.Role.find(db, "user")
    for i in range(nusers):
        name = f"user-{i}"
        user = orm.User(name=name)
        db.add(user)
        user.roles.append(user_role)
        spawner = orm.Spawner(user=user, name='')
        db.add(spawner)
        if i in running_indices:
            # put them all on the proxy port so health checks hit proxy
            spawner.server = orm.Server(ip="127.0.0.1", port=http_port)
    db.commit()


class FakeProxy(Proxy):
    async def check_routes(self, users, service_map):
        return

class FakeSpawner(Spawner):
    """Empty spawner that does nothing"""

    _stopped = False

    async def start(self):
        pass

    async def poll(self):
        if self._stopped:
            return 0
        await asyncio.sleep(0)
        return None

    async def stop(self):
        self._stopped = True


admin_token = b2a_hex(os.urandom(16)).decode('ascii')

class TestHub(JupyterHub):
    start_event: threading.Event

    def start(self):
        self.start_event.set()
        return super().start()

class JupyterHubSuite:
    timeout = 120.0
    param_names = [
        "n_users",
        "n_running",
    ]

    params = [
        [10, 100, 200, 500, 1_000, 5_000],
        [0, 10, 100, 200, 500, 1_000],
    ]

    http_dummy = None
    hub_thread = None

    def get_config(self):
        cfg = Config()
        cfg.Proxy.should_start = False
        cfg.JupyterHub.db_url = self.db_url
        cfg.JupyterHub.log_level = logging.ERROR
        cfg.JupyterHub.authenticator_class = "null"
        cfg.JupyterHub.spawner_class = FakeSpawner
        cfg.JupyterHub.proxy_class = FakeProxy
        cfg.JupyterHub.cleanup_servers = False

        cfg.JupyterHub.services = [
            {
                'name': 'admin',
                'api_token': admin_token,
            }
        ]
        cfg.JupyterHub.load_roles = [{"name": "admin", "services": ["admin"]}]
        return cfg


    def setup(self, n_users, n_running):
        if n_running > n_users:
            raise NotImplementedError()
        # TODO: test with other databases
        db_file = f"jupyterhub-{n_users}-{n_running}.sqlite"
        try:
            os.remove(db_file)
        except FileNotFoundError:
            pass
        self.db_url = f'sqlite:///{db_file}'

        os.environ["CONFIGPROXY_AUTH_TOKEN"] = "proxy-token"
        init_db(self.db_url)
        with orm.new_session_factory(self.db_url)() as db:
            add_users(db, n_users, n_running)
        self.http_dummy = Popen([sys.executable, "-mhttp.server", "-b", "127.0.0.1", str(http_port)])
        asyncio.run(wait_for_http_server(f"http://127.0.0.1:{http_port}"))
        self.start_hub()

    def start_hub(self):
        self.hub_initialized = threading.Event()
        self.hub_thread = threading.Thread(target=partial(asyncio.run, self._hub_thread()))
        self.hub_thread.start()
        self.hub_initialized.wait(timeout=10)
        asyncio.run(wait_for_http_server(self.hub.hub.api_url))

    def stop_hub(self):
        if self.hub_thread and self.hub_thread.is_alive():
            f = concurrent.futures.Future()
            async def cleanup():
                try:
                    await self.hub.cleanup()
                    self.hub.http_server.stop()
                    self.hub_stop_future.set_result(None)
                except Exception as e:
                    f.set_exception(e)
                else:
                    f.set_result(None)
            self.hub_loop.call_soon_threadsafe(lambda: asyncio.ensure_future(cleanup()))
            f.result(timeout=10)
            self.hub_thread.join(timeout=10)

    async def _hub_thread(self):
        self.hub_stop_future = asyncio.Future()
        self.hub_loop = asyncio.get_running_loop()
        JupyterHub.clear_instance()
        self.hub = JupyterHub.instance(config=self.get_config())
        # disable signal override
        self.hub.init_signal = lambda: None
        await self.hub.initialize([])
        self.hub_initialized.set()
        await self.hub.start()
        # wait until stop_hub is called
        await self.hub_stop_future

    def teardown(self, n_users, n_running):
        if self.http_dummy is not None:
            with self.http_dummy:
                self.http_dummy.terminate()
        self.stop_hub()
