# encoding: utf-8
import inspect
import re

from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.middleware.csrf import rotate_token
from django.utils.crypto import constant_time_compare
from django.utils.module_loading import import_string
from django.utils.translation import LANGUAGE_SESSION_KEY

from .signals import user_logged_in, user_logged_out, user_login_failed

SESSION_KEY = '_auth_user_id'
BACKEND_SESSION_KEY = '_auth_user_backend'
HASH_SESSION_KEY = '_auth_user_hash'
REDIRECT_FIELD_NAME = 'next'


def load_backend(path):
    return import_string(path)()


def _get_backends(return_tuples=False):
    backends = []
    for backend_path in settings.AUTHENTICATION_BACKENDS:
        backend = load_backend(backend_path)
        backends.append((backend, backend_path) if return_tuples else backend)
    if not backends:
        raise ImproperlyConfigured(
            'No authentication backends have been defined. Does '
            'AUTHENTICATION_BACKENDS contain anything?'
        )
    return backends


def get_backends():
    return _get_backends(return_tuples=False)


def _clean_credentials(credentials):
    """
    Cleans a dictionary of credentials of potentially sensitive info before
    sending to less secure functions.

    Not comprehensive - intended for user_login_failed signal
    """
    SENSITIVE_CREDENTIALS = re.compile('api|token|key|secret|password|signature', re.I)
    CLEANSED_SUBSTITUTE = '********************'
    for key in credentials:
        if SENSITIVE_CREDENTIALS.search(key):
            credentials[key] = CLEANSED_SUBSTITUTE
    return credentials


def _get_user_session_key(request):
    # This value in the session is always serialized to a string, so we need
    # to convert it back to Python whenever we access it.
    # 服务端session保存着userid,这里其实是从request.session中取得user_id
    return get_user_model()._meta.pk.to_python(request.session[SESSION_KEY])


def authenticate(**credentials):
    """
    If the given credentials are valid, return a User object.
    """
    # 调用 settings.AUTHENTICATION_BACKENDS 里面配置的backend的里面的authenticate, 只要一个成功就返回user
    for backend, backend_path in _get_backends(return_tuples=True):
        try:
            inspect.getcallargs(backend.authenticate, **credentials)
        except TypeError:
            # This backend doesn't accept these credentials as arguments. Try the next one.
            continue

        try:
            user = backend.authenticate(**credentials)
        except PermissionDenied:
            # This backend says to stop in our tracks - this user should not be allowed in at all.
            return None
        if user is None:
            continue
        # Annotate the user object with the path of the backend.
        user.backend = backend_path
        return user

    # The credentials supplied are invalid to all backends, fire signal
    user_login_failed.send(sender=__name__,
            credentials=_clean_credentials(credentials))


def login(request, user, backend=None):
    """
    Persist a user id and a backend in the request. This way a user doesn't
    have to reauthenticate on every request. Note that data set during
    the anonymous session is retained when the user logs in.
    """
    session_auth_hash = ''
    if user is None:
        user = request.user
    if hasattr(user, 'get_session_auth_hash'):
        session_auth_hash = user.get_session_auth_hash()

    # session已经保存了用户id,当然这里不能保证是当前用户的用户id，
    # 如果是当前用户id，密码也可能已经变了,这两种情况都彻底清空session
    # 这里的SESSION_KEY是session中user_id的键值
    if SESSION_KEY in request.session:
        if _get_user_session_key(request) != user.pk or (
                session_auth_hash and
                request.session.get(HASH_SESSION_KEY) != session_auth_hash):
            # To avoid reusing another user's session, create a new, empty
            # session if the existing session corresponds to a different
            # authenticated user.
            request.session.flush()
    else:
        #session没有保存用户id，也就是如果用户从匿名状态登录到非匿名，sessionid会刷新，但是session的其他数据不会丢失
        #cycle_key 维持session的数据不变，但是更改sessionid
        request.session.cycle_key()

    try:
        backend = backend or user.backend
    except AttributeError:
        backends = _get_backends(return_tuples=True)
        if len(backends) == 1:
            _, backend = backends[0]
        else:
            raise ValueError(
                'You have multiple authentication backends configured and '
                'therefore must provide the `backend` argument or set the '
                '`backend` attribute on the user.'
            )
    # 这里的SESSION_KEY和middleware的session_key不一样，这里是一个固定的键值，可以直接理解为'user_id',里面保存user_id
    # BACKEND_SESSION_KEY 保存session具体储存实现引擎如 'django.contrib.auth.backends.ModelBackend'
    # HASH_SESSION_KEY 保存加盐密码的hash，如果用户密码更换了,就是依靠这个字段判断
    request.session[SESSION_KEY] = user._meta.pk.value_to_string(user)
    request.session[BACKEND_SESSION_KEY] = backend
    request.session[HASH_SESSION_KEY] = session_auth_hash
    if hasattr(request, 'user'):
        request.user = user
    rotate_token(request)
    user_logged_in.send(sender=user.__class__, request=request, user=user)


def logout(request):
    """
    Removes the authenticated user's ID from the request and flushes their
    session data.
    """
    # Dispatch the signal before the user is logged out so the receivers have a
    # chance to find out *who* logged out.
    user = getattr(request, 'user', None)
    if hasattr(user, 'is_authenticated') and not user.is_authenticated():
        user = None
    user_logged_out.send(sender=user.__class__, request=request, user=user)

    # remember language choice saved to session
    language = request.session.get(LANGUAGE_SESSION_KEY)

    request.session.flush()

    if language is not None:
        request.session[LANGUAGE_SESSION_KEY] = language

    if hasattr(request, 'user'):
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()


def get_user_model():
    """
    Returns the User model that is active in this project.
    """

    try:
        return django_apps.get_model(settings.AUTH_USER_MODEL)
    except ValueError:
        raise ImproperlyConfigured("AUTH_USER_MODEL must be of the form 'app_label.model_name'")
    except LookupError:
        raise ImproperlyConfigured(
            "AUTH_USER_MODEL refers to model '%s' that has not been installed" % settings.AUTH_USER_MODEL
        )


def get_user(request):
    """
    Returns the user model instance associated with the given request session.
    If no user is retrieved an instance of `AnonymousUser` is returned.
    从用户内存session中取到userid，根据userid从储存取到密码hash和内存session中的hash密码对比通过
    返回user对象，不对则返回AnonymousUser
    根据userid获取user对象是可以定制的，在AUTHENTICATION_BACKENDS配置
    """
    from .models import AnonymousUser
    user = None
    try:
        # 从session中获取userid
        user_id = _get_user_session_key(request)
        # backend_path是session具体实现的引擎,如db引擎
        backend_path = request.session[BACKEND_SESSION_KEY]
    except KeyError:
        pass
    else:
        # userid->user对象 的方法是可以自定义的
        # 调用 settings.AUTHENTICATION_BACKENDS 里面配置的backend的里面的get_user, 只要一个成功就返回user
        # 比如 django.contrib.auth.middleware.AuthenticationMiddleware 会把这个函数转为request.user
        # user_id 就是从session取得
        if backend_path in settings.AUTHENTICATION_BACKENDS:
            backend = load_backend(backend_path)
            user = backend.get_user(user_id)
            # Verify the session
            # 校验session，其实就是校验内存里session里面的密码hash有没有和储存里面的一样
            if hasattr(user, 'get_session_auth_hash'):
                session_hash = request.session.get(HASH_SESSION_KEY)
                session_hash_verified = session_hash and constant_time_compare(
                    session_hash,
                    user.get_session_auth_hash()
                )
                if not session_hash_verified:
                    request.session.flush()
                    user = None

    return user or AnonymousUser()


def get_permission_codename(action, opts):
    """
    Returns the codename of the permission for the specified action.
    """
    return '%s_%s' % (action, opts.model_name)


def update_session_auth_hash(request, user):
    """
    Updating a user's password logs out all sessions for the user.

    This function takes the current request and the updated user object from
    which the new session hash will be derived and updates the session hash
    appropriately to prevent a password change from logging out the session
    from which the password was changed.
    """
    if hasattr(user, 'get_session_auth_hash') and request.user == user:
        request.session[HASH_SESSION_KEY] = user.get_session_auth_hash()

default_app_config = 'django.contrib.auth.apps.AuthConfig'
