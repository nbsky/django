# encoding: utf-8
from __future__ import unicode_literals

import logging
from functools import update_wrapper

from django import http
from django.core.exceptions import ImproperlyConfigured
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, reverse
from django.utils import six
from django.utils.decorators import classonlymethod

logger = logging.getLogger('django.request')


class ContextMixin(object):
    """
    A default context mixin that passes the keyword arguments received by
    get_context_data as the template context.
    """

    def get_context_data(self, **kwargs):
        if 'view' not in kwargs:
            kwargs['view'] = self
        return kwargs


class View(object):
    """
    Intentionally simple parent class for all views. Only implements
    dispatch-by-method and simple sanity checking.
    所有views的父类，最重要的是实现了get post等方法的调度
    """

    http_method_names = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace']

    def __init__(self, **kwargs):
        """
        Constructor. Called in the URLconf; can contain helpful extra
        keyword arguments, and other things.
        """
        # Go through keyword arguments, and either save their values to our
        # instance, or raise an error.
        # 把传入的参数（必须是原来类属性）不然会报错,最终是由as_view()传入，比如我们可以传入template_name="about.html
        # 这样就不用在新建一个类
        for key, value in six.iteritems(kwargs):
            setattr(self, key, value)

    """
    函数式视图的缺点——难以扩展和自定义，开始显现出来。于是 1.3 起 django 开始用类视图来实现通用视图。较
    于函数，类能够更方便的实现继承和 mixins。但类视图并非要取代函数视图，这从现在 URLConf 仍然保留着函数式的调用方式便可以看出来。

    因为 URLConf 仍然使用“给一个可调用对象传入 HttpRequest ，并期待其返回一个 HttpResponse”这样的逻辑，所
    以对于类视图，必须设计一个可调用的接口。这就是类视图的 as_view() 类方法。
    他接受 request，并实例化类视图，接着调用实例的 dispatch() 方法。
    这个方法会依据 request 的请求类型再去调用实例的对应同名方法，并把 request 传过去，如果没有对应的方法，
    就引发一个 HttpResponseNotAllowed 异常。（可以捕捉这个异常用以返回一个 404）值得注意的是，
    这个（比如 get）方法的返回值和普通的视图函数的返回值没有什么不同，这意味着，
    http shortcuts（render_to_response之类的）和 TemplateResponse 在类视图里也是有效的。

    django 提供了一系列现成的类视图，他们都继承自一个 View 基类（django.views.generic.base.View）。
    在这个基类里实现了与 URLs 的接口（as_view）、请求方法匹配（dispatch）
    和一些其他的基本功能。比如 RedirectView 实现了一个简单的 HTTP 重定向，TemplateView 给 View 添加了一个渲染模板的功能。

    flask 事实上在后面的版本也借鉴了django这个方法加入了cbv
    如果不用as_view可能需要这样写 view().dispatch()可能觉得不够优雅
    """

    #classonlymethod继承自classmethod，所以下面是个类方法，同时重写__get__对类是否实例监控
    @classonlymethod
    def as_view(cls, **initkwargs):
        """
        Main entry point for a request-response process.
        请求入口,其实就是实例化一个view，并且返回这个view的dispatch方法,因为url里面就是接受一个函数
        """
        for key in initkwargs:
            # 参数不能是http的方法名，比如说不能是get,这里是用来替换属性的，如template_name="about.html",当然要是类的属性
            if key in cls.http_method_names:
                raise TypeError("You tried to pass in the %s method name as a "
                                "keyword argument to %s(). Don't do that."
                                % (key, cls.__name__))
            if not hasattr(cls, key):
                raise TypeError("%s() received an invalid keyword %r. as_view "
                                "only accepts arguments that are already "
                                "attributes of the class." % (cls.__name__, key))

        def view(request, *args, **kwargs):
            # url正则匹配出来的数据会传入，args kwargs
            # 实例化一个view
            self = cls(**initkwargs)
            if hasattr(self, 'get') and not hasattr(self, 'head'):
                self.head = self.get
            self.request = request
            self.args = args
            self.kwargs = kwargs
            return self.dispatch(request, *args, **kwargs)
        view.view_class = cls
        view.view_initkwargs = initkwargs

        # update_wrapper 的作用
        # 把后面的对象的__name__、module、__doc__和 __dict__ 拷贝到前面,一般用在修饰器消除__name__引起的debug困难
        # take name and docstring from class
        # as_view返回的这个view就是一个函数所以有必要update_wrapper消除副作用
        # view其实就是http方法映射实现：dispatch
        update_wrapper(view, cls, updated=())

        # and possible attributes set by decorators
        # like csrf_exempt from dispatch
        update_wrapper(view, cls.dispatch, assigned=())
        return view

    # 这个是配置url时候最终的入口，传入一个request对象,返回一个对应http方法的函数，如get()
    # 也就是根据request在返回一个对应的函数
    def dispatch(self, request, *args, **kwargs):
        # Try to dispatch to the right method; if a method doesn't exist,
        # defer to the error handler. Also defer to the error handler if the
        # request method isn't on the approved list.
        # 这个类中我们看不到任何的get post等方法，但是却可以调度在于使用了getattr这种动态的方式从字符串动态匹配方法
        if request.method.lower() in self.http_method_names:
            handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
        else:
            handler = self.http_method_not_allowed
        return handler(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        logger.warning('Method Not Allowed (%s): %s', request.method, request.path,
            extra={
                'status_code': 405,
                'request': request
            }
        )
        return http.HttpResponseNotAllowed(self._allowed_methods())

    def options(self, request, *args, **kwargs):
        """
        Handles responding to requests for the OPTIONS HTTP verb.
        询问服务器支持的方法，比如说支持get，就在allow header返回get,options不用返回header
        这个不像get，post和业务相关，所以在这个父类实现。
        """
        response = http.HttpResponse()
        response['Allow'] = ', '.join(self._allowed_methods())
        response['Content-Length'] = '0'
        return response

    def _allowed_methods(self):
        return [m.upper() for m in self.http_method_names if hasattr(self, m)]


class TemplateResponseMixin(object):
    """
    A mixin that can be used to render a template.
    混入模板渲染功能的mixin加进view可以组成出有模板渲染能力的view
    """
    template_name = None
    template_engine = None
    # 核心在这里，我们加入了一个渲染类,我们使用的时候就只要配置这个类即可
    response_class = TemplateResponse
    content_type = None

    def render_to_response(self, context, **response_kwargs):
        """
        Returns a response, using the `response_class` for this
        view, with a template rendered with the given context.

        If any keyword arguments are provided, they will be
        passed to the constructor of the response class.
        """
        response_kwargs.setdefault('content_type', self.content_type)
        return self.response_class(
            request=self.request,
            template=self.get_template_names(),
            context=context,
            using=self.template_engine,
            **response_kwargs
        )

    def get_template_names(self):
        """
        Returns a list of template names to be used for the request. Must return
        a list. May not be called if render_to_response is overridden.
        """
        if self.template_name is None:
            raise ImproperlyConfigured(
                "TemplateResponseMixin requires either a definition of "
                "'template_name' or an implementation of 'get_template_names()'")
        else:
            return [self.template_name]


class TemplateView(TemplateResponseMixin, ContextMixin, View):
    """
    A view that renders a template.  This view will also pass into the context
    any keyword arguments passed by the URLconf.
    """
    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)


class RedirectView(View):
    """
    A view that provides a redirect on any GET request.
    用来处理重定向的view
    """
    permanent = False
    url = None
    pattern_name = None
    query_string = False

    def get_redirect_url(self, *args, **kwargs):
        """
        Return the URL redirect to. Keyword arguments from the
        URL pattern match generating the redirect request
        are provided as kwargs to this method.
        """
        if self.url:
            url = self.url % kwargs
        elif self.pattern_name:
            try:
                url = reverse(self.pattern_name, args=args, kwargs=kwargs)
            except NoReverseMatch:
                return None
        else:
            return None

        args = self.request.META.get('QUERY_STRING', '')
        if args and self.query_string:
            url = "%s?%s" % (url, args)
        return url

    def get(self, request, *args, **kwargs):
        url = self.get_redirect_url(*args, **kwargs)
        if url:
            if self.permanent:
                # 永久重定向
                # 比如用户http://letv.com/user/1 少加/
                # 需要被永久定向的http://letv.com/user/1/ 对于浏览器他就知道下次应该访问后者，对应的资源都应该这样处理
                # 浏览器甚至有必要更改他的书签
                return http.HttpResponsePermanentRedirect(url)
            else:
                # 默认302临时重定向
                return http.HttpResponseRedirect(url)
        else:
            logger.warning('Gone: %s', request.path,
                        extra={
                            'status_code': 410,
                            'request': request
                        })
            return http.HttpResponseGone()

    # 无论接受什么方法最终用get处理重定向
    def head(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def options(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)
