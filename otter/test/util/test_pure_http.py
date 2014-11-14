"""Tests for otter.util.pure_http"""

import json

from testtools import TestCase

from effect.testing import StubIntent, resolve_stubs
from effect.twisted import perform
from effect import Effect, ConstantIntent, FuncIntent

from twisted.trial.unittest import SynchronousTestCase

from otter.util.pure_http import (
    Request, request, check_status,
    effect_on_response,
    add_effectful_headers, add_headers, add_effect_on_response, add_bind_root,
    add_content_only, add_error_handling, add_json_response,
    add_json_request_data)
from otter.util.http import APIError
from otter.test.utils import stub_pure_response, StubResponse, StubTreq


Constant = lambda x: StubIntent(ConstantIntent(x))


def stub_request(response):
    """Create a request function that returns a stubbed response."""
    return lambda method, url, headers=None, data=None: Effect(Constant(response))


class RequestEffectTests(SynchronousTestCase):
    """
    Tests for the effects of pure_http.Request.
    """
    def test_perform(self):
        """
        The Request effect dispatches a request to treq, and returns a two-tuple
        of the Twisted Response object and the content as bytes.
        """
        req = ('GET', 'http://google.com/', None, None,  {'log': None})
        response = StubResponse(200, {})
        treq = StubTreq(reqs=[(req, response)],
                        contents=[(response, "content")])
        req = Request(method="get", url="http://google.com/")
        req.treq = treq
        self.assertEqual(
            self.successResultOf(perform(None, Effect(req))),
            (response, "content"))

    def test_log(self):
        """
        The log specified in the Request is passed on to the treq implementation.
        """
        log = object()
        req = ('GET', 'http://google.com/', None, None, {'log': log})
        response = StubResponse(200, {})
        treq = StubTreq(reqs=[(req, response)],
                        contents=[(response, "content")])
        req = Request(method="get", url="http://google.com/", log=log)
        req.treq = treq
        self.assertEqual(self.successResultOf(perform(None, Effect(req))),
                         (response, "content"))


class CheckStatusTests(TestCase):
    """Tests :func:`check_status`"""

    def test_check_status(self):
        """
        :func:`check_status` raises an APIError when HTTP codes don't match.
        """
        self.assertRaises(
            APIError,
            check_status,
            (200,),
            stub_pure_response({"foo": "bar"}, code=404))

    def test_check_status_success(self):
        """When the HTTP code matches, the response is returned."""
        response = stub_pure_response({"foo": "bar"}, code=404)
        result = check_status((404,),  response)
        self.assertEqual(result, response)

    def test_add_error_handling(self):
        """
        :func:`add_error_handling` invokes :func:`check_status` as a callback.
        """
        response = stub_pure_response("", code=404)
        eff = add_error_handling((200,), stub_request(response))('m', 'u')
        self.assertRaises(APIError, resolve_stubs, eff)


class AddEffectfulHeadersTest(TestCase):
    """
    Tests for :func:`add_effectful_headers`.
    """

    def setUp(self):
        """Save auth effect."""
        super(AddEffectfulHeadersTest, self).setUp()
        self.auth_effect = Effect(Constant({"x-auth-token": "abc123"}))

    def test_add_headers(self):
        """Headers from the provided effect are inserted."""
        request_ = add_effectful_headers(self.auth_effect, request)
        eff = request_('m', 'u', headers={'default': 'headers'})
        self.assertEqual(
            resolve_stubs(eff).intent,
            Request(method="m",
                    url="u",
                    headers={"x-auth-token": "abc123",
                             "default": "headers"}))

    def test_added_headers_win(self):
        """When merging headers together, headers from the effect win."""
        request_ = add_effectful_headers(self.auth_effect, request)
        eff = request_('m', 'u', headers={'x-auth-token': 'fooey'})
        self.assertEqual(
            resolve_stubs(eff).intent,
            Request(method="m",
                    url="u",
                    headers={"x-auth-token": "abc123"}))

    def test_add_headers_optional(self):
        """It's okay if no headers are passed."""
        request_ = add_effectful_headers(self.auth_effect, request)
        eff = request_('m', 'u')
        self.assertEqual(
            resolve_stubs(eff).intent,
            Request(method='m',
                    url='u',
                    headers={'x-auth-token': 'abc123'}))


class AddHeadersTest(TestCase):
    """Tests for :func:`add_headers`."""

    def test_add_headers(self):
        """Headers are merged, with fixed headers taking precedence."""
        request_ = add_headers({'one': '1', 'two': '2'}, request)
        eff = request_('m', 'u', headers={'one': 'hey', 'three': '3'})
        self.assertEqual(
            resolve_stubs(eff).intent,
            Request(method='m',
                    url='u',
                    headers={'one': '1', 'two': '2', 'three': '3'}))

    def test_add_headers_optional(self):
        """It's okay if no headers are passed."""
        request_ = add_headers({'one': '1'}, request)
        eff = request_('m', 'u')
        self.assertEqual(
            resolve_stubs(eff).intent,
            Request(method='m',
                    url='u',
                    headers={'one': '1'}))


class EffectOnResponseTests(TestCase):
    """Tests for :func:`effect_on_response`."""

    def setUp(self):
        """Set up an invalidation request ."""
        super(EffectOnResponseTests, self).setUp()
        self.invalidations = []
        invalidate = lambda: self.invalidations.append(True)
        self.invalidate_effect = Effect(StubIntent(FuncIntent(invalidate)))

    def test_invalidate(self):
        """
        :func:`effect_on_response` invokes the provided effect and
        returns an Effect of the original response.
        """
        badauth = stub_pure_response("badauth!", code=401)
        eff = effect_on_response((401,), self.invalidate_effect, badauth)
        self.assertEqual(eff.intent, self.invalidate_effect.intent)
        self.assertEqual(resolve_stubs(eff), badauth)
        self.assertEqual(self.invalidations, [True])

    def test_invalidate_unnecessary(self):
        """
        The result is returned immediately and the provided effect is not
        invoked when the HTTP response code is not in ``codes``.
        """
        good = stub_pure_response("okay!", code=200)
        result = effect_on_response((401,), self.invalidate_effect, good)
        self.assertEqual(result, good)
        self.assertEqual(self.invalidations, [])

    def test_add_effect_on_response(self):
        """Test the decorator :func:`add_effect_on_response`."""
        badauth = stub_pure_response("badauth!", code=401)
        request_ = add_effect_on_response(
            self.invalidate_effect, (401,), stub_request(badauth))
        eff = request_('m', 'u')
        self.assertEqual(resolve_stubs(eff), badauth)
        self.assertEqual(self.invalidations, [True])


class BindRootTests(TestCase):
    """Tests for :func:`add_bind_root`"""

    def test_bind_root(self):
        """
        :func:`add_bind_root` decorates a request function to append any
        passed URL paths onto the root URL.
        """
        request_ = add_bind_root("http://slashdot.org/", request)
        self.assertEqual(request_("get", "foo").intent.url,
                         "http://slashdot.org/foo")

    def test_bind_root_no_slashes(self):
        """
        Root URLs without a trailing slash will have one inserted
        automatically.
        """
        request_ = add_bind_root("http://slashdot.org", request)
        self.assertEqual(request_("get", "foo").intent.url,
                         "http://slashdot.org/foo")


class ContentOnlyTests(TestCase):
    """Tests for :func:`add_content_only`"""

    def test_add_content_only(self):
        """The produced request function results in the content."""
        request_ = add_content_only(stub_request(stub_pure_response('foo', 200)))
        eff = request_('m', 'u')
        self.assertEqual(resolve_stubs(eff), 'foo')


class AddJsonResponseTests(TestCase):
    """Tests for :func:`add_json_response`."""
    def test_add_json_response(self):
        """The produced request function results in a parsed data structure."""
        response = stub_pure_response('{"a": "b"}', 200)
        request_ = add_json_response(stub_request(response))
        self.assertEqual(resolve_stubs(request_('m', 'u')),
                         (response[0], {'a': 'b'}))


class AddJsonRequestDataTests(TestCase):
    """Tests for :func:`add_json_request_data`."""
    def test_add_json_request_data(self):
        """The produced request function serializes data to json."""
        eff = add_json_request_data(request)('m', 'u', data={'a': 'b'})
        self.assertEqual(eff.intent.data, json.dumps({'a': 'b'}))