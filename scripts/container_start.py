import subprocess
import sys
import time
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.db import connection
for attempt in range(30):
    try:
        connection.ensure_connection();break
    except Exception:
        if attempt==29:raise
        time.sleep(1)
subprocess.run([sys.executable,'manage.py','migrate','--noinput'],check=True)
subprocess.run([sys.executable,'manage.py','seed_demo'],check=True)
os.execv(sys.executable,[sys.executable,'manage.py','runserver','0.0.0.0:8000','--noreload'])
