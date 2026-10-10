from django.contrib.auth.backends import ModelBackend

class MockCampusBackend(ModelBackend):
    """Replaceable local adapter. No connection to any university identity service."""
