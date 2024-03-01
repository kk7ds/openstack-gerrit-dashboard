#!/usr/bin/env -S python3 -u

# osfinger - a tool to watch Zuul build consoles live from the terminal
#
# The point here is to provide a cross-platform tool that speaks the
# finger protocol enough to connect to zuul and stream the console.
# Unlike the regular finger, it handles dropped connections and tries to
# resume to the same point in the stream to avoid having to redisplay the
# progress each time.

import argparse
import asyncio
import logging
import sys
import unittest
from unittest import mock
import urllib.parse

LOG = logging.getLogger('osfinger')
BUFFER_LIMIT = 1024


class FingerProtocol(asyncio.Protocol):
    def __init__(self, build, end_future, position):
        self._build = build
        self._chars = 0
        self._end_future = end_future
        self._startpos = position
        self._buffer = b''
        super().__init__()

    def connection_made(self, transport):
        LOG.debug('Connected - sending build %s' % self._build)
        self.transport = transport
        transport.write(self._build.encode() + b'\r\n')

    def data_received(self, data):
        try:
            datastr = (self._buffer + data).decode()
            self._buffer = b''
        except UnicodeDecodeError:
            # This can happen if we get a chunk of data that ends in the middle
            # of a unicode character. Buffer this chunk (up to our limit) and
            # use later to hopefully recover.
            if len(self._buffer) < BUFFER_LIMIT:
                self._buffer += data
                LOG.debug('Buffering chunk to handle unicode')
            else:
                LOG.error('Failed to resolve decode error with buffer')
                self._buffer = b''
            return

        if datastr == 'Build not found' and not self._chars:
            # This is what tells us the build is done and we should stop
            # reconnecting. Set the condition to True (finished) and make sure
            # we don't overwrite it in our connection_lost() handler.
            LOG.info('Build not found or ended')
            self._end_future.set_result(True)
            self._end_future = None
            self.transport.close()
            return
        prevpos = self._chars
        self._chars += len(datastr)
        if self._chars < self._startpos:
            # Catching up to our previous position - discard
            LOG.debug('Skipping %i to position %s',
                      self._chars, self._startpos)
            return
        elif prevpos < self._startpos < self._chars:
            # This straddles the old threshold, grab anything new
            chunkpos = self._chars - self._startpos
            datastr = datastr[chunkpos - 1:]
            LOG.debug('Truncated %i initial bytes of partial message %i/%i',
                      chunkpos, self._startpos, self._chars)
        sys.stdout.write(datastr)

    def connection_lost(self, exc):
        if self._end_future:
            LOG.debug('Connection lost unexpectedly')
            self._end_future.set_result(False)

    @property
    def position(self):
        """The position (in characters) in the stream so far"""
        return self._chars


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('build', metavar='BUILD',
                        help='Build URL or UUID')
    parser.add_argument('--debug', action='store_true',
                        help='Enable verbose debug logging')
    args = parser.parse_args()
    if args.build.startswith('http'):
        url = urllib.parse.urlparse(args.build)
        path = url.path.split('/')
        build = path[path.index('stream') + 1]
        host = url.hostname
    else:
        build = args.build
        host = 'zuul.opendev.org'

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    loop = asyncio.get_running_loop()
    startpos = 0

    # Keep reconnecting until we get an obvious end-of-stream
    while True:
        end = loop.create_future()
        LOG.debug('Connecting to %s...', host)
        conn, proto = await loop.create_connection(
            lambda: FingerProtocol(build, end, startpos),
            host, 79)
        try:
            if await end:
                LOG.debug('Stream ended - no reconnect needed')
                break
        finally:
            conn.close()
            startpos = proto.position


class TestCase(unittest.TestCase):
    def setUp(self):
        pass
        # logging.basicConfig(level=logging.DEBUG)

    @mock.patch('sys.stdout.write')
    def test_resume_zero(self, mock_print):
        p = FingerProtocol('', None, 0)
        p.data_received(b'abc')
        p.data_received(b'def')
        mock_print.assert_has_calls([
            mock.call('abc'), mock.call('def'),
        ])

    @mock.patch('sys.stdout.write')
    def test_resume_nonzero(self, mock_print):
        p = FingerProtocol('', None, 4)
        p.data_received(b'abc')
        p.data_received(b'def')
        p.data_received(b'ghi')
        p.data_received(b'jkl')
        mock_print.assert_has_calls([
            mock.call('ef'), mock.call('ghi'), mock.call('jkl'),
        ])

    @mock.patch('sys.stdout.write')
    def test_resume_unicode(self, mock_print):
        p = FingerProtocol('', None, 0)
        data = b'\xf0\x9f\x92\xa9' * 2
        p.data_received(data[:2])
        p.data_received(data[2:])
        mock_print.assert_called_once_with(data.decode())


if __name__ == '__main__':
    asyncio.run(main())
