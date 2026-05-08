from repo_mgmt import validator

def test_validator_wrapper_runs_commands(tmp_path):
    result=validator.run_commands(["printf 123"], tmp_path)
    assert result.passed is True and "123" in result.output_tail
