#!/usr/bin/env python3
"""Create and safely auto-merge PRs for approved development branches.

Runs only from the trusted default-branch controller. Pushed branch code is
never checked out or executed by this privileged path.
"""
from __future__ import annotations
import json, os, re, urllib.error, urllib.parse, urllib.request
from dataclasses import dataclass
from typing import Any
API="https://api.github.com"; GRAPHQL="https://api.github.com/graphql"
TOKEN=os.environ["GH_TOKEN"]; REPO=os.environ.get("REPO") or os.environ["GITHUB_REPOSITORY"]
DEFAULT_BRANCH=os.environ.get("DEFAULT_BRANCH","main"); REPAIR_APP_LOGIN=os.environ.get("REPAIR_APP_LOGIN","")
REQUIRED_WORKFLOWS=[x.strip() for x in os.environ.get("REQUIRED_WORKFLOWS","").split("|") if x.strip()]
MANAGED_LABEL="automation:branch-pr"; ALLOWED=("fix/","feat/","chore/","ci/","work/","codex/")
BLOCKING={"autonomy:human-hold","do-not-merge","do not merge","hold"}
BRANCH_RE=re.compile(r"^(fix|feat|chore|ci|work|codex)/[A-Za-z0-9._/-]+$")

@dataclass
class ApiError(RuntimeError): status:int; body:str

def log(s:str)->None: print(s,flush=True)
def req(method:str,path:str,data:Any|None=None,expected:tuple[int,...]=(200,))->Any:
    url=path if path.startswith("http") else API+path; raw=None if data is None else json.dumps(data).encode()
    r=urllib.request.Request(url,data=raw,method=method)
    r.add_header("Accept","application/vnd.github+json"); r.add_header("Authorization",f"Bearer {TOKEN}"); r.add_header("X-GitHub-Api-Version","2022-11-28")
    if raw is not None:r.add_header("Content-Type","application/json")
    try:
        with urllib.request.urlopen(r,timeout=30) as v:
            body=v.read()
            if v.status not in expected: raise ApiError(v.status,body.decode("utf-8","replace"))
            return None if not body else json.loads(body.decode())
    except urllib.error.HTTPError as e: raise ApiError(e.code,e.read().decode("utf-8","replace")) from e
def get(p:str)->Any:return req("GET",p)
def post(p:str,d:Any|None=None,e:tuple[int,...]=(200,201))->Any:return req("POST",p,d,e)
def put(p:str,d:Any|None=None,e:tuple[int,...]=(200,202))->Any:return req("PUT",p,d,e)
def gql(q:str,v:dict[str,Any])->Any:
    out=post(GRAPHQL,{"query":q,"variables":v},(200,))
    if out.get("errors"):raise RuntimeError("GitHub GraphQL error: "+json.dumps(out["errors"])[:1000])
    return out.get("data",{})
def event()->dict[str,Any]:
    p=os.environ.get("GITHUB_EVENT_PATH","")
    if not p:return {}
    with open(p,encoding="utf-8") as f:x=json.load(f)
    return x if isinstance(x,dict) else {}
def labs(pr:dict[str,Any])->set[str]:return {str(x.get("name","")).strip().lower() for x in pr.get("labels",[])}
def same_repo(pr:dict[str,Any])->bool:return str(pr.get("head",{}).get("repo",{}).get("full_name",""))==REPO
def allowed(b:str)->bool:return b!=DEFAULT_BRANCH and any(b.startswith(x) for x in ALLOWED) and ".." not in b and "//" not in b and not b.endswith("/") and BRANCH_RE.fullmatch(b) is not None

def ensure_label()->None:
    try:post(f"/repos/{REPO}/labels",{"name":MANAGED_LABEL,"color":"1D76DB","description":"PR created and managed by trusted branch automation"},(201,))
    except ApiError as e:
        if e.status!=422:raise
def open_prs()->list[dict[str,Any]]:
    out=[]
    for page in range(1,11):
        batch=get(f"/repos/{REPO}/pulls?state=open&per_page=100&page={page}");out.extend(batch)
        if len(batch)<100:return out
    raise RuntimeError("More than 1,000 open PRs; refusing incomplete reconciliation")
def exact(items:list[dict[str,Any]],b:str)->dict[str,Any]|None:
    hits=[p for p in items if same_repo(p) and p.get("head",{}).get("ref")==b and p.get("base",{}).get("ref")==DEFAULT_BRANCH]
    if len(hits)>1:raise RuntimeError(f"Multiple open PRs for {b} -> {DEFAULT_BRANCH}")
    return hits[0] if hits else None

def candidate()->tuple[str,str]|None:
    if os.environ.get("GITHUB_EVENT_NAME")!="workflow_run":return None
    ev=event();run=ev.get("workflow_run") or {}
    if ev.get("action")!="completed" or run.get("name")!="Branch PR signal" or run.get("event")!="push" or run.get("conclusion")!="success":return None
    b=str(run.get("head_branch") or "");sha=str(run.get("head_sha") or "")
    if str((run.get("head_repository") or {}).get("full_name",""))!=REPO or not allowed(b) or not re.fullmatch(r"[0-9a-f]{40}",sha):return None
    current=get(f"/repos/{REPO}/branches/{urllib.parse.quote(b,safe='')}")
    if str(current.get("commit",{}).get("sha",""))!=sha:return None
    cmp=get(f"/repos/{REPO}/compare/{urllib.parse.quote(DEFAULT_BRANCH,safe='')}...{urllib.parse.quote(b,safe='')}")
    return (b,sha) if int(cmp.get("ahead_by",0))>0 else None

def create_or_reuse()->None:
    c=candidate()
    if c is None:return
    b,sha=c;old=exact(open_prs(),b)
    if old:log(f"Using existing PR #{old['number']} for {b} -> {DEFAULT_BRANCH}.");return
    commit=get(f"/repos/{REPO}/commits/{sha}");msg=str(commit.get("commit",{}).get("message") or "").strip();first,_,rest=msg.partition("\n")
    title=(first.strip() or b.replace("/",": ",1))[:240]
    body="Created automatically by the repository's trusted branch automation.\n\n"+f"- Source branch: `{b}`\n- Target branch: `{DEFAULT_BRANCH}`\n- Signalled head: `{sha}`\n\n"+"Normal pull-request CI and security workflows must succeed before native GitHub auto-merge is requested."+(f"\n\n### Commit details\n\n{rest.strip()[:4000]}" if rest.strip() else "")
    try:new=post(f"/repos/{REPO}/pulls",{"title":title,"head":b,"base":DEFAULT_BRANCH,"body":body,"maintainer_can_modify":True},(201,))
    except ApiError as e:
        if e.status!=422:raise
        old=exact(open_prs(),b)
        if not old:raise
        log(f"Creation raced with another actor; using PR #{old['number']}.");return
    n=int(new["number"]);post(f"/repos/{REPO}/issues/{n}/labels",{"labels":[MANAGED_LABEL]},(200,));log(f"Created managed PR #{n} for {b}.")

def latest(sha:str)->dict[str,dict[str,Any]]:
    q=urllib.parse.urlencode({"head_sha":sha,"event":"pull_request","per_page":100});data=get(f"/repos/{REPO}/actions/runs?{q}");out={}
    for run in data.get("workflow_runs",[]):
        name=str(run.get("name", ""));old=out.get(name)
        if old is None or int(run.get("id",0))>int(old.get("id",0)):out[name]=run
    return out
def green(pr:dict[str,Any])->tuple[bool,str]:
    sha=str(pr.get("head",{}).get("sha",""));runs=latest(sha)
    for name in REQUIRED_WORKFLOWS:
        run=runs.get(name)
        if run is None:return False,f"{name!r} has not run on {sha[:12]}"
        if run.get("status")!="completed" or run.get("conclusion")!="success":return False,f"{name!r} is {run.get('status')}/{run.get('conclusion')}"
    return True,"required workflows succeeded"
def managed(pr:dict[str,Any])->bool:return pr.get("state")=="open" and not pr.get("draft") and same_repo(pr) and pr.get("base",{}).get("ref")==DEFAULT_BRANCH and allowed(str(pr.get("head",{}).get("ref",""))) and pr.get("user",{}).get("login")==REPAIR_APP_LOGIN and MANAGED_LABEL in labs(pr) and not labs(pr).intersection(BLOCKING)
def update_behind(pr:dict[str,Any])->bool:
    if pr.get("mergeable_state")!="behind":return False
    try:put(f"/repos/{REPO}/pulls/{pr['number']}/update-branch",{"expected_head_sha":pr.get("head",{}).get("sha")},(202,));log(f"Updated PR #{pr['number']} from {DEFAULT_BRANCH}; waiting for fresh CI.")
    except ApiError as e:
        if e.status not in (403,409,422):raise
        log(f"PR #{pr['number']} is behind but GitHub could not update it automatically ({e.status}).")
    return True
def enable(pr:dict[str,Any])->None:
    if pr.get("auto_merge"):return
    q="mutation($id:ID!){enablePullRequestAutoMerge(input:{pullRequestId:$id,mergeMethod:SQUASH}){pullRequest{number state autoMergeRequest{enabledAt}}}}";gql(q,{"id":pr["node_id"]});log(f"Requested native GitHub auto-merge for PR #{pr['number']} after exact-head checks passed.")
def reconcile()->None:
    for listed in open_prs():
        if not managed(listed):continue
        cur=get(f"/repos/{REPO}/pulls/{listed['number']}")
        if not managed(cur):continue
        if cur.get("mergeable_state")=="dirty" or cur.get("mergeable") is False:log(f"PR #{cur['number']} has merge conflicts; withholding auto-merge.");continue
        if update_behind(cur):continue
        ok,why=green(cur)
        if not ok:log(f"PR #{cur['number']} not ready: {why}.");continue
        sha=str(cur.get("head",{}).get("sha",""));cur=get(f"/repos/{REPO}/pulls/{cur['number']}")
        if not managed(cur) or str(cur.get("head",{}).get("sha",""))!=sha:continue
        if update_behind(cur):continue
        base=get(f"/repos/{REPO}/branches/{urllib.parse.quote(DEFAULT_BRANCH,safe='')}")
        if str(base.get("commit",{}).get("sha",""))!=str(cur.get("base",{}).get("sha","")):continue
        enable(cur)
def main()->int:
    if not REQUIRED_WORKFLOWS:raise RuntimeError("REQUIRED_WORKFLOWS is required")
    if not re.fullmatch(r"[A-Za-z0-9-]+\[bot\]",REPAIR_APP_LOGIN):raise RuntimeError("REPAIR_APP_LOGIN must identify the trusted GitHub App bot")
    ensure_label();create_or_reuse();reconcile();return 0
if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception as exc:print(f"::error::{exc}",flush=True);raise
