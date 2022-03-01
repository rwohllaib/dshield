import datetime
import json
import logging
import os
import random
import re
from http import HTTPStatus
from typing import Dict, Optional

from jinja2 import Environment, BaseLoader
from twisted.web import server, resource
from twisted.internet import endpoints, reactor, ssl
from twisted.web.http import Request

import settings
from plugins.tcp.http.models import Response, Signature, prepare_database, RequestLog
from plugins.tcp.http.schemas import Condition

condition_translator = {
    Condition.absent: lambda x, y: x not in y,
    Condition.contain: lambda x, y: x in y,
    Condition.equal: lambda x, y: x == y,
    Condition.regex: re.match,
}
default_http_ports = [80, 8000, 8080]
default_https_ports = [443]
template_environment = Environment(loader=BaseLoader())
logger = logging.getLogger(__name__)


def extract_request_attributes(request: Request) -> Dict:
    return {
        'args': request.args,
        'client_ip': request.getClientIP(),
        'cookies': {k.decode(): v.decode() for k, v in request.received_cookies.items()},
        'headers': {k.decode(): v.decode() for k, v in request.getAllHeaders().items()},
        'method': request.method.decode(),
        'password': request.getPassword().decode(),
        'path': request.path.decode(),
        'target_ip': settings.LOCAL_IP,
        'user': request.getUser().decode(),
        'version': request.clientproto,
    }


def get_winning_signature(request_attributes: Dict) -> Optional[Signature]:
    top_score = 0
    winning_signature = None
    signatures = settings.DATABASE_SESSION.query(Signature).order_by(Signature.max_score.desc()).all()
    for signature in signatures:
        if top_score >= signature.max_score:
            break
        score = get_signature_score(signature.rules, request_attributes)
        if score >= top_score:
            top_score = score
            winning_signature = signature
    return winning_signature


def get_signature_score(rules, attributes):
    score = 0
    for rule in rules:
        if rule['attribute'] not in attributes or rule['condition'] not in condition_translator:
            continue

        if ":" in rule['value']:
            key, value = rule['value'].split(':')
            attribute = attributes[rule['attribute']][key]
        else:
            value = rule['value']
            attribute = attributes[rule['attribute']]

        condition_function = condition_translator[rule['condition']]
        if condition_function(value, attribute):
            score += rule['score']
        elif rule['required']:
            score = 0
            break
    return score


def log_request(request_attributes: Dict, signature_id: Optional[int] = None, response_id: Optional[int] = None):
    request_log = RequestLog(
        client_ip=request_attributes['client_ip'],
        data={'post_data': request_attributes['args']},
        headers=str(request_attributes['headers']),
        method=request_attributes['method'],
        path=request_attributes['path'],
        response_id=response_id,
        signature_id=signature_id,
        target_ip=request_attributes['target_ip'],
        version=request_attributes['version'],
    )
    settings.DATABASE_SESSION.add(request_log)
    settings.DATABASE_SESSION.commit()


class HTTP(resource.Resource):
    isLeaf = True

    def render(self, request: Request):
        request_attributes = extract_request_attributes(request)
        signature = get_winning_signature(request_attributes)

        if signature:
            response = settings.DATABASE_SESSION.query(Response).get(random.choice(signature.responses))  # nosec
            request.setResponseCode(response.status_code)

            template_variables = {
                **request_attributes,
                'datetime': datetime.datetime.now()
            }
            body = template_environment.from_string(response.body).render(template_variables)
            headers = json.loads(
                template_environment.from_string(
                    json.dumps(response.headers)
                ).render(template_variables)
            )
            request.write(body.encode())
            for name, value in headers.items():
                request.setHeader(name, value)
            log_request(request_attributes, signature.id, response.id)
        else:
            request.setResponseCode(HTTPStatus.BAD_REQUEST)
            request.write(HTTPStatus.BAD_REQUEST.description.encode())
            log_request(request_attributes)
        request.finish()
        return server.NOT_DONE_YET


def handler(**kwargs):
    prepare_database()
    http_ports = kwargs.get('http_ports', default_http_ports)
    https_ports = kwargs.get('https_ports', default_https_ports)
    for port in http_ports:
        endpoints.TCP4ServerEndpoint(reactor, port).listen(server.Site(HTTP()))
    if os.path.exists(settings.PRIVATE_KEY) and os.path.exists(settings.CERT_KEY):
        ssl_context = ssl.DefaultOpenSSLContextFactory(settings.PRIVATE_KEY, settings.CERT_KEY)
        for port in https_ports:
            endpoints.SSL4ServerEndpoint(reactor, port, ssl_context).listen(server.Site(HTTP()))
    else:
        logger.warning('Will not start https because cert or key file not found')