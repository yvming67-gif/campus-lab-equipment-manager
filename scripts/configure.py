"""Generate local JSON configuration; preserve existing values. No .env loading."""
import json
import secrets
from pathlib import Path
root = Path(__file__).resolve().parent.parent
path = root / '.local' / 'settings.json'
path.parent.mkdir(exist_ok=True)
if not path.exists():
    path.write_text(json.dumps({'SECRET_KEY': secrets.token_urlsafe(50),
        'PGPASSWORD': secrets.token_urlsafe(28)}, indent=2), encoding='utf-8')
print('本地配置已就绪：.local/settings.json（不打印密钥）')
