# encoding: utf-8
from __future__ import unicode_literals

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.http import Http404
from django.utils.translation import ugettext as _
from django.views.generic.base import ContextMixin, TemplateResponseMixin, View


class SingleObjectMixin(ContextMixin):
    """
    Provides the ability to retrieve a single object for further manipulation.
    提供的默认行为就是，通过url正则传入的pk或者slug，从queryset model这些基础配置信息中
    在get方法里面查找单挑数据
    """
    # 比如model = User
    model = None
    # 比如queryset = User.objects.all()
    queryset = None
    slug_field = 'slug'
    context_object_name = None
    slug_url_kwarg = 'slug'
    pk_url_kwarg = 'pk'
    query_pk_and_slug = False


    # 默认会被get()调用
    # 默认get_queryset方法
    def get_object(self, queryset=None):
        """
        Returns the object the view is displaying.

        By default this requires `self.queryset` and a `pk` or `slug` argument
        in the URLconf, but subclasses can override this to return any object.
        pk 和 slug都可以在url正则配置，url调度会默认把这两个指传入kwargs里面，
        而view默认使用这两个，查找数据，有时候用pk唯一一条数据不是很方便，比如数据库不一样id不一样，
        slug可以作为唯一键字段，比pk更灵活
        这里 queryset 和 pk 和slug是串行的，通常我们只设置一个就够了
        """
        # Use a custom queryset if provided; this is required for subclasses
        # like DateDetailView
        # 优先选择queryset属性配置，如果没有更加model生成queryset
        if queryset is None:
            queryset = self.get_queryset()

        # Next, try looking up by primary key.
        pk = self.kwargs.get(self.pk_url_kwarg)
        slug = self.kwargs.get(self.slug_url_kwarg)
        # 加入pk where语句
        if pk is not None:
            queryset = queryset.filter(pk=pk)

        # Next, try looking up by slug.
        # 如果有slug继续加入slug 查找条件,也可以同时使用pk和slug
        if slug is not None and (pk is None or self.query_pk_and_slug):
            slug_field = self.get_slug_field()
            queryset = queryset.filter(**{slug_field: slug})

        # If none of those are defined, it's an error.
        # url正郑一定要有pk或者slug这种参数的否则报错
        if pk is None and slug is None:
            raise AttributeError("Generic detail view %s must be called with "
                                 "either an object pk or a slug."
                                 % self.__class__.__name__)

        try:
            # Get the single item from the filtered queryset
            obj = queryset.get()
        except queryset.model.DoesNotExist:
            raise Http404(_("No %(verbose_name)s found matching the query") %
                          {'verbose_name': queryset.model._meta.verbose_name})
        return obj

    def get_queryset(self):
        """
        Return the `QuerySet` that will be used to look up the object.

        Note that this method is called by the default implementation of
        `get_object` and may not be called if `get_object` is overridden.
        这个方法默认会被get_object调用，如果get_object被重写可能就不会被调用了
        根据model属性获取queryset

        pk或者slug默认从queryset属性里面查找，如果没有这个数据就靠这个方法从model属性生成queryset
        """
        if self.queryset is None:
            if self.model:
                return self.model._default_manager.all()
            else:
                raise ImproperlyConfigured(
                    "%(cls)s is missing a QuerySet. Define "
                    "%(cls)s.model, %(cls)s.queryset, or override "
                    "%(cls)s.get_queryset()." % {
                        'cls': self.__class__.__name__
                    }
                )
        return self.queryset.all()

    def get_slug_field(self):
        """
        Get the name of a slug field to be used to look up by slug.
        除了pk也可以使用slug标示一条记录，我们更方便的处理不同数据库环境的数据保持唯一键一致
        """
        return self.slug_field

    def get_context_object_name(self, obj):
        """
        Get the name to use for the object.
        """
        if self.context_object_name:
            return self.context_object_name
        elif isinstance(obj, models.Model):
            if obj._deferred:
                obj = obj._meta.proxy_for_model
            return obj._meta.model_name
        else:
            return None

    def get_context_data(self, **kwargs):
        """
        Insert the single object into the context dict.
        从获取的object插入context，也就完成了从数据库到模板的数据传递
        """
        context = {}
        if self.object:
            context['object'] = self.object
            context_object_name = self.get_context_object_name(self.object)
            if context_object_name:
                context[context_object_name] = self.object
        context.update(kwargs)
        return super(SingleObjectMixin, self).get_context_data(**context)


class BaseDetailView(SingleObjectMixin, View):
    """
    给基本View组合上获取单个数据对象的能力，并实现基本view的get方法，调用这个能力

    A base view for displaying a single object
    用来展示单个对象详情的view，比如我们实现某个用户的信息详情页
    最核心的就是他默认会调用get_object方法来想数据库查找单个对象
    但是查找条件呢？
    view中默认是先查找pk，再查找slug，我们需要在url正则中配置这两个字段
    """
    def get(self, request, *args, **kwargs):
        # 获取单条数据行
        self.object = self.get_object()
        # 传入给模板的变量
        context = self.get_context_data(object=self.object)
        # render_to_response这个方法只能mixin具有这个方法的类使用?,这个方法在view和SingleObjectMixin中并没有
        return self.render_to_response(context)


class SingleObjectTemplateResponseMixin(TemplateResponseMixin):
    template_name_field = None
    template_name_suffix = '_detail'

    def get_template_names(self):
        """
        Return a list of template names to be used for the request. May not be
        called if render_to_response is overridden. Returns the following list:

        * the value of ``template_name`` on the view (if provided)
        * the contents of the ``template_name_field`` field on the
          object instance that the view is operating upon (if available)
        * ``<app_label>/<model_name><template_name_suffix>.html``
        """
        try:
            names = super(SingleObjectTemplateResponseMixin, self).get_template_names()
        except ImproperlyConfigured:
            # If template_name isn't specified, it's not a problem --
            # we just start with an empty list.
            names = []

            # If self.template_name_field is set, grab the value of the field
            # of that name from the object; this is the most specific template
            # name, if given.
            if self.object and self.template_name_field:
                name = getattr(self.object, self.template_name_field, None)
                if name:
                    names.insert(0, name)

            # The least-specific option is the default <app>/<model>_detail.html;
            # only use this if the object in question is a model.
            if isinstance(self.object, models.Model):
                object_meta = self.object._meta
                if self.object._deferred:
                    object_meta = self.object._meta.proxy_for_model._meta
                names.append("%s/%s%s.html" % (
                    object_meta.app_label,
                    object_meta.model_name,
                    self.template_name_suffix
                ))
            elif hasattr(self, 'model') and self.model is not None and issubclass(self.model, models.Model):
                names.append("%s/%s%s.html" % (
                    self.model._meta.app_label,
                    self.model._meta.model_name,
                    self.template_name_suffix
                ))

            # If we still haven't managed to find any template names, we should
            # re-raise the ImproperlyConfigured to alert the user.
            if not names:
                raise

        return names


class DetailView(SingleObjectTemplateResponseMixin, BaseDetailView):
    """
    Render a "detail" view of an object.

    By default this is a model instance looked up from `self.queryset`, but the
    view will support display of *any* object by overriding `self.get_object()`.
    """
