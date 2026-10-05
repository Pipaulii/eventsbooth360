"""Published landing pages and sitemap share one explicit registry."""
import json
from pathlib import Path
from xml.sax.saxutils import escape
from flask import Blueprint,render_template,Response,abort
ROOT=Path(__file__).resolve().parent
PAGES=json.loads((ROOT/'seo_pages.json').read_text(encoding='utf-8'))
bp=Blueprint('seo',__name__)
@bp.get('/sitemap.xml')
def sitemap():
    urls=['https://eventsbooth360.fr/']+['https://eventsbooth360.fr/'+slug for slug in PAGES]
    xml='<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+escape(url)+'</loc></url>' for url in urls)+'</urlset>'
    return Response(xml,mimetype='application/xml')
@bp.get('/<slug>')
def landing(slug):
    if slug not in PAGES:
        return __import__('flask').send_from_directory(ROOT/'public',slug)
    return render_template('seo.html',page=PAGES[slug],slug=slug,pages=PAGES)
