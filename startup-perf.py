import asyncio
import json
import logging
import os
import random
import tempfile
import time
from binascii import b2a_hex
from concurrent.futures import ProcessPoolExecutor
from subprocess import Popen

from traitlets.config import Config
from jupyterhub import orm
from jupyterhub.app import JupyterHub
from jupyterhub.spawner import LocalProcessSpawner
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.httputil import url_concat
from tornado.ioloop import IOLoop
from sqlalchemy import event

# silence alembic logging
logging.getLogger('alembic').parent = None


class FakeSpawner(LocalProcessSpawner):
    """Empty spawner that does nothing"""

    _stopped = False

    async def poll(self):
        if self._stopped:
            return 0
        await asyncio.sleep(0)
        return None

    async def stop(self):
        self._stopped = True


admin_token = b2a_hex(os.urandom(16)).decode('ascii')


class FakeJupyterHub(JupyterHub):

    async def api_request(self, client, path, params=None):
        url = url_concat(self.hub.api_url + path, params or {})
        req = HTTPRequest(
            url,
            headers={
                "Authorization": f"token {admin_token}",
                "Accept": "application/jupyterhub-pagination+json",
            },
        )
        self.log.info("Fetching %s", url)
        resp = await client.fetch(req)
        return json.loads(resp.body.decode())

    async def get_users(self):
        client = AsyncHTTPClient()
        # make an initial request to ensure the Hub is warmed up
        await client.fetch(self.hub.api_url)
        self.users_times = times = []
        for i in range(2):
            tic = time.perf_counter()
            next = None
            user_info = await self.api_request(client, "/users")
            while user_info["_pagination"]["next"]:
                user_info = await self.api_request(
                    client,
                    "/users",
                    {"offset": user_info["_pagination"]["next"]["offset"]},
                )
            times.append(time.perf_counter() - tic)

    def init_db(self):
        result = super().init_db()
        engine = self.db.get_bind()

        @event.listens_for(engine, "before_execute")
        def before_execute(conn, clauseelement, multiparams, params, execution_options):
            # simulate 'slow' database performance by adding 1ms to each db execution
            time.sleep(1e-3)

        return result

    async def start(self):
        await super().start()
        self.start_toc = time.perf_counter()
        self.log.info("Awaiting init spawners")
        await self._init_spawners_done
        self.log.info("Awaiting get users")
        await self.get_users()
        self.log.info("done")

    async def init_spawners(self):
        self._init_spawners_done = asyncio.Future()
        tic = time.perf_counter()
        try:
            result = await super().init_spawners()
        except Exception as e:
            self._init_spawners_done.set_exception(e)
            raise
        else:
            self._init_spawners_done.set_result(None)
            self.spawn_time = time.perf_counter() - tic
            return result


c = config = Config()
c.Proxy.should_start = False
c.JupyterHub.log_level = logging.ERROR
c.JupyterHub.spawner_class = FakeSpawner
c.JupyterHub.authenticator_class = "null"
c.JupyterHub.cleanup_servers = False

# make sure these are explicit
# because this is what costs a lot
c.Authenticator.admin_users = {"admin"}
c.Authenticator.allowed_users = {"admin"}

c.JupyterHub.services = [
    {
        "name": "admin",
        "api_token": admin_token,
    }
]
c.JupyterHub.load_roles = [{"name": "admin", "services": ["admin"]}]


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
            # put them all proxy port so health checks hit proxy
            spawner.server = orm.Server(ip="127.0.0.1", port=8001)
    db.commit()


def run_test(nusers, nrunning):
    with tempfile.TemporaryDirectory() as td:
        db_file = os.path.join(td, 'jupyterhub.sqlite')
        db_url = f"sqlite:///{db_file}"""
        JupyterHub.clear_instance()
        # init db by initializing (but not launching) a Hub
        hub = JupyterHub.instance(
            db_url=db_url,
            config=config,
        )
        asyncio.run(hub.initialize([]))
        JupyterHub.clear_instance()
        db = orm.new_session_factory(db_url)()
        add_users(db, nusers, nrunning)
        FakeJupyterHub.clear_instance()
        tic = time.perf_counter()
        hub = FakeJupyterHub.instance(db_url=db_url, config=config)

        asyncio.run(hub.launch_instance_async([]))
        return hub.start_toc - tic, hub.spawn_time, hub.users_times


def main():
    import atexit

    os.environ["CONFIGPROXY_AUTH_TOKEN"] = "proxy-token"
    p = Popen(
        [
            "configurable-http-proxy",
            "--log-level=error",
            "--default-target",
            "http://127.0.0.1:8081",
            "--api-ip",
            "127.0.0.1",
        ]
    )
    atexit.register(lambda *args: p.terminate())
    time.sleep(1)

    print("users,active,running,startup,spawn,first_users,second_users")
    for n in [10, 50, 100, 500, 1000, 2000, 5000]:
        for frac in [0, 0.25, 1]:
            r = int(frac * n)
            pool = ProcessPoolExecutor(1)
            f = pool.submit(run_test, n, r)
            # f.result()
            total, spawn, users = f.result()
            print(",".join(map(str, [n, frac, r, total, spawn] + users)))


if __name__ == '__main__':
    main()
