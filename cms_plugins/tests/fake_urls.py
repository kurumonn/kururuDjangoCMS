from django.http import HttpResponse
from django.urls import path


def unguarded_view(request):
    return HttpResponse("unguarded")


app_name = "fake_plugin"
urlpatterns = [path("action/", unguarded_view, name="action")]
