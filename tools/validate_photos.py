"""Ensure public clients don't bundle unlicensed third-party lab photographs."""
from pathlib import Path
import json

root = Path(__file__).resolve().parents[1]
web = root / "static" / "photos"
mini = root / "miniprogram" / "assets" / "photos"
web_catalog = json.loads((web / "catalog.json").read_text("utf-8"))
mini_catalog = json.loads((mini / "catalog.json").read_text("utf-8"))
module = (mini / "catalog.js").read_text("utf-8").split("module.exports = ", 1)[1].strip().removesuffix(";")

assert web_catalog == [] and mini_catalog == [] and json.loads(module) == [], "Both clients must use the no-photo placeholder until rights are verified"
for directory in (web, mini):
    bundled = [p.name for p in directory.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".webp"}]
    assert not bundled, f"Unapproved photographs remain bundled: {directory}: {bundled}"

for required in ("static/photos.css", "templates/resources.html", "templates/login.html", "miniprogram/pages/resources/index.wxml", "docs/图片来源.md"):
    assert (root / required).exists(), f"Missing no-photo policy surface: {required}"

print("PASS: web and mini-program catalogs are empty; no third-party lab photographs are bundled")
