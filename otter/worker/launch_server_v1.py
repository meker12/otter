from itertools import chain
import time
import json

from urllib import quote

from twisted.internet.defer import Deferred, gatherResults
from twisted.internet.task import LoopingCall

import treq

class APIError(Exception):
    """
    An error raised when a non-success response is returned by the API.

    :param int code: HTTP Response code for this error.
    :param str body: HTTP Response body for this error or None.
    """
    def __init__(self, code, body):
        Exception.__init__(
            self,
            'API Error code={0!r}, body={1!r}'.format(code, body))

        self.code = code
        self.body = body


def check_success(response, success_codes):
    """
    Convert an HTTP response to an appropriate APIError if
    the response code does not match an expected success code.

    This is intended to be used as a callback for a deferred that fires with
    an IResponse provider.

    :param IResponse response: The response to check.
    :param list success_codes: A list of int HTTP response codes that indicate
        "success".

    :return: response or a deferred that errbacks with an APIError.
    """
    def _raise_api_error(body):
        raise APIError(response.code, body)

    if response.code not in success_codes:
        return treq.content(response).addCallback(_raise_api_error)

    return response


def append_segments(uri, *segments):
    """
    Append segments to URI in a reasonable way.

    :param str uri: base URI with or without a trailing /.
    :type segments: str or unicode
    :param segments: One or more segments to append to the base URI.

    :return: complete URI as str.
    """
    def _segments(segments):
        for s in segments:
            if isinstance(s, unicode):
                s = s.encode('utf-8')

            yield quote(s)

    uri = '/'.join(chain([uri.rstrip('/')], _segments(segments)))
    print uri
    return uri


def auth_headers(auth_token):
    """
    Generate an appropriate set of headers given an auth_token.

    :param str auth_token: The auth_token.
    :return: A dict of common headers.
    """
    return {'content-type': ['application/json'],
            'accept': ['application/json'],
            'x-auth-token': [auth_token]}


def server_details(server_endpoint, auth_token, server_id):
    """
    Fetch the details of a server as specified by id.

    :param str server_endpoint: A str base URI probably from the service
        catalog.

    :param str auth_token: The auth token.
    :param str server_id: The opaque ID of a server.

    :return: A dict of the server details.
    """
    d = treq.get(append_segments(server_endpoint, 'servers', server_id),
                 auth_headers=auth_headers(auth_token))
    d.addCallback(check_success, [200, 203])
    return d.addCallback(treq.json_content)


def wait_for_status(server_endpoint,
                    auth_token,
                    server_id,
                    expected_status,
                    interval=5):
    """
    Wait until the server specified by server_id's status is expected_status.

    @TODO: Timeouts
    @TODO: Errback on error statuses.

    :param str server_endpoint: Server endpoint URI.
    :param str auth_token: Keystone Auth token.
    :param str server_id: Opaque nova server id.
    :param str expected_status: Nova status string.
    :param int interval: Polling interval.  Default: 5.

    :return: Deferred that fires when the expected status has been seen.
    """

    d = Deferred()

    def poll():
        def _check_status(server):
            if server['server']['status'] == expected_status:
                d.callback(server)

        sd = server_details(server_endpoint, auth_token, server_id)
        sd.addCallback(_check_status)

        return sd

    lc = LoopingCall(poll)

    def _stop(r):
        lc.stop()
        return r

    d.addCallback(_stop)

    return lc.start(interval).addCallback(lambda _: d)


def create_server(server_endpoint, auth_token, scaling_group, server_config):
    """
    Create a new server.

    :param str server_endpoint: Server endpoint URI.
    :param str auth_token: Keystone Auth Token.
    :param str scaling_group: Scaling group ID.
    :param dict server_config: Nova server config.

    :return: Deferred that fires with the CreateServer response as a dict.
    """
    d = treq.post(append_segments(server_endpoint, 'servers'),
                  auth_headers=auth_headers(auth_token),
                  data=json.dumps({'server': server_config}))
    d.addCallback(check_success, [202])
    return d.addCallback(treq.json_content)


def add_to_load_balancer(endpoint, auth_token, lb_config, ip_address):
    """
    Add an IP addressed to a load balancer based on the lb_config.

    :param str endpoint: Load balancer endpoint URI.
    :param str auth_token: Keystone Auth Token.
    :param str lb_config: An lb_config dictionary.
    :param str ip_address: The IP Address of the node to add to the load
        balancer.

    :return: Deferred that fires with the Add Node to load balancer response
        as a dict.
    """
    lb_id = lb_config['loadBalancerId']
    port = lb_config['port']
    path = append_segments(endpoint, 'loadbalancers', str(lb_id), 'nodes')

    d = treq.post(path,
                  auth_headers=auth_headers(auth_token),
                  data=json.dumps({
                    "nodes": [
                        {"address": ip_address,
                         "port": port,
                         "condition": "ENABLED",
                         "type": "PRIMARY"}]}))
    d.addCallback(check_success, [200, 202])
    return d.addCallback(treq.json_content)


def add_to_load_balancers(endpoint, auth_token, lb_configs, ip_address):
    """
    Add the specified IP to mulitple load balancer based on the configs in
    lb_configs.

    :param str endpoint: Load balancer endpoint URI.
    :param str auth_token: Keystone Auth Token.
    :param list lb_configs: List of lb_config dictionaries.
    :param str ip_address: IP address of the node to add to the load balancer.

    :return: Deferred that fires with the Add Node load balancer response
        for each lb_config in lb_configs or errbacks on the first error.
    """
    return gatherResults([
        add_to_load_balancer(endpoint, auth_token, lb_config, ip_address)
        for lb_config in lb_configs
    ], consumeErrors=True)


def endpoints(service_catalog, service_name=None, service_type=None, region=None):
    """
    Search a service catalog for matching endpoints.

    :param list service_catalog: List of services.
    :param str service_name: Name of service.  Example: 'cloudServersOpenStack'
    :param str service_type: Type of service. Example: 'compute'
    :param str region: Region of service.  Example: 'ORD'

    :return: Iterable of endpoints.
    """
    for service in service_catalog:
        if service_type and service_type != service['type']:
            continue

        if service_name and service_name != service['name']:
            continue

        for endpoint in service['endpoints']:
            if region and endpoint['region'] != region:
                continue

            yield endpoint


def launch_server(region, service_catalog, auth_token, launch_config):
    """
    Launch a new server given the launch config auth tokens and service catalog.
    Possibly adding the newly launched server to a load balancer.

    :param str region: A rackspace region as found in the service catalog.
    :param list service_catalog: A list of services as returned by the auth apis.
    :param str auth_token: The user's auth token.
    :param dict launch_config: A launch_config args structure as defined for
        the launch_server_v1 type.

    :return: Deferred that fires when the server is "launched" based on the
        given configuration.
    """

    lb_config = launch_config.get('loadBalancers', [])

    server_config = launch_config['server']

    lb_endpoint = list(endpoints(
        service_catalog, service_name='cloudLoadBalancers', region=region))[0]['publicURL']

    server_endpoint = list(endpoints(
        service_catalog, service_name='cloudServersOpenStack', region=region))[0]['publicURL']

    d = create_server(server_endpoint, auth_token, None, server_config)
    d.addCallback(lambda server: wait_for_status(server_endpoint, auth_token,
                                            server['server']['id'], 'ACTIVE'))
    def _add_lb(server):
        ip_address = filter(
            lambda x: x['version'] == 4,
            server['server']['addresses']['private'])[0]['addr']

        return add_to_load_balancers(lb_endpoint, auth_token, lb_config, ip_address)

    d.addCallback(_add_lb)
    return d


if __name__ == '__main__':
    import sys

    from twisted.internet.defer import inlineCallbacks
    from twisted.internet.task import react


    @inlineCallbacks
    def main(reactor, *argv):
        service_catalog = [
            {'name': 'cloudLoadBalancers', 'endpoints': [
                {'region': 'ORD', 'publicURL': 'https://ord.loadbalancers.api.rackspacecloud.com/v1.0/416511'}
            ]},
            {'name': 'cloudServersOpenStack', 'endpoints': [
                {'region': 'ORD', 'publicURL': 'https://ord.servers.api.rackspacecloud.com/v2/416511'}
            ]}
        ]

        auth_token = 'ad55f47f-ccf1-407a-8558-5d3a678106ac'

        manual_launch_config = {
            'server': {'name': 'test-server-manual',
                       'imageRef': '3afe97b2-26dc-49c5-a2cc-a2fc8d80c001',
                       'flavorRef': '3',
                       'OS-DCF:diskConfig': 'MANUAL'},
            'loadBalancers': [
                {'loadBalancerId': 96815,
                 'port': 8080}
            ]
        }

        auto_launch_config = {
            'server': {'name': 'test-server-auto',
                       'imageRef': '3afe97b2-26dc-49c5-a2cc-a2fc8d80c001',
                       'flavorRef': '3',
                       'OS-DCF:diskConfig': 'AUTO'},
            'loadBalancers': [
                {'loadBalancerId': 96815,
                 'port': 8080}
            ]
        }


        def print_duration(r, name, start):
            print name, 'duration:', time.time() - start
            return r

        start = time.time()
        d = launch_server('ORD', service_catalog, auth_token, manual_launch_config)
        d.addCallback(print_duration, 'manual', start)

        start = time.time()
        d2 = launch_server('ORD', service_catalog, auth_token, auto_launch_config)
        d2.addCallback(print_duration, 'auto', start)
        try:
            yield gatherResults([d, d2], consumeErrors=True)
        except Exception as e:
            print e.subFailure

    react(main, sys.argv[1:])
