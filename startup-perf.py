from binascii import b2a_hex
from concurrent.futures import ProcessPoolExecutor
import json
import logging
import os
import random
from subprocess import Popen
import tempfile
import time

from tornado import gen
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.ioloop import IOLoop

from jupyterhub import orm
from jupyterhub.app import JupyterHub
from jupyterhub.auth import Authenticator
from jupyterhub.spawner import LocalProcessSpawner


# silence alembic logging
logging.getLogger('alembic').parent = None


class FakePollSpawner(LocalProcessSpawner):
    @gen.coroutine
    def poll(self):
        yield gen.moment
        return None


admin_token = b2a_hex(os.urandom(16)).decode('ascii')


class FakeJupyterHub(JupyterHub):

    authenticator_class = Authenticator
    spawner_class = FakePollSpawner
    log_level = logging.ERROR

    services = [
        {
            'name': 'admin',
            'admin': True,
            'api_token': admin_token,
        }
    ]

    @gen.coroutine
    def get_users(self):
        client = AsyncHTTPClient()
        req = HTTPRequest('http://127.0.0.1:8081/hub/api/users',
            headers={'Authorization': f"token {admin_token}"})
        # make an initial request to ensure the Hub is warmed up
        yield client.fetch('http://127.0.0.1:8081/hub/api')
        self.users_times = times = []
        for i in range(2):
            tic = time.perf_counter()
            try:
                resp = yield client.fetch(req)
            except Exception as e:
                print("Failed", e)
                IOLoop.current().stop()
                raise
            times.append(time.perf_counter() - tic)
            users = json.loads(resp.body.decode('utf8'))
        loop = IOLoop.current()
        # yield self.cleanup()
        loop.add_callback(loop.stop)
        # loop.stop()

    def start(self):
        self.start_toc = time.perf_counter()
        IOLoop.current().call_later(1, lambda : self.get_users())
        return super().start()

    @gen.coroutine
    def init_spawners(self):
        tic = time.perf_counter()
        yield super().init_spawners()
        self.spawn_time = time.perf_counter() - tic


def add_users(db, nusers, nrunning):
    """Fill a jupyterhub database with users

    There will be nusers total, and nrunning will have a running server
    """
    running_indices = set(random.choices(range(nusers), k=nrunning))
    user = orm.User(name='admin', admin=True)
    db.add(user)
    db.commit()
    admin_token = user.new_api_token()

    for i in range(nusers-1):
        name = f"user-{i}"
        user = orm.User(name=name)
        db.add(user)
        spawner = orm.Spawner(user=user, name='')
        db.add(spawner)
        if i in running_indices:
            spawner.server = orm.Server(port=i)
    db.commit()
    return admin_token


def run_test(nusers, nrunning):
    # import signal
    # signal.signal(signal.SIGINT, signal.SIG_IGN)
    # fixes for 0.8.1's AsyncIOMainLoop installation,
    # which isn't fork-safe
    # we have to tear everything down and create a new eventloop
    if hasattr(IOLoop, 'clear_instance'):
        IOLoop.clear_instance()
    IOLoop.clear_current()
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    # from tornado.platform.asyncio import AsyncIOMainLoop
    # AsyncIOMainLoop().install()

    with tempfile.TemporaryDirectory() as td:
        db_file = os.path.join(td, 'jupyterhub.sqlite')
        db_url = f"sqlite:///{db_file}"""
        FakeJupyterHub.db_url = db_url
        db = orm.new_session_factory(db_url)()
        add_users(db, nusers, nrunning)
        tic = time.perf_counter()
        FakeJupyterHub.launch_instance(['--Proxy.should_start=False'])
        hub = FakeJupyterHub.instance()
        return hub.start_toc - tic, hub.spawn_time, hub.users_times


def main():
    import atexit
    os.environ['CONFIGPROXY_AUTH_TOKEN'] = 'proxy-token'
    p = Popen(['configurable-http-proxy', '--log-level=error',
        '--default-target', 'http://127.0.0.1:8081'])
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
