# encoding: utf-8
import re

from django.utils.cache import patch_vary_headers
from django.utils.text import compress_sequence, compress_string

re_accepts_gzip = re.compile(r'\bgzip\b')


class GZipMiddleware(object):
    """
    This middleware compresses content if the browser allows gzip compression.
    It sets the Vary header accordingly, so that caches will base their storage
    on the Accept-Encoding header.
    """
    def process_response(self, request, response):
        # It's not worth attempting to compress really short responses.
        # 太小不值得压缩
        if not response.streaming and len(response.content) < 200:
            return response

        # Avoid gzipping if we've already got a content-encoding.
        # 如果已经是被压缩过过的也不用处理
        if response.has_header('Content-Encoding'):
            return response

        # 确保Accept-Encoding在vary里面，因为这压缩和不压缩得告诉缓存服务器这是两个不不一样的，缓存服务器是通过url和vary标记的header的信息做hash来索引数据
        patch_vary_headers(response, ('Accept-Encoding',))

        # request里面如果不支持gzip也不压缩
        ae = request.META.get('HTTP_ACCEPT_ENCODING', '')
        if not re_accepts_gzip.search(ae):
            return response

        if response.streaming:
            # Delete the `Content-Length` header for streaming content, because
            # we won't know the compressed size until we stream it.
            # 如果是文件流是持续传输的要持续多次压缩传输，无法指定整个response大小否则客户端接收可能会有问题
            response.streaming_content = compress_sequence(response.streaming_content)
            del response['Content-Length']
        else:
            # Return the compressed content only if it's actually shorter.
            # 压缩后没有变小也不压缩
            compressed_content = compress_string(response.content)
            if len(compressed_content) >= len(response.content):
                return response
            # 更新body, 和Content-Length(body的大小)
            response.content = compressed_content
            response['Content-Length'] = str(len(response.content))

        # 如果启用了etag，我们应该标注这个etag是gzip后的，因为内容实体被压缩，理论上应该从新计算etag但是没有必要标注下即可
        # 这里在原来的etag后面加了';gzip'字符串
        if response.has_header('ETag'):
            response['ETag'] = re.sub('"$', ';gzip"', response['ETag'])

        # 写上压缩算法
        response['Content-Encoding'] = 'gzip'

        return response
