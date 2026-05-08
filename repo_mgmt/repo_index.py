"""Repository index builder with Node/Express route discovery."""
from __future__ import annotations
import json, logging, re, subprocess
from pathlib import Path
from typing import Any
logger=logging.getLogger(__name__)
try:
    import pathspec as _pathspec_mod; _HAS_PATHSPEC=True
except ImportError:
    _pathspec_mod=None; _HAS_PATHSPEC=False
_EXPRESS_RE=re.compile(r"(?:app|router|[A-Za-z0-9_]+Router)\s*\.\s*(?:get|post|put|patch|delete|use|all)\s*\(\s*['\"]([^'\"]+)['\"]")
def _load_gitignore(repo_root:Path)->Any:
    gp=repo_root/'.gitignore'
    if not gp.is_file(): return None
    pats=gp.read_text(encoding='utf-8',errors='replace').splitlines()
    if _HAS_PATHSPEC: return _pathspec_mod.PathSpec.from_lines('gitwildmatch',pats)
    return [p.strip().rstrip('/') for p in pats if p.strip() and not p.lstrip().startswith('#')]
def _ignored(rel:str,spec:Any)->bool:
    if spec is None: return False
    if hasattr(spec,'match_file'): return bool(spec.match_file(rel))
    return any(rel==p or rel.startswith(p+'/') or Path(rel).name==p for p in spec)
def _walk(repo_root:Path,spec:Any)->list[str]:
    out=[]
    for path in repo_root.rglob('*'):
        if path.is_dir(): continue
        rel=path.relative_to(repo_root).as_posix(); parts=path.relative_to(repo_root).parts
        if any(x.startswith('.') for x in parts): continue
        if _ignored(rel,spec): continue
        out.append(rel)
    return sorted(out)
def _recent_commits(repo_root:Path,n:int=10)->list[str]:
    try: return subprocess.run(['git','log','--oneline',f'-{n}'],cwd=repo_root,capture_output=True,text=True,timeout=10).stdout.strip().splitlines()
    except Exception: return []
def _discover_routes(repo_root:Path, file_list:list[str])->list[str]:
    routes={f for f in file_list if f.startswith('pages/') or f.startswith('app/')}; candidates=[]
    for f in file_list:
        p=Path(f); name=p.name.lower()
        if f in {'server.js','server.ts'}: candidates.append(f)
        elif f.startswith('routes/') and p.suffix in {'.js','.mjs','.cjs','.ts'}: candidates.append(f)
        elif f.startswith('audits/routes/') and p.suffix in {'.js','.mjs','.cjs','.ts'}: candidates.append(f)
        elif f.startswith('services/') and p.suffix in {'.js','.mjs','.cjs','.ts'} and ('route' in name or name in {'server.js','index.js','app.js'}): candidates.append(f)
    for rel in candidates:
        txt=(repo_root/rel).read_text(encoding='utf-8',errors='replace')
        routes.update(m.group(1) for m in _EXPRESS_RE.finditer(txt))
    return sorted(routes)
def build_node_index(repo_root:Path)->dict[str,Any]:
    spec=_load_gitignore(repo_root); files=_walk(repo_root,spec); by={}
    for f in files: by.setdefault(Path(f).suffix or '(none)',[]).append(f)
    scripts={}; pkg=repo_root/'package.json'
    if pkg.is_file():
        try: scripts=json.loads(pkg.read_text(encoding='utf-8')).get('scripts',{})
        except json.JSONDecodeError: pass
    return {'file_list':files,'by_extension':by,'route_strings':_discover_routes(repo_root,files),'package_scripts':scripts,'recent_commits':_recent_commits(repo_root)}
def build_static_index(repo_root:Path)->dict[str,Any]:
    spec=_load_gitignore(repo_root); files=_walk(repo_root,spec); by={}
    for f in files: by.setdefault(Path(f).suffix or '(none)',[]).append(f)
    return {'file_list':files,'by_extension':by,'html_pages':[f for f in files if f.endswith('.html')],'css_files':[f for f in files if f.endswith('.css')],'partial_files':[f for f in files if 'partial' in f.lower()],'recent_commits':_recent_commits(repo_root)}
def build(repo_root:Path,target_type:str='static')->dict[str,Any]: return build_node_index(repo_root) if target_type=='node' else build_static_index(repo_root)
