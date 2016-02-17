# encoding: utf-8
from django.utils.cache import get_conditional_response
from django.utils.http import http_date, parse_http_date_safe, unquote_etag


class ConditionalGetMiddleware(object):
    """
    这个中间件主要根据http协议加上或者处理一些header或者逻辑

    ETag 是https协议response用来标记网页内容版本的类似hash的东西
    If-None-Match 是客户端发送服务端之前返回的页面ETag
    这两个配对用来标记版本，如果服务器看到这一次要返回的数据的Etag和客户端请求的
    If-None-Match是一样，意味着数据没变，只要给客户端304,让客户端用本地数据，
    就不用在发送一次了.

    http为什么有了last-modified还需哟etag呢：
    1.可能有些服务器无法精确得到最后修改时间
    2.修改非常平凡，在秒以下
    3.时间改了，但是内容没变
    Handles conditional GET operations. If the response has an ETag or
    Last-Modified header, and the request has If-None-Match or
    If-Modified-Since, the response is replaced by an HttpNotModified.

    Also sets the Date and Content-Length response-headers.
    """
    def process_response(self, request, response):
        # 加上时间头
        response['Date'] = http_date()
        # 不是文件流并且没有长度header则补上
        if not response.streaming and not response.has_header('Content-Length'):
            response['Content-Length'] = str(len(response.content))

        # 获得本次response的内容标示ETag
        etag = response.get('ETag')
        last_modified = response.get('Last-Modified')
        if last_modified:
            last_modified = parse_http_date_safe(last_modified)

        if etag or last_modified:
            return get_conditional_response(
                request,
                etag=unquote_etag(etag),
                last_modified=last_modified,
                response=response,
            )

        return response
