import asyncio
import json
from .utils import JupyterHubSuite, admin_token
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.httputil import url_concat

async def get_users(hub, **params):
    client = AsyncHTTPClient()
    # make an initial request to ensure the Hub is warmed up
    url = url_concat(hub.api_url + "/users", params)
    headers = {
        "Authorization": f"token {admin_token}",
        "Accept": "application/jupyterhub-pagination+json",
    }
    req = HTTPRequest(url, headers=headers)
    resp = await client.fetch(req)
    return json.loads(resp.body.decode("utf8"))

class GetUsersSuite(JupyterHubSuite):

    def time_get_all_users(self, n_users, n_running):
        model = asyncio.run(get_users(self.hub.hub))
        while model["_pagination"]["next"]:
            model = asyncio.run(get_users(self.hub.hub, offset=model["_pagination"]["offset"]))

    def time_get_all_users_first_page(self, n_users, n_running):
        asyncio.run(get_users(self.hub.hub))
