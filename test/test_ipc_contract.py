"""The control socket's contract (src/ipc/contract.py): messages and framing.

Pure data, so every test here runs on every platform. What they pin:

* a request and a response survive encode -> decode -> parse unchanged, and
  the on-demand arguments carry exactly what the file mailbox carries;
* the envelope and the arguments refuse what the display could not act on
  (missing ids, wrong types, a non-finite duration) with a stable error code;
* framing never holds more than one message's worth of bytes, however the
  bytes arrive;
* where the socket is looked for, and how it is switched off.
"""

import json
import math

import pytest

from src.ipc import contract as c
from src.ipc.contract import (
    Command, ErrorCode, FrameReader, OnDemandStartArgs, OnDemandStopArgs,
    ProtocolError, Request, Response,
)


def _wire(obj):
    """Encode then decode, as one side's bytes reach the other."""
    data = c.encode_message(obj)
    assert data.endswith(b'\n') and data.count(b'\n') == 1
    return c.decode_message(data)


class TestRoundTrip:
    def test_request(self):
        req = Request(id='abc-1', cmd=Command.ON_DEMAND_START,
                      args={'plugin_id': 'clock', 'mode': None, 'duration': 30.0,
                            'pinned': True})
        back = Request.from_dict(_wire(req.to_dict()))
        assert back == req
        assert back.v == c.PROTOCOL_VERSION

    def test_success_response(self):
        resp = Response.success('abc-1', {'accepted': True, 'request_id': 'abc-1', 'queued': 1})
        back = Response.from_dict(_wire(resp.to_dict()))
        assert back == resp
        assert back.ok and back.error is None

    def test_failure_response(self):
        resp = Response.failure('abc-1', ErrorCode.BUSY, 'queue full')
        wire = _wire(resp.to_dict())
        assert wire == {'v': 1, 'id': 'abc-1', 'ok': False,
                        'error': {'code': 'busy', 'message': 'queue full'}}
        assert Response.from_dict(wire) == resp

    def test_failure_without_an_id(self):
        wire = _wire(Response.failure(None, ErrorCode.BAD_JSON, 'nope').to_dict())
        assert wire['id'] is None
        assert Response.from_dict(wire).id is None

    def test_start_args_round_trip(self):
        args = OnDemandStartArgs(plugin_id='clock', mode='clock_main', duration=45.0,
                                 pinned=True)
        assert OnDemandStartArgs.from_dict(_wire(args.to_dict())) == args

    def test_encoded_messages_are_ascii_single_lines(self):
        data = c.encode_message({'v': 1, 'id': 'x', 'cmd': 'ping',
                                 'args': {'text': 'line1\nline2 café'}})
        assert data.count(b'\n') == 1
        data.decode('ascii')
        assert c.decode_message(data)['args']['text'] == 'line1\nline2 café'


class TestEnvelopeValidation:
    @pytest.mark.parametrize('obj', [
        [], 'x', 1, None,
    ])
    def test_not_an_object(self, obj):
        with pytest.raises(ProtocolError) as e:
            Request.from_dict(obj)
        assert e.value.code == ErrorCode.BAD_REQUEST

    @pytest.mark.parametrize('bad_id', [None, '', 7, 'x' * (c.MAX_ID_LENGTH + 1), 'a\nb'])
    def test_bad_id(self, bad_id):
        with pytest.raises(ProtocolError) as e:
            Request.from_dict({'v': 1, 'id': bad_id, 'cmd': 'ping'})
        assert e.value.code == ErrorCode.BAD_REQUEST
        assert e.value.request_id is None

    @pytest.mark.parametrize('v', [None, '1', 1.0, True])
    def test_bad_version_type_keeps_the_id(self, v):
        with pytest.raises(ProtocolError) as e:
            Request.from_dict({'v': v, 'id': 'r1', 'cmd': 'ping'})
        assert e.value.code == ErrorCode.BAD_REQUEST
        assert e.value.request_id == 'r1'

    def test_missing_cmd(self):
        with pytest.raises(ProtocolError) as e:
            Request.from_dict({'v': 1, 'id': 'r1'})
        assert e.value.code == ErrorCode.BAD_REQUEST

    def test_args_default_to_empty(self):
        assert Request.from_dict({'v': 1, 'id': 'r', 'cmd': 'ping'}).args == {}
        assert Request.from_dict({'v': 1, 'id': 'r', 'cmd': 'ping', 'args': None}).args == {}

    def test_args_must_be_an_object(self):
        with pytest.raises(ProtocolError) as e:
            Request.from_dict({'v': 1, 'id': 'r', 'cmd': 'ping', 'args': [1]})
        assert e.value.code == ErrorCode.BAD_REQUEST

    def test_an_unknown_version_parses(self):
        # The server, not the parser, decides about versions, so that hello
        # can negotiate.
        assert Request.from_dict({'v': 99, 'id': 'r', 'cmd': 'hello'}).v == 99

    @pytest.mark.parametrize('obj', [
        {'v': 1, 'id': 'r', 'ok': 'yes'},
        {'v': 1, 'id': 'r', 'ok': False},
        {'v': 1, 'id': 'r', 'ok': False, 'error': {'message': 'x'}},
        {'v': 1, 'id': 5, 'ok': True},
        {'v': 1, 'id': 'r', 'ok': True, 'result': [1]},
        {'id': 'r', 'ok': True},
    ])
    def test_malformed_responses(self, obj):
        with pytest.raises(ProtocolError):
            Response.from_dict(obj)


class TestOnDemandArgs:
    def test_plugin_or_mode_is_required(self):
        with pytest.raises(ProtocolError) as e:
            OnDemandStartArgs.from_dict({'duration': 10})
        assert e.value.code == ErrorCode.INVALID_ARGS

    def test_mode_alone_is_enough(self):
        assert OnDemandStartArgs.from_dict({'mode': 'nfl_live'}).mode == 'nfl_live'

    @pytest.mark.parametrize('raw, seconds', [
        (None, None), ('', None), (0, None), (45, 45.0), (2.5, 2.5), ('30', 30.0),
    ])
    def test_duration(self, raw, seconds):
        assert OnDemandStartArgs.from_dict({'plugin_id': 'p', 'duration': raw}).duration == seconds

    @pytest.mark.parametrize('raw', [-1, 'soon', True, [5], math.inf, math.nan, 'inf'])
    def test_bad_duration(self, raw):
        with pytest.raises(ProtocolError) as e:
            OnDemandStartArgs.from_dict({'plugin_id': 'p', 'duration': raw})
        assert e.value.code == ErrorCode.INVALID_ARGS

    @pytest.mark.parametrize('pinned', ['true', 1, 'false'])
    def test_pinned_must_be_a_real_boolean(self, pinned):
        # The web route coerces "false" to False before it gets here; the
        # contract does not guess (bool("false") is True).
        with pytest.raises(ProtocolError):
            OnDemandStartArgs.from_dict({'plugin_id': 'p', 'pinned': pinned})

    @pytest.mark.parametrize('name', [5, 'x' * (c.MAX_NAME_LENGTH + 1), 'a\nb'])
    def test_bad_names(self, name):
        with pytest.raises(ProtocolError):
            OnDemandStartArgs.from_dict({'plugin_id': name})

    def test_unknown_command(self):
        with pytest.raises(ProtocolError) as e:
            c.parse_args('reboot', {})
        assert e.value.code == ErrorCode.UNKNOWN_COMMAND

    @pytest.mark.parametrize('cmd', c.COMMANDS)
    def test_every_command_has_an_argument_type(self, cmd):
        args = {'plugin_id': 'p'} if cmd == Command.ON_DEMAND_START else {}
        c.parse_args(cmd, args)

    def test_hello_versions(self):
        assert c.HelloArgs.from_dict({'versions': [1, 2], 'client': 'web'}).versions == (1, 2)
        for bad in ([], ['1'], 'x', [True]):
            with pytest.raises(ProtocolError):
                c.HelloArgs.from_dict({'versions': bad})

    def test_negotiation(self):
        assert c.negotiate_version((1,)) == 1
        assert c.negotiate_version((1, 7)) == 1
        assert c.negotiate_version((7,)) is None


class TestMailboxShape:
    """Socket commands are handed to the mailbox's own handler, so they must
    look exactly like what the web route writes to the mailbox."""

    def test_start(self):
        args = OnDemandStartArgs(plugin_id='clock', mode='clock_main', duration=60.0,
                                 pinned=True)
        payload = c.on_demand_request('rid', args, 123.0)
        assert payload == {'request_id': 'rid', 'action': 'start', 'plugin_id': 'clock',
                           'mode': 'clock_main', 'duration': 60.0, 'pinned': True,
                           'timestamp': 123.0, 'source': 'socket'}

    def test_stop(self):
        payload = c.on_demand_request('rid', OnDemandStopArgs(), 5.0)
        assert payload['action'] == 'stop' and payload['request_id'] == 'rid'


class TestFraming:
    def test_one_message_in_pieces(self):
        data = c.encode_message({'v': 1, 'id': 'a', 'cmd': 'ping'})
        reader = FrameReader()
        out = []
        for i in range(len(data)):
            out += reader.feed(data[i:i + 1])
        assert [json.loads(x) for x in out] == [{'v': 1, 'id': 'a', 'cmd': 'ping'}]
        assert reader.pending == 0

    def test_several_messages_in_one_chunk(self):
        data = b''.join(c.encode_message({'n': n}) for n in range(3))
        assert [json.loads(x)['n'] for x in FrameReader().feed(data)] == [0, 1, 2]

    def test_blank_lines_are_skipped(self):
        assert FrameReader().feed(b'\n\r\n  \n{"a":1}\n') == [b'{"a":1}']

    def test_a_line_over_the_limit_is_refused(self):
        reader = FrameReader(max_bytes=32)
        with pytest.raises(ProtocolError) as e:
            reader.feed(b'x' * 40 + b'\n')
        assert e.value.code == ErrorCode.MESSAGE_TOO_LARGE

    def test_a_line_that_never_ends_is_refused_at_the_limit(self):
        reader = FrameReader(max_bytes=32)
        reader.feed(b'x' * 31)
        with pytest.raises(ProtocolError) as e:
            reader.feed(b'x')
        assert e.value.code == ErrorCode.MESSAGE_TOO_LARGE

    def test_exactly_the_limit_is_allowed(self):
        reader = FrameReader(max_bytes=8)
        assert reader.feed(b'1234567\n') == [b'1234567']

    def test_encode_refuses_an_oversize_message(self):
        with pytest.raises(ProtocolError) as e:
            c.encode_message({'blob': 'x' * c.MAX_MESSAGE_BYTES})
        assert e.value.code == ErrorCode.MESSAGE_TOO_LARGE

    def test_encode_refuses_non_json(self):
        with pytest.raises(ProtocolError):
            c.encode_message({'x': math.nan})
        with pytest.raises(ProtocolError):
            c.encode_message({'x': object()})

    @pytest.mark.parametrize('line', [b'{', b'[1,2]', b'"x"', b'\xff\xfe', b'null'])
    def test_decode_garbage(self, line):
        with pytest.raises(ProtocolError) as e:
            c.decode_message(line)
        assert e.value.code == ErrorCode.BAD_JSON


class TestSocketLocation:
    def test_default(self):
        paths = c.client_socket_paths({})
        assert paths[0] == '/run/ledmatrix/control.sock'
        assert len(paths) == 2 and paths[1].endswith('control.sock')

    def test_configured_path_is_the_only_one_tried(self):
        assert c.client_socket_paths({c.SOCKET_PATH_ENV: '/x/y.sock'}) == ['/x/y.sock']

    @pytest.mark.parametrize('value', ['off', 'OFF', '0', 'false', 'disabled', ' none '])
    def test_switched_off(self, value):
        env = {c.SOCKET_PATH_ENV: value}
        assert c.socket_disabled(env)
        assert c.client_socket_paths(env) == []
        assert c.configured_socket_path(env) is None

    def test_dev_path_is_per_user(self):
        assert c.dev_socket_path(1000) != c.dev_socket_path(1001)
        assert 'ledmatrix-1000' in c.dev_socket_path(1000)

    def test_the_default_dir_is_the_heartbeats(self):
        # One RuntimeDirectory= serves both (#687).
        from src import display_watchdog
        assert c.DEFAULT_SOCKET_DIR == display_watchdog.HEARTBEAT_DIR
