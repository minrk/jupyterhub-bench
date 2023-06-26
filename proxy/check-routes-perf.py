from binascii import b2a_hex
from concurrent.futures import ProcessPoolExecutor
import json
import logging
import os
import random
from subprocess import Popen
import sys
import tempfile
import time

from tornado import gen
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.ioloop import IOLoop
from traitlets import Dict

import jupyterhub
from jupyterhub import orm
from jupyterhub.app import JupyterHub
from jupyterhub.auth import Authenticator
from jupyterhub.proxy import Proxy
from jupyterhub.spawner import LocalProcessSpawner


# silence alembic logging
logging.getLogger('alembic').parent = None


class FakePollSpawner(LocalProcessSpawner):
    @gen.coroutine
    def poll(self):
        yield gen.moment
        return None


admin_token = b2a_hex(os.urandom(16)).decode('ascii')


class FakeProxy(Proxy):
    routes = Dict()
    @gen.coroutine
    def get_all_routes(self):
        yield gen.moment
        return self.routes

    @gen.coroutine
    def add_route(self, routespec, target, data):
        self.routes[routespec] = {
            'routespec': routespec,
            'target': target,
            'data': data,
        }

    @gen.coroutine
    def delete_route(self, routespec):
        self.routes.pop(routespec, None)


class FakeJupyterHub(JupyterHub):

    authenticator_class = Authenticator
    spawner_class = FakePollSpawner
    # proxy_class = FakeProxy
    log_level = logging.ERROR

    services = [
        {
            'name': 'admin',
            'admin': True,
            'api_token': admin_token,
        }
    ]

    @gen.coroutine
    def proxy_check_routes(self):
        self.proxy_times = times = []
        for i in range(2):
            self.db.expire_all()
            tic = time.perf_counter()
            try:
                yield self.proxy.check_routes(self.users, self._service_map)
            except Exception as e:
                print("Failed", e)
                IOLoop.current().stop()
                raise
            times.append(time.perf_counter() - tic)
        loop = IOLoop.current()
        # yield self.cleanup()
        loop.add_callback(loop.stop)
        # loop.stop()

    @gen.coroutine
    def start(self):
        # print(f"{len(list(self.db))} ORM objects at startup", file=sys.stderr)
        yield super().start()
        self.start_toc = time.perf_counter()
        IOLoop.current().add_callback(self.proxy_check_routes)

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
        return hub.start_toc - tic, hub.proxy_times


def main():
    import atexit
    os.environ['CONFIGPROXY_AUTH_TOKEN'] = 'proxy-token'
    p = Popen(['configurable-http-proxy', '--log-level=error',
        '--default-target', 'http://127.0.0.1:8081'])
    atexit.register(lambda *args: p.terminate())
    time.sleep(1)
    print(jupyterhub.__version__, file=sys.stderr)

    print("users,active,running,startup,first_check,second_check")
    for n in [10, 50, 100, 500, 1000, 2000, 5000]:
        for frac in [0, 0.25, 1]:
            r = int(frac * n)
            pool = ProcessPoolExecutor(1)
            f = pool.submit(run_test, n, r)
            # f.result()
            total, checks = f.result()
            print(",".join(map(str, [n, frac, r, total] + checks)))


if __name__ == '__main__':
    main()
