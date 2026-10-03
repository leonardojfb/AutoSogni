# Task 1 report: Clone campaign metadata

## Status
Implemented `CampaignManager.clone_campaign_base(campaign_id: int) -> Campaign`.

## Changed paths
- `app/core/campaign_manager.py`: reads the source campaign and inserts a READY metadata-only clone named `<source name> (copia)`, preserving all requested fields and the stored `settings_json` verbatim.
- `tests/test_core_behaviors.py`: adds an R2V regression test covering LoRAs, strengths, reference media, metadata copying, empty work-item collections, no campaign timestamps, and unchanged source state.

## Test result
Command: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_core_behaviors.py -q`
Result: `38 passed in 1.35s` (exit code 0).

## Concerns
The shared checkout already contained unrelated modifications in other files and runtime data. They were left untouched. No remote workflow is started by this method.

## Isolation follow-up
Updated the regression fixture to seed the R2V campaign and its source frame, prompt, and job directly through `CampaignRepository`; the test no longer invokes R2V campaign creation.

Re-run command: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_core_behaviors.py -q`
Exact result: `38 passed in 1.17s` (exit code 0).

## Isolation follow-up 2
Removed the optional `reference_media` argument from the seeded job so the regression test uses only the repository's base job insertion interface. R2V reference settings remain covered in the source and clone assertions.

Re-run command: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_core_behaviors.py -q`
Exact result: `38 passed in 1.16s` (exit code 0).
