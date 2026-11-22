import re, markdown
src=open('report.md',encoding='utf-8').read().replace('\u200e','').replace('\u200f','')
html=markdown.markdown(src,extensions=['tables','nl2br'])
TOK=r"[A-Za-z0-9./@:_+\-~'\"]*[A-Za-z][A-Za-z0-9./@:_+\-~]*"
LAT=re.compile(r"(?:/?"+TOK+r")(?:[ ,/&]+(?:/?"+TOK+r"))*|/|0\d{2}-\d{3}-\d{4}")
def wrap_text(t):
    def rep(m):
        s=m.group(0)
        if s=='/': return s
        tail=''
        while s and s[-1] in '.,:;': tail=s[-1]+tail; s=s[:-1]
        while s and s[-1] in ' ,': tail=s[-1]+tail; s=s[:-1]
        if not s: return m.group(0)
        return f'<span dir="ltr" class="en">{s}</span>{tail}'
    return LAT.sub(rep,t)
parts=re.split(r'(<[^>]+>)',html)
html=''.join(p if p.startswith('<') else wrap_text(p) for p in parts)
html=html.replace('<table>','<table dir="rtl">')
css="""
@page{size:A4;margin:16mm 14mm}
body{font-family:"Assistant","Rubik","Heebo","Segoe UI",Arial,sans-serif;line-height:1.6;color:#1b1f24;max-width:900px;margin:24px auto;padding:0 20px;font-size:15px}
h1{font-size:26px;color:#0b3d91;border-bottom:3px solid #0b3d91;padding-bottom:8px;margin-bottom:12px}
h2{font-size:20px;color:#0b3d91;margin-top:30px;border-bottom:1px solid #d0d7de;padding-bottom:4px}
h3{font-size:16px;color:#1f4f9c;margin-top:18px}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px;page-break-inside:avoid}
th,td{border:1px solid #c9d1d9;padding:6px 8px;text-align:right;vertical-align:top}
th{background:#eef3fb}
tr:nth-child(even) td{background:#fafbfc}
hr{border:none;border-top:1px solid #d0d7de;margin:22px 0}
.en{unicode-bidi:isolate;direction:ltr;font-family:"Segoe UI",Arial,sans-serif}
strong{color:#0d1b2a}
.summary{background:#f3f7fd;border:1px solid #b9cbe8;border-radius:8px;padding:6px 18px 12px;page-break-after:auto}
@media print{body{margin:0;max-width:none;font-size:12.5px}a{color:inherit;text-decoration:none}h2{page-break-after:avoid}}
"""
# wrap exec summary section in a box
html=re.sub(r'(<h2>תקציר מנהלים.*?)(<hr />)',r'<section class="summary">\1</section>\n\2',html,count=1,flags=re.S)
out=f'<!DOCTYPE html>\n<html lang="he" dir="rtl">\n<head>\n<meta charset="utf-8">\n<title>דוח AgentReady — מאפיית לחמים</title>\n<style>{css}</style>\n</head>\n<body dir="rtl">\n{html}\n</body>\n</html>\n'
open('report.html','w',encoding='utf-8').write(out)
