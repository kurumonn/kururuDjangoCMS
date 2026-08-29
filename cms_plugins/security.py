from functools import wraps

from django.http import Http404

from .models import is_plugin_enabled


def require_plugin_enabled(key: str):
    """公開ViewをDB上の有効状態でfail-closedにする。"""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not is_plugin_enabled(key):
                raise Http404
            return view(request, *args, **kwargs)

        return wrapped
    return decorator
