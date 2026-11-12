"""Public entry page; platform release is independent of web deployment."""
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from core.demo_identity import review_key


@never_cache
@require_safe
def access(request):
    return render(request, 'access.html', {'title': '访问入口', 'review_available': bool(review_key())})
