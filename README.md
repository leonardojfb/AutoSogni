# Sogni Video Automator

Local Windows desktop app for persistent Frame x Prompt image-to-video campaigns through the Sogni API, plus a standalone WaveSpeedAI Wan 3.0 reference-to-video tab.

## Run

```powershell
py -m pip install -r requirements.txt
py -m app.main
```

Set `SOGNI_API_KEY` or save it in the Settings tab. The app stores local state in `data/app.db`.

## Recover an older campaign

Use `RecuperarCampana.bat` for the interactive recovery utility, or run
`python scripts/recover_campaign.py --help` for command-line options. Provide
the original frame folder, prompt source file, and output folder. The tool
previews matches without changing the database; in interactive mode, type
`RECUPERAR` to apply. It marks uniquely matched videos as `DONE`, leaves
unmatched jobs `PENDING`, and creates a SQLite backup before writing. Supply
the original model ID/name and settings JSON so pending jobs can be resumed
with the intended configuration. For command-line use, settings can be passed
as `--settings-json` or read from a UTF-8 file with `--settings-file`.
The standalone `dist\RecuperarCampana.exe` runs without a Python installation.
Close AutoSogni before applying recovery to avoid concurrent database changes.

## Current Flow

1. Fetch video models from Sogni.
2. Select a frames folder (`.png`, `.jpg`, `.jpeg`, `.webp`).
3. Import prompts from `.json`, `.csv`, or `.txt`.
4. Create a campaign. All Frame x Prompt jobs are persisted before execution.
5. Choose duration mode: manual seconds or automatic detection from each prompt.
6. Choose the video format/aspect ratio, such as `9:16`, `16:9`, `1:1`, or `4:5`.
7. Start/resume the queue. Default concurrency is 1.
8. Soft pause sets the campaign to `PAUSE_REQUESTED`; the active job is allowed to finish.

For MiniMax H3 image-to-video campaigns, choose an H3 I2V model and open
`Browse LoRAs`. The catalog opens immediately and loads LoRAs compatible with
that exact model. Set each strength and order up to 8 selections; the stack is
saved with the new campaign. Turning off the sensitive-content filter also
shows `My LoRAs` and the import form. Personal imports require rights
confirmation and can remain queued until Sogni marks them ready. Trigger words
must be included in the prompt you supply; the app does not change prompt text.
Existing campaigns retain their saved settings.

On the Campaign tab, `Agregar jobs a campaña seleccionada` appends new frame
and prompt combinations to the selected campaign. `Cola Sogni` lists its jobs
and statuses; repeating an identical frame and prompt combination does not
create another job.

## WaveSpeedAI tab

The `WaveSpeed` tab directly integrates `alibaba/wan-3.0/reference-to-video`.
It supports local image/video/audio uploads, all documented model parameters,
advanced sync/Base64/webhook controls, price and balance checks, async polling,
output download, task inspection/deletion, and a sanitized local history. It is
intentionally independent from the Sogni campaign queue.

The WaveSpeed queue captures one job per click on `Agregar a la cola`, including
the current prompt, all selected image/video/audio references, model, and
generation settings. Remove the references from the editor, prepare the next
job, and add it separately. The table shows each job's references, price, task
ID, and result. Jobs added while a queue is running are submitted in the next
batch automatically. Existing rows cannot be edited during a run.

`Secuencial` waits for each task to finish before submitting the next;
`Paralelo` submits pending jobs together and polls their results concurrently.
Failed rows do not stop later jobs and expose `Reintentar`. Use `Estimar costos`
to see per-job and total prices before submitting. Queue runs use asynchronous
polling, so webhook, sync mode, and Base64 output are disabled for that workflow.
API keys and temporary upload URLs are not stored in campaign files.
Older campaigns with a shared frame and one video per row remain readable.
Queues are saved as named WaveSpeed campaigns in
`data/wavespeed_campaigns.json`. The campaign selector restores the last active
campaign on startup, and the previous single-queue file is migrated
automatically the first time this feature is opened.

## Venice

Open the root `Venice` tab. Enter a Venice API key there or set
`VENICE_API_KEY`, then click `Refresh models` to load the verified edit-model
catalog. Add 1–3 reference images in the order they should be sent, enter a
prompt, and click `Generate` to use the synchronous Venice image-edit endpoint.
Saved images go to the selected output folder (by default,
`data/venice_outputs`), and sanitized local history is kept in
`data/venice_history.json`. The key saved from this tab is stored separately in
`data/venice_api_key.txt`. Tests do not perform paid image generation.

The `Batch` page processes a folder with one `Process folder` action. Choose the
source folder, the face/hair reference first and body reference second, an
output parent, and edit the master prompt (or load it from a TXT file). Each
photo is sent to DeepSeek V4.1 Flash Uncensored (the editable default vision model)
for an individual prompt, then to Qwen Image
2.1 Pro Edit with those two identity references; the generated image and its
`.prompt.txt` are saved together in a unique run folder. A batch makes two
Venice calls per photo and does not pause for prompt review.

## Sogni API Notes

The integration uses the current Sogni docs as of 2026-09-14:

- Model catalog: `GET /v1/model-catalog?mediaType=video&include=parameters`
- Workflow start: `POST /v1/creative-agent/workflows`
- Idempotency header: `Idempotency-Key`
- Workflow read/polling: `GET /v1/creative-agent/workflows/:id`
- Image pre-upload: `GET /v2/image/uploadUrl`
- Public H3 LoRAs: `GET /v1/loras/comfy?modelId=...`
- Personal LoRAs: `GET /v1/loras/personal/catalog`, `POST /v1/loras/personal`

API-specific behavior is isolated in `app/sogni/`.
