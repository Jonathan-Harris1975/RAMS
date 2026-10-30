import json
from repo_mgmt.model_defaults import restore_pinned_model_defaults


def test_restore_pinned_models_is_read_only_and_preserves_existing_routes(settings):
    class ReadOnlyR2:
        def object_exists(self, bucket, key):
            return True

        def get_object_limited(self, bucket, key, maximum):
            return json.dumps({"assignments": {"OPENROUTER_PRIMARY_MODEL": "approved/primary",
                "OPENROUTER_SECONDARY_MODEL": "approved/fallback"}, "premiumApprovals": {}, "decisions": {}}).encode()

    cfg = settings
    result = restore_pinned_model_defaults(cfg, ReadOnlyR2())
    assert result["restored"] is True
    assert cfg.openrouter_primary_model == "approved/primary"
    assert cfg.openrouter_secondary_model == "approved/fallback"
