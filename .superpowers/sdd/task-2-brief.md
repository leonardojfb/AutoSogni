### Task 2: Add the Campaign-tab action and load copied settings

Modify `app/ui/main_window.py` and add a focused regression test in `tests/test_ui_frame_preview.py`.

Task 1 adds `CampaignManager.clone_campaign_base(campaign_id: int) -> Campaign`. Add a button labelled `Usar como base` beside `Create Campaign`, save it as `self.use_campaign_as_base_button`, and connect it to `MainWindow._use_campaign_as_base()`.

The handler must require `self.current_campaign_id`. If none is selected, show a warning and return. Otherwise clone it, refresh the campaign selector, select the clone, and load its editable configuration into existing fields: name, frames folder, prompts source, output folder, filename template, organization, concurrency, model when present in the combo, duration mode/seconds, aspect ratio, sensitive-content checkbox, skip prompt processing, ordered LoRAs with strengths, and ordered R2V `reference_media` items. The clone remains empty: do not create jobs or call any remote API.

Write a UI test that constructs a source campaign with metadata/settings, calls `_use_campaign_as_base`, and asserts: button label, new selected ID, copied name/folder fields, copied settings, and zero jobs in the clone.

Run:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_ui_frame_preview.py -q
```

Do not touch `data/`, `errorlogs.txt`, `promptr2v.json`, or unrelated existing modifications. Do not commit; report your detailed result in `.superpowers/sdd/task-2-report.md` and return only status, changed paths, test summary, and concerns.
