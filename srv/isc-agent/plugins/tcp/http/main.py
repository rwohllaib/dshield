import logging
from http import HTTPStatus
from twisted.web import server, resource
from twisted.internet import reactor, endpoints
from twisted.web.http import Request

PRODSTRING = 'Apache/3.2.3'
logger = logging.getLogger(__name__)


class HealthCheck(resource.Resource):
    isLeaf = True
    numberRequests = 0

    def render_GET(self, request: Request):
        self.numberRequests += 1
        request.setHeader(b"content-type", b"text/plain")
        content = f"I am request #{self.numberRequests}\n"
        return content.encode("ascii")

    def render_HEAD(self, request: Request):
        request.setResponseCode(HTTPStatus.OK)
        request.setHeader('Server', PRODSTRING)
        request.setHeader('Access-Control-Allow-Origin', '*')
        request.setHeader('content-type', 'text/plain')
        logger.info("Request type is %s", request.method)
        request.finish()


def handler():
    endpoints.serverFromString(reactor, "tcp:8000").listen(server.Site(HealthCheck()))
    return reactor.run()
