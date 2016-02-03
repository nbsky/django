# encoding: utf-8
import time
from importlib import import_module

from django.conf import settings
from django.utils.cache import patch_vary_headers
from django.utils.http import cookie_date


class SessionMiddleware(object):
    def __init__(self):
        #settings.SESSION_ENGINE session的具体存储实现可以自己继承backends里面的几个类自定义功能并通过配置生效
        engine = import_module(settings.SESSION_ENGINE)
        self.SessionStore = engine.SessionStore

    def process_request(self, request):
        #settings.SESSION_COOKIE_NAME cookies中保存的session的名字是可配置的默认是'sessionid'
        session_key = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
        #给request添加一个session实例属性,这时候session实例最重要的init只是从cookies得到sessionid,session信息只有第一次读取的时候才回加载
        request.session = self.SessionStore(session_key)

    def process_response(self, request, response):
        """
        If request.session was modified, or if the configuration is to save the
        session every time, save the changes and set a session cookie or delete
        the session cookie if the session has been emptied.
        """
        try:
            accessed = request.session.accessed
            modified = request.session.modified
            empty = request.session.is_empty()
        except AttributeError:
            pass
        else:
            # First check if we need to delete this cookie.
            # The session should be deleted only if the session is entirely empty
            # 如果session我们没有填充任何数据，cookies中的sessionid将会被删掉,
            # so 当session没有信息时,又没登录时cookies里面是不会有sessionid的,但是匿名状态有数据是会有的
            # 因为这种情况服务端session没有储存任何东西，即便有sessionid也只是纯粹浪费空间

            # Cookies中max-age作为对expires的补充，现阶段有兼容性问题（IE低版本不支持），所以一般不单独使用，max-age是一个时间段比较合理，expires是一个时间点，容易不同步。
            if settings.SESSION_COOKIE_NAME in request.COOKIES and empty:
                response.delete_cookie(settings.SESSION_COOKIE_NAME,
                    domain=settings.SESSION_COOKIE_DOMAIN)
            else:
                if accessed:
                    # 本次response依赖session，要确保header的vary字段，有Cookie，告诉缓存服务器这个response是有依赖cookie的，也就是对不同用户有差别的,如果用户访问一个任何人都可以访问的我们的逻辑代码肯定不用访问session，缓存服务器也不用区别对待
                    patch_vary_headers(response, ('Cookie',))
                if (modified or settings.SESSION_SAVE_EVERY_REQUEST) and not empty:
                    # settings.SESSION_EXPIRE_AT_BROWSER_CLOSE 设置是否浏览器关闭session失效,如果是这样就没有过期时间了，因为关闭后seesion也就没了
                    # 为什么要加入empty判断？因为如果设置了SESSION_SAVE_EVERY_REQUEST用户只要简单直接访问一个链接都会创建一条session(空会话)在数据库,形成DoS攻击，这个在1.8.4之后增加的
                    if request.session.get_expire_at_browser_close():
                        max_age = None
                        expires = None
                    else:
                        # 每次session修改或设置保存session，就会刷新cookie的过期时间，让他往后延
                        max_age = request.session.get_expiry_age()
                        expires_time = time.time() + max_age
                        expires = cookie_date(expires_time)
                    # Save the session data and refresh the client cookie.
                    # Skip session save for 500 responses, refs #3881.
                    if response.status_code != 500:
                        # 真正保存持久化是这里，创建或者保存
                        request.session.save()
                        response.set_cookie(settings.SESSION_COOKIE_NAME,
                                request.session.session_key, max_age=max_age,
                                expires=expires, domain=settings.SESSION_COOKIE_DOMAIN,
                                path=settings.SESSION_COOKIE_PATH,
                                secure=settings.SESSION_COOKIE_SECURE or None,
                                httponly=settings.SESSION_COOKIE_HTTPONLY or None)
        return response
