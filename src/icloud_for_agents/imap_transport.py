"""Local IMAPClient 3.0.1 compatibility for Python 3.14's file property.

Keep upstream TLS verification, socket timeouts and UID behavior unchanged.
No global monkeypatch; no network connection occurs during import.
"""
from imapclient import IMAPClient
from imapclient.tls import IMAP4_TLS


class CompatibleTLS(IMAP4_TLS):
    @property
    def file(self):
        return self._file

    @file.setter
    def file(self, stream):
        self._file = stream


class CompatibleIMAPClient(IMAPClient):
    def _create_IMAP4(self):
        if self.ssl and not self.stream:
            return CompatibleTLS(self.host, self.port, self.ssl_context,
                                 getattr(self._timeout, "connect", None))
        return super()._create_IMAP4()

    def _command_and_check(self, command, *args, **kwargs):
        if command not in {'copy', 'move'}:
            return super()._command_and_check(command, *args, **kwargs)
        # imaplib.response consumes stored response codes. Clear old COPYUID
        # BEFORE the command so a failed/missing new response cannot reuse it.
        self._imap.response('COPYUID')
        result = super()._command_and_check(command, *args, **kwargs)
        _, values = self._imap.response('COPYUID')
        from .core import SafetyError, text
        import re
        tagged = re.findall(r'COPYUID (\d+) ([\d:,]+) ([\d:,]+)', text(result), re.I)
        fresh = []
        for value in values:
            if value is None:
                continue
            match = re.fullmatch(r'(\d+) ([\d:,]+) ([\d:,]+)', text(value).strip())
            if match is None:
                raise SafetyError('Malformed fresh COPYUID; reconcile, do not retry mutation')
            fresh.append(match.groups())
        mappings = tagged + fresh
        if len(set(mappings)) > 1:
            raise SafetyError('Conflicting COPYUID responses; reconcile, do not retry mutation')
        if not mappings:
            return result  # core refuses missing identity; never retries the write.
        return ('[COPYUID ' + ' '.join(mappings[0]) + ']').encode('ascii')
