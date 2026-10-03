# Campaign base design

## Goal

Let an operator create a new Sogni campaign from a previous campaign without changing the source campaign.

## User flow

1. Select a prior campaign in the existing campaign selector.
2. Click `Usar como base`.
3. AutoSogni creates a new campaign named `<original name> (copia)` and selects it.
4. The Campaign form loads the copied editable configuration. The operator can rename it, change model, LoRAs, references, prompts, folders, duration, aspect ratio, output folder, organization, and content-filter setting.
5. The campaign begins with no jobs. The operator clicks `Agregar jobs a campaña seleccionada` when ready.

## Copied data

Copy the campaign-level configuration: model ID and name, source folders, prompt source, output folder, filename template, organization mode, concurrency, and every value in `settings_json`, including LoRAs and R2V reference-media paths.

Do not copy job rows, job statuses, generated files, workflow IDs, artifact URLs, attempt counts, errors, or rendered prompts. A base campaign is a new intended generation, never a retry or recovery of its source.

## Implementation boundaries

Add a repository/manager operation that copies only campaign metadata into a new SQLite campaign row. Add a Campaign-tab button that calls it, loads the new campaign in the existing selector, and populates the form from its copied settings. Reuse the existing add-jobs flow; no remote workflow starts during duplication.

## Validation

Add focused coverage proving that duplication creates a distinct campaign, preserves all editable configuration including LoRAs/reference media, does not create jobs, and leaves the source campaign unchanged. Verify the UI exposes the button and selects the resulting campaign.
