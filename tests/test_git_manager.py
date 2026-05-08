import subprocess, pytest
from repo_mgmt.git_manager import BranchSafetyError, GitManager
@pytest.mark.parametrize('branch',['main','master'])
def test_create_branch_blocks_from_protected_branch(tmp_path,branch):
    subprocess.run(['git','init','-b',branch],cwd=tmp_path,check=True,stdout=subprocess.PIPE); subprocess.run(['git','config','user.email','test@example.com'],cwd=tmp_path,check=True); subprocess.run(['git','config','user.name','Test'],cwd=tmp_path,check=True); (tmp_path/'README.md').write_text('hello'); subprocess.run(['git','add','README.md'],cwd=tmp_path,check=True); subprocess.run(['git','commit','-m','init'],cwd=tmp_path,check=True,stdout=subprocess.PIPE)
    with pytest.raises(BranchSafetyError): GitManager(tmp_path).create_branch('rms-qa/test')
