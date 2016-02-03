# encoding: utf-8
# encoding: utf-8
from __future__ import unicode_literals

import cgi
import codecs
import logging
import re
import sys
from io import BytesIO
from threading import Lock

from django import http
from django.conf import settings
from django.core import signals
from django.core.handlers import base
from django.urls import set_script_prefix
from django.utils import six
from django.utils.encoding import force_str, force_text
from django.utils.functional import cached_property

logger = logging.getLogger('django.request')

# encode() and decode() expect the charset to be a native string.
ISO_8859_1, UTF_8 = str('iso-8859-1'), str('utf-8')

_slashes_re = re.compile(br'/+')


class LimitedStream(object):
    '''
    LimitedStream wraps another stream in order to not allow reading from it
    past specified amount of bytes.
    '''
    def __init__(self, stream, limit, buf_size=64 * 1024 * 1024):
        self.stream = stream
        self.remaining = limit
        self.buffer = b''
        self.buf_size = buf_size

    def _read_limited(self, size=None):
        if size is None or size > self.remaining:
            size = self.remaining
        if size == 0:
            return b''
        result = self.stream.read(size)
        self.remaining -= len(result)
        return result

    def read(self, size=None):
        if size is None:
            result = self.buffer + self._read_limited()
            self.buffer = b''
        elif size < len(self.buffer):
            result = self.buffer[:size]
            self.buffer = self.buffer[size:]
        else:  # size >= len(self.buffer)
            result = self.buffer + self._read_limited(size - len(self.buffer))
            self.buffer = b''
        return result

    def readline(self, size=None):
        while b'\n' not in self.buffer and \
              (size is None or len(self.buffer) < size):
            if size:
                # since size is not None here, len(self.buffer) < size
                chunk = self._read_limited(size - len(self.buffer))
            else:
                chunk = self._read_limited()
            if not chunk:
                break
            self.buffer += chunk
        sio = BytesIO(self.buffer)
        if size:
            line = sio.readline(size)
        else:
            line = sio.readline()
        self.buffer = sio.read()
        return line


class WSGIRequest(http.HttpRequest):
    """
        init实现了wsgi把environ的数据填进 request对象
    """
    def __init__(self, environ):
        script_name = get_script_name(environ)
        path_info = get_path_info(environ)
        if not path_info:
            # Sometimes PATH_INFO exists, but is empty (e.g. accessing
            # the SCRIPT_NAME URL without a trailing slash). We really need to
            # operate as if they'd requested '/'. Not amazingly nice to force
            # the path like this, but should be harmless.
            path_info = '/'
        self.environ = environ
        self.path_info = path_info
        # be careful to only replace the first slash in the path because of
        # http://test/something and http://test//something being different as
        # stated in http://www.ietf.org/rfc/rfc2396.txt
        self.path = '%s/%s' % (script_name.rstrip('/'),
                               path_info.replace('/', '', 1))
        self.META = environ
        self.META['PATH_INFO'] = path_info
        self.META['SCRIPT_NAME'] = script_name
        self.method = environ['REQUEST_METHOD'].upper()
        _, content_params = cgi.parse_header(environ.get('CONTENT_TYPE', ''))
        if 'charset' in content_params:
            try:
                codecs.lookup(content_params['charset'])
            except LookupError:
                pass
            else:
                self.encoding = content_params['charset']
        self._post_parse_error = False
        try:
            content_length = int(environ.get('CONTENT_LENGTH'))
        except (ValueError, TypeError):
            content_length = 0
        self._stream = LimitedStream(self.environ['wsgi.input'], content_length)
        self._read_started = False
        self.resolver_match = None

    def _get_scheme(self):
        return self.environ.get('wsgi.url_scheme')

    @cached_property
    def GET(self):
        # The WSGI spec says 'QUERY_STRING' may be absent.
        raw_query_string = get_bytes_from_wsgi(self.environ, 'QUERY_STRING', '')
        return http.QueryDict(raw_query_string, encoding=self._encoding)

    def _get_post(self):
        if not hasattr(self, '_post'):
            self._load_post_and_files()
        return self._post

    def _set_post(self, post):
        self._post = post

    @cached_property
    def COOKIES(self):
        raw_cookie = get_str_from_wsgi(self.environ, 'HTTP_COOKIE', '')
        return http.parse_cookie(raw_cookie)

    def _get_files(self):
        if not hasattr(self, '_files'):
            self._load_post_and_files()
        return self._files

    POST = property(_get_post, _set_post)
    FILES = property(_get_files)


class WSGIHandler(base.BaseHandler):
    initLock = Lock()
    request_class = WSGIRequest

    """
        wsgi的实现,这里可以算是整个django的入口了
        WSGIHandler 最终被谁调用呢？就是被wsgi服务器,所以其实wsgi并非是网络协议他是很像java servlet的一种函数调用约定而已
        environ:一个包含所有HTTP请求信息的dict对象,浏览器的数据传给web服务器，web服务器把请求封装成eviron字典
        start_response:要求调用一次，有两个参数，一个是状态码,如'200 OK'，一个是header[('Content-Type', 'text/html')]那body呢
        body由return负责,return一个可迭代对象
    """
    def __call__(self, environ, start_response):
        # Set up middleware if needed. We couldn't do this earlier, because
        # settings weren't available.
        if self._request_middleware is None:
            with self.initLock:
                # Check that middleware is still uninitialized.
                if self._request_middleware is None:
                    self.load_middleware()

        set_script_prefix(get_script_name(environ))
        signals.request_started.send(sender=self.__class__, environ=environ)
        try:
            #这一步完成了wsgi的environ原始的数据的封装，封装成了django定义的request对象,把各种header body打包进request
            request = self.request_class(environ)
        except UnicodeDecodeError:
            logger.warning('Bad Request (UnicodeDecodeError)',
                exc_info=sys.exc_info(),
                extra={
                    'status_code': 400,
                }
            )
            response = http.HttpResponseBadRequest()
        else:
            #request进去response出来,response并不能直接返回，按照wsgi，把response拆成(http body)和(status code + header)两部分
            response = self.get_response(request)

        response._handler_class = self.__class__

        #剥离出status code
        status = '%s %s' % (response.status_code, response.reason_phrase)
        #剥离出headers
        response_headers = [(str(k), str(v)) for k, v in response.items()]
        #header加上set-cookies,其实也是header的一部分，只是response单独放cookies属性里面
        for c in response.cookies.values():
            response_headers.append((str('Set-Cookie'), str(c.output(header=''))))
        #wsgi第一步:实现了包含http状态码和header两个参数的函数调用完成了wsgi的其中一步
        start_response(force_str(status), response_headers)
        # TODO是文件流要特殊处理,具体如何处理
        if getattr(response, 'file_to_stream', None) is not None and environ.get('wsgi.file_wrapper'):
            response = environ['wsgi.file_wrapper'](response.file_to_stream)
        #wsgi第二步:返回可迭代body
        return response


def get_path_info(environ):
    """
    Returns the HTTP request's PATH_INFO as a unicode string.
    """
    path_info = get_bytes_from_wsgi(environ, 'PATH_INFO', '/')

    return path_info.decode(UTF_8)


def get_script_name(environ):
    """
    Returns the equivalent of the HTTP request's SCRIPT_NAME environment
    variable. If Apache mod_rewrite has been used, returns what would have been
    the script name prior to any rewriting (so it's the script name as seen
    from the client's perspective), unless the FORCE_SCRIPT_NAME setting is
    set (to anything).
    """
    if settings.FORCE_SCRIPT_NAME is not None:
        return force_text(settings.FORCE_SCRIPT_NAME)

    # If Apache's mod_rewrite had a whack at the URL, Apache set either
    # SCRIPT_URL or REDIRECT_URL to the full resource URL before applying any
    # rewrites. Unfortunately not every Web server (lighttpd!) passes this
    # information through all the time, so FORCE_SCRIPT_NAME, above, is still
    # needed.
    script_url = get_bytes_from_wsgi(environ, 'SCRIPT_URL', '')
    if not script_url:
        script_url = get_bytes_from_wsgi(environ, 'REDIRECT_URL', '')

    if script_url:
        if b'//' in script_url:
            # mod_wsgi squashes multiple successive slashes in PATH_INFO,
            # do the same with script_url before manipulating paths (#17133).
            script_url = _slashes_re.sub(b'/', script_url)
        path_info = get_bytes_from_wsgi(environ, 'PATH_INFO', '')
        script_name = script_url[:-len(path_info)] if path_info else script_url
    else:
        script_name = get_bytes_from_wsgi(environ, 'SCRIPT_NAME', '')

    return script_name.decode(UTF_8)


def get_bytes_from_wsgi(environ, key, default):
    """
    Get a value from the WSGI environ dictionary as bytes.

    key and default should be str objects. Under Python 2 they may also be
    unicode objects provided they only contain ASCII characters.
    """
    value = environ.get(str(key), str(default))
    # Under Python 3, non-ASCII values in the WSGI environ are arbitrarily
    # decoded with ISO-8859-1. This is wrong for Django websites where UTF-8
    # is the default. Re-encode to recover the original bytestring.
    return value.encode(ISO_8859_1) if six.PY3 else value


def get_str_from_wsgi(environ, key, default):
    """
    Get a value from the WSGI environ dictionary as str.

    key and default should be str objects. Under Python 2 they may also be
    unicode objects provided they only contain ASCII characters.
    """
    value = get_bytes_from_wsgi(environ, key, default)
    return value.decode(UTF_8, errors='replace') if six.PY3 else value
