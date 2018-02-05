from concurrent.futures import ProcessPoolExecutor
import logging
import os
import random
import tempfile
import time

import numpy as np
from tornado import gen
from tornado.ioloop import IOLoop
from traitlets.config import Config

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


class FakeJupyterHub(JupyterHub):

    authenticator_class = Authenticator
    spawner_class = FakePollSpawner
    log_level = logging.ERROR

    @gen.coroutine
    def start(self):
        loop = IOLoop.current()
        loop.add_callback(loop.stop)

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
    for i in range(nusers):
        name = f"user-{i}"
        user = orm.User(name=name)
        db.add(user)
        spawner = orm.Spawner(user=user, name='')
        db.add(spawner)
        if i in running_indices:
            spawner.server = orm.Server(port=i)
    db.commit()


def run_test(nusers, nrunning):
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
        FakeJupyterHub.launch_instance([])
        toc = time.perf_counter()
        return toc - tic, FakeJupyterHub.instance().spawn_time


def main():
    print("users, active, running, startup, spawn")
    for n in [10, 50, 100, 500, 1000, 2000, 5000]:
        for frac in [0, 0.1, 0.25, 0.5, 1]:
            r = int(frac * n)
            pool = ProcessPoolExecutor(1)
            f = pool.submit(run_test, n, r)
            total, spawn = f.result()
            print(",".join(map(str, [n, frac, r, total, spawn])))


if __name__ == '__main__':
    main()
