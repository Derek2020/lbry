from random import choice
import logging

from twisted.internet import defer
import treq
from twisted.internet.task import LoopingCall

log = logging.getLogger(__name__)


class HTTPBlobDownloader(object):
    def __init__(self, blob_manager, blob_hashes=None, servers=None, client=None):
        self.blob_manager = blob_manager
        self.servers = servers or []
        self.client = client or treq
        self.blob_hashes = blob_hashes or []
        self.looping_call = LoopingCall(self._download_next_blob_hash_for_file)
        self.failures = 0
        self.max_failures = 3
        self.interval = 1

    @property
    def running(self):
        return self.looping_call.running

    def start(self):
        if not self.running and self.blob_hashes and self.servers:
            return self.looping_call.start(self.interval, now=True)
        defer.succeed(None)

    def stop(self):
        if self.running:
            self.blob_hashes = []
            return self.looping_call.stop()

    @defer.inlineCallbacks
    def _download_next_blob_hash_for_file(self):
        for blob_hash in self.blob_hashes:
            blob = yield self.blob_manager.get_blob(blob_hash)
            if not blob.verified:
                self.download_blob(blob)
                return
        self.stop()

    @defer.inlineCallbacks
    def download_blob(self, blob):
        try:
            yield self._download_blob(blob)
            self.failures = 0
        except Exception as exception:
            self.failures += 1
            log.error('Mirror failed downloading: %s', exception)
            if self.failures >= self.max_failures:
                self.stop()
                self.failures = 0

    @defer.inlineCallbacks
    def _download_blob(self, blob):
        if not blob.get_is_verified() and not blob.is_downloading() and 'mirror' not in blob.writers:
            response = yield self.client.get(url_for(choice(self.servers), blob.blob_hash))
            if response.code != 200:
                log.debug('[Mirror] Missing a blob: %s', blob.blob_hash)
                if blob.blob_hash in self.blob_hashes:
                    self.blob_hashes.remove(blob.blob_hash)
                defer.returnValue(blob.blob_hash)
            log.debug('[Mirror] Download started: %s', blob.blob_hash)
            blob.set_length(response.length)
            writer, finished_deferred = blob.open_for_writing('mirror')
            try:
                yield self.client.collect(response, writer.write)
                log.info('Mirror completed download for %s', blob.blob_hash)
            except Exception as e:
                writer.close(e)
            yield finished_deferred
            defer.returnValue(blob.blob_hash)


def url_for(server, blob_hash=''):
    return 'http://{}/{}'.format(server, blob_hash)