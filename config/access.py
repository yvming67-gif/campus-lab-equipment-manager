"""Public entry page; platform release is independent of web deployment."""
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@never_cache
@require_safe
def access(request):
    return render(request, 'access.html', {'title': '访问入口'})
