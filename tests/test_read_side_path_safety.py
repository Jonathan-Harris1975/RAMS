import pytest
from repo_mgmt.context_builder import load_context
from repo_mgmt.patch_protocol import PathTraversalError
def test_context_builder_blocks_sibling_prefix_traversal(tmp_path):
    repo=tmp_path/'repo'; repo.mkdir(); sib=tmp_path/'repo-secret'; sib.mkdir(); (sib/'secret.txt').write_text('secret')
    with pytest.raises(PathTraversalError): load_context(['../repo-secret/secret.txt'], repo)
