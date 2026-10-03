### Task 1: Clone campaign metadata

Modify `app/core/campaign_manager.py` and add a focused regression test in `tests/test_core_behaviors.py`.

Implement this exact interface:

```python
CampaignManager.clone_campaign_base(campaign_id: int) -> Campaign
```

It must read the source campaign and create a distinct campaign named `"<source name> (copia)"`, with status `READY`. Copy model ID/name, frames folder, prompt source, output folder, filename template, organization mode, concurrency, and `settings_json` with its exact semantic content. The new campaign must have zero frames, zero prompts, zero jobs, and no remote history. Do not mutate the source campaign or start a remote workflow.

Write a test that creates an R2V source campaign with `loras`, `loraStrengths`, and `reference_media` in settings; assert the clone has a new ID, the copied metadata/settings, zero jobs, and the source remains unchanged.

Run:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_core_behaviors.py -q
```

Do not touch `data/`, `errorlogs.txt`, `promptr2v.json`, or unrelated existing modifications. Do not commit; report your changed paths and exact test result in `.superpowers/sdd/task-1-report.md`, then return a concise status.
