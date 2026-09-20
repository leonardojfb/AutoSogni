# Sogni Video Automator — Implementation Specification for Codex

## 1. Objective

Build a local Windows desktop application that automates bulk image-to-video generation through the Sogni API.

The core workflow is:

1. User selects a folder containing multiple reference frames.
2. User imports a list/file containing multiple prompts.
3. User can manually edit a human-readable **Outfit Name** for every frame.
4. User can manually edit a human-readable **Prompt Name** for every prompt without changing the actual prompt text.
5. User selects the Sogni video model to use.
6. User configures the supported parameters for that model.
7. The application creates every `Frame × Prompt` combination.
8. Each combination becomes one persistent job.
9. Jobs are processed automatically.
10. For every job:
    - send frame + prompt to Sogni;
    - wait for the remote generation to complete;
    - download the generated video to the PC;
    - save it with a filename based on the Outfit Name and Prompt Name;
    - mark the job as completed.
11. The user can pause the automation.
12. The application must remember exactly where it stopped.
13. After restarting the program, the user can resume the campaign without regenerating already completed videos.

Example:

- 10 frames
- 12 prompts
- Result: 120 video generation jobs

The default execution mode should be sequential:

```text
Frame 01 + Prompt 01
↓
Generate
↓
Wait
↓
Download
↓
Frame 01 + Prompt 02
↓
Generate
↓
...
```

The architecture should support configurable concurrency later, but default concurrency must be `1`.

---

# 2. Target Platform

Primary target:

```text
Windows 10 / Windows 11
```

The application should eventually be distributable as a standalone `.exe`.

Recommended stack:

```text
Python 3.12+
PySide6
SQLite
httpx
Pydantic
Tenacity or custom retry system
PyInstaller
```

---

# 3. General Architecture

Use a modular architecture.

Recommended structure:

```text
sogni-video-automator/
│
├── app/
│   ├── main.py
│   │
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── campaign_view.py
│   │   ├── campaign_creator.py
│   │   ├── frame_table.py
│   │   ├── prompt_table.py
│   │   ├── job_table.py
│   │   ├── settings_view.py
│   │   └── model_settings_panel.py
│   │
│   ├── core/
│   │   ├── campaign_manager.py
│   │   ├── queue_manager.py
│   │   ├── job_runner.py
│   │   ├── recovery_manager.py
│   │   ├── downloader.py
│   │   ├── filename_builder.py
│   │   ├── validators.py
│   │   └── retry_policy.py
│   │
│   ├── sogni/
│   │   ├── client.py
│   │   ├── auth.py
│   │   ├── catalog.py
│   │   ├── uploads.py
│   │   ├── workflows.py
│   │   ├── events.py
│   │   └── schemas.py
│   │
│   ├── database/
│   │   ├── db.py
│   │   ├── models.py
│   │   ├── repositories.py
│   │   └── migrations.py
│   │
│   └── utils/
│       ├── paths.py
│       ├── hashing.py
│       ├── logging.py
│       └── time.py
│
├── data/
│   └── app.db
│
├── logs/
│
├── tests/
│
├── requirements.txt
└── README.md
```

Important rule:

> The UI must not contain Sogni API logic directly.

All remote operations should go through the Sogni client/service layer.

---

# 4. Sogni API

Base API:

```text
https://api.sogni.ai
```

Authentication:

```http
Authorization: Bearer SOGNI_API_KEY
```

Never hardcode the API key inside the source code.

Store it securely on Windows, preferably using Windows Credential Manager or another OS-backed secrets mechanism.

Reference documentation:

```text
https://docs.sogni.ai/api-reference/
```

---

# 5. Main Concepts

The application revolves around four primary entities:

```text
Campaign
Frame
Prompt
Job
```

## Campaign

A campaign is one complete batch generation session.

Example:

```text
Campaign:
Eve - September Batch 01

Frames:
10

Prompts:
12

Total Jobs:
120

Model:
WAN 2.2

Output:
D:\Eve\Videos\September Batch 01
```

Each campaign must persist across application restarts.

---

# 6. Frame / Outfit Management

The user selects a folder containing frames.

Supported local input extensions:

```text
.png
.jpg
.jpeg
.webp
```

Example folder:

```text
D:\Frames\Eve\
```

Example files:

```text
outfit_01_pink_dress.png
outfit_02_black_top.png
outfit_03_white_dress.png
```

After loading the folder, display an editable table:

| Frame | Outfit Name |
|---|---|
| `outfit_01_pink_dress.png` | `Pink Dress` |
| `outfit_02_black_top.png` | `Black Top` |
| `outfit_03_white_dress.png` | `White Dress` |

The application may derive a default Outfit Name from the filename, but the user must always be able to edit it manually.

Example:

```text
outfit_01_pink_dress.png
```

default:

```text
Pink Dress
```

The editable `outfit_name` is the value used for:

- display;
- organization;
- filenames;
- manifest records.

Do not use AI to guess the outfit.

---

# 7. Prompt Management

Prompts must have three separate concepts:

```text
Prompt ID
Prompt Name
Prompt Text
```

Example:

```text
Prompt ID:
P01

Prompt Name:
Quick Question

Prompt Text:
The woman looks directly at the camera...
```

The **Prompt Name** is editable and must never modify the real Prompt Text.

Display prompts in an editable table:

| ID | Prompt Name | Prompt Text |
|---|---|---|
| P01 | Quick Question | The woman looks directly at the camera... |
| P02 | Come Closer | She leans slightly toward the camera... |
| P03 | Hair Flip | She casually moves her hair... |

The user must be able to rename:

```text
Quick Question
```

to:

```text
Pick Me - Quick Question
```

without changing the actual prompt sent to Sogni.

---

# 8. Prompt Input Formats

Support:

```text
.json
.csv
.txt
```

Internally normalize every imported format to:

```json
[
  {
    "id": "P01",
    "name": "Quick Question",
    "text": "The woman looks directly at the camera..."
  },
  {
    "id": "P02",
    "name": "Come Closer",
    "text": "She leans slightly toward the camera..."
  }
]
```

For plain `.txt` input, allow one prompt per block or another clearly documented convention.

If no prompt names are supplied, create default names such as:

```text
Prompt 01
Prompt 02
Prompt 03
```

which the user can edit.

---

# 9. Frame × Prompt Matrix

Before any Sogni request is sent, create the complete job matrix.

Formula:

```text
number_of_frames × number_of_prompts = number_of_jobs
```

Example:

```text
10 frames × 12 prompts = 120 jobs
```

Example job ordering:

```text
JOB 001 = Frame 01 + Prompt 01
JOB 002 = Frame 01 + Prompt 02
JOB 003 = Frame 01 + Prompt 03
...
JOB 012 = Frame 01 + Prompt 12

JOB 013 = Frame 02 + Prompt 01
...
```

Every job must be inserted into SQLite before processing begins.

This is mandatory for reliable pause/resume and crash recovery.

---

# 10. Database

Use SQLite.

At minimum implement:

## campaigns

```text
id
name
status
model_id
model_name
frames_folder
prompts_source
output_folder
filename_template
organization_mode
concurrency
settings_json
created_at
updated_at
started_at
completed_at
```

## frames

```text
id
campaign_id
file_path
filename
outfit_name
sha256
created_at
```

## prompts

```text
id
campaign_id
prompt_code
prompt_name
prompt_text
created_at
```

## jobs

```text
id
campaign_id

frame_id
prompt_id

order_index

status

workflow_id
idempotency_key

artifact_url
output_file

attempt_count
last_error

created_at
started_at
remote_completed_at
downloaded_at
completed_at
updated_at
```

Optional:

## events

```text
id
job_id
event_type
message
payload_json
created_at
```

for detailed audit logs.

---

# 11. Campaign States

Suggested campaign states:

```text
DRAFT
READY
RUNNING
PAUSE_REQUESTED
PAUSED
COMPLETED
FAILED
CANCELLED
```

---

# 12. Job States

Use explicit job states.

Recommended:

```text
PENDING
PREPARING
UPLOADING_FRAME
SUBMITTING
QUEUED
GENERATING
COMPLETED_REMOTE
DOWNLOADING
DONE
RETRY_WAIT
FAILED
CANCELLED
```

Never use a vague boolean such as:

```text
completed = true / false
```

for the full execution state.

---

# 13. Model Selection

The user must be able to choose the Sogni video model.

Do not hardcode a single model.

Fetch the video model catalog dynamically from Sogni.

Use the current model catalog API documented by Sogni.

Conceptually:

```http
GET /v1/model-catalog?mediaType=video&include=parameters
```

Also support querying the selected model individually:

```http
GET /v1/model-catalog/:modelId
```

Use the returned metadata/schema to determine which parameters are supported.

The UI should display something like:

```text
Model
[ WAN 2.2 ▼ ]

WAN 2.2
Seedance 2
Seedance 2 Mini
LTX
...
```

Do not assume model IDs remain permanent forever.

Store both:

```text
model_id
model_name
```

inside each campaign.

---

# 14. Dynamic Model Parameters

Do not build a fixed settings panel that assumes every model supports the same fields.

Instead:

```text
User selects model
↓
Fetch model schema
↓
Build supported settings dynamically
```

Possible settings could include:

```text
duration
resolution
aspect ratio
fps
seed
guidance
quality
reference image
reference video
audio options
```

but only show fields supported by the selected model.

Validate all fields before Start is enabled.

---

# 15. Validate Image-to-Video Support

Before allowing the campaign to start:

- verify that the selected model supports the required reference/input mode;
- verify that the selected model accepts an initial/reference frame;
- verify required fields;
- validate parameter ranges.

If the model cannot perform the requested workflow, show a clear blocking error:

```text
The selected model does not support the required image-to-video input.
```

---

# 16. Generation Workflow

Each job should normally correspond to one independent Sogni Creative Workflow.

Conceptually:

```text
Frame
+
Prompt
+
Selected Model
+
Model Parameters
↓
Sogni generate_video workflow
```

Use the current Sogni workflow API.

Generation should be encapsulated inside:

```text
SogniWorkflowService
```

not directly inside the UI.

---

# 17. Upload Strategy

Reference frames should be uploaded through the appropriate Sogni media/image upload flow.

Important optimization:

> Do not unnecessarily re-upload the same local frame for every prompt if Sogni allows the uploaded asset/reference URL to be safely reused.

Implement a cache keyed by local frame hash:

```text
sha256(frame_file)
```

Store any reusable remote upload metadata.

If the remote reference expires or is invalid, re-upload.

This optimization should be isolated so it can be disabled if Sogni requires a fresh upload per workflow.

---

# 18. Idempotency

Every job must have a deterministic idempotency key.

Example:

```text
campaign_17_job_0048
```

or better:

```text
sva:{campaign_uuid}:{job_uuid}
```

Send it using Sogni's supported idempotency mechanism.

Purpose:

```text
Request sent
↓
Sogni accepted it
↓
Network connection dies
↓
Application does not know whether submission succeeded
```

The application must avoid creating a duplicate video generation when retrying the submission.

Persist the idempotency key before submission.

---

# 19. Workflow ID

Immediately after Sogni returns a workflow identifier:

```text
workflow_id
```

save it in SQLite before performing other work.

Example:

```text
JOB 048

status:
QUEUED

workflow_id:
wf_abc123
```

The application must be able to recover from that ID later.

---

# 20. Monitoring Sogni

Prefer Sogni workflow events / SSE for live updates when available.

Conceptual flow:

```text
Submit Workflow
↓
Save workflow_id
↓
Connect to event stream
↓
Receive worker / queue / generation events
↓
Completed
```

Display friendly statuses in the UI:

```text
Searching worker...
Worker assigned
Queued
Generating
Finalizing
Downloading
Done
```

Do not tightly couple user-facing status labels to raw provider strings.

---

# 21. Polling Fallback

Never rely exclusively on SSE.

If the event stream disconnects:

```text
SSE disconnected
↓
query workflow status
↓
reconnect or continue polling
```

The job must not be marked failed solely because SSE disconnected.

Provide a polling fallback through the current workflow status endpoint.

---

# 22. Remote Completion

When Sogni reports the workflow as complete:

1. retrieve the generated video artifact;
2. store the artifact URL;
3. mark the job:

```text
COMPLETED_REMOTE
```

Only then start downloading.

---

# 23. Downloading

Every completed video must automatically be downloaded to the user's configured local output directory.

Recommended safe procedure:

```text
download URL
↓
write to temporary file:
video_name.mp4.part
↓
verify request succeeded
↓
verify file exists and size > 0
↓
rename atomically to:
video_name.mp4
↓
mark DONE
```

Never mark a job as `DONE` until the final local file exists successfully.

---

# 24. Download Failure Must Not Regenerate

Critical rule:

If Sogni successfully generated the video but downloading fails:

```text
COMPLETED_REMOTE
↓
DOWNLOAD FAILED
```

do not regenerate the video.

Retry only the download.

The original artifact/workflow must be reused whenever possible.

---

# 25. Filename System

Generated videos must be named using:

- Outfit Name;
- Prompt ID;
- Prompt Name;
- optionally Model;
- optionally Job Number.

Default filename template:

```text
{outfit}__{prompt_id}_{prompt_name}.mp4
```

Example:

```text
Pink_Dress__P01_Quick_Question.mp4
Pink_Dress__P02_Come_Closer.mp4
Pink_Dress__P03_Hair_Flip.mp4
```

Optional template:

```text
{job_number}__{outfit}__{prompt_name}__{model}.mp4
```

Example:

```text
0047__Pink_Dress__Quick_Question__WAN22.mp4
```

---

# 26. Editable Filename Template

Allow the user to choose or edit the filename pattern.

Supported variables should include:

```text
{job_number}
{outfit}
{frame_name}
{prompt_id}
{prompt_name}
{model}
{campaign}
```

Provide a live preview.

Example:

```text
Template:
{outfit}__{prompt_id}_{prompt_name}.mp4

Preview:
Pink_Dress__P01_Quick_Question.mp4
```

---

# 27. Filename Sanitization

Windows does not allow these characters:

```text
< > : " / \ | ? *
```

Sanitize all filenames.

Also:

- trim leading/trailing spaces;
- avoid trailing dots;
- limit filename length;
- replace repeated separators;
- handle duplicate filenames.

If a collision occurs, append something deterministic:

```text
Pink_Dress__P01_Quick_Question__02.mp4
```

or use the job number.

---

# 28. Output Organization

Support at least two modes.

## Flat

```text
Output/
├── Pink_Dress__P01_Quick_Question.mp4
├── Pink_Dress__P02_Come_Closer.mp4
├── Black_Top__P01_Quick_Question.mp4
└── ...
```

## Group by Outfit

Recommended default:

```text
Output/
│
├── Pink Dress/
│   ├── P01 - Quick Question.mp4
│   ├── P02 - Come Closer.mp4
│   └── ...
│
├── Black Top/
│   ├── P01 - Quick Question.mp4
│   ├── P02 - Come Closer.mp4
│   └── ...
│
└── White Dress/
```

Optional future modes:

```text
Group by Prompt
Group by Model
Group by Campaign
```

---

# 29. Sequential Queue

Default behavior:

```text
concurrency = 1
```

Meaning:

```text
Job A
↓
remote generation completed
↓
local download completed
↓
mark DONE
↓
Job B
```

Do not submit the next job until the current job is safely completed locally unless concurrency is explicitly increased.

---

# 30. Future Concurrency

Architect the queue manager so it can later support:

```text
1
2
3
```

parallel jobs without major refactoring.

Concurrency must be configurable but default to:

```text
1
```

Respect Sogni's active workflow limits and handle `409` / capacity conditions safely.

---

# 31. Pause

Implement **Soft Pause**.

When the user clicks:

```text
PAUSE
```

set:

```text
campaign.status = PAUSE_REQUESTED
```

Do not automatically cancel the currently generating remote workflow.

Instead:

```text
current job finishes
↓
video downloads
↓
job becomes DONE
↓
campaign becomes PAUSED
↓
do not start next job
```

This is the default pause behavior.

---

# 32. Resume

When the user clicks:

```text
RESUME
```

the application should:

1. run recovery checks;
2. find the first unfinished job;
3. continue from that point.

Never regenerate completed jobs.

---

# 33. Stop / Cancel

Keep this separate from Pause.

Possible buttons:

```text
Pause
Resume
Stop After Current
Cancel Current
Cancel Campaign
```

`Cancel Current` may invoke the Sogni workflow cancellation endpoint.

Do not use remote cancellation as the standard Pause implementation.

---

# 34. Crash Recovery

The program must recover correctly if:

- application crashes;
- Windows restarts;
- internet connection drops;
- user closes the app;
- PC sleeps;
- SSE disconnects.

On startup, inspect campaigns/jobs with non-terminal states.

Example:

```text
JOB 48
status = GENERATING
workflow_id = wf_abc123
```

The recovery manager must query Sogni.

Possible outcomes:

## Remote workflow completed

```text
download artifact
mark DONE
continue
```

## Remote workflow still running

```text
reattach monitoring
```

## Remote workflow failed

```text
mark FAILED or schedule retry
```

## No workflow ID exists

The job may safely return to a pre-submission state depending on its saved stage.

Do not blindly resubmit every interrupted job.

---

# 35. Resume Logic

Do not store only:

```text
current_job = 48
```

Instead derive progress from persisted jobs.

Conceptually:

```sql
SELECT *
FROM jobs
WHERE status NOT IN ('DONE', 'CANCELLED')
ORDER BY order_index
LIMIT 1;
```

This makes the system resilient even if jobs fail out of order later.

---

# 36. Retry Policy

Differentiate between:

## Recoverable errors

Examples:

```text
network timeout
connection reset
429
temporary 5xx
worker unavailable
SSE disconnect
download timeout
temporary storage problem
```

Use:

```text
RETRY_WAIT
```

with exponential backoff.

Example:

```text
10 sec
30 sec
60 sec
120 sec
```

## Permanent / configuration errors

Examples:

```text
invalid model parameter
unsupported input
invalid API key
corrupted local frame
invalid prompt payload
```

Mark:

```text
FAILED
```

and continue to next job if configured.

---

# 37. Configurable Retry Settings

Settings UI:

```text
Retry Failed Generations: [✓]

Maximum Attempts:
[ 3 ]

Initial Delay:
[ 10 seconds ]

Maximum Delay:
[ 120 seconds ]
```

Generation retries and download retries should be tracked separately if practical.

---

# 38. Manual Job Actions

From the job table, support context actions:

```text
Retry
Retry Download
Retry With Different Model
View Prompt
Open Frame
Open Output File
Open Output Folder
Copy Workflow ID
Mark Pending
Skip
```

Be careful with destructive status overrides.

---

# 39. Main UI

Recommended layout:

```text
┌────────────────────────────────────────────────────────────┐
│ SOGNI VIDEO AUTOMATOR                                      │
├────────────────────────────────────────────────────────────┤
│ Campaign: Eve - September Batch 01                         │
│                                                            │
│ Frames Folder                                              │
│ D:\Eve\Frames                              [Browse]         │
│ 10 frames detected                                         │
│                                                            │
│ Prompts                                                    │
│ prompts.json                               [Browse]         │
│ 12 prompts detected                                        │
│                                                            │
│ Model                                                      │
│ [ WAN 2.2 ▼ ]                                              │
│                                                            │
│ Model Settings                                             │
│ [dynamic fields]                                           │
│                                                            │
│ Output Folder                                              │
│ D:\Eve\Output                              [Browse]         │
│                                                            │
│ Total generations: 120                                     │
│                                                            │
│ Completed: 47                                              │
│ Active: 1                                                  │
│ Failed: 0                                                  │
│ Pending: 72                                                │
│                                                            │
│ █████████████████░░░░░░░░░░░░░ 39.2%                      │
│                                                            │
│ [ START ] [ PAUSE ] [ RESUME ] [ STOP ]                   │
└────────────────────────────────────────────────────────────┘
```

---

# 40. Frame Editing UI

Provide a dedicated editable table:

| # | Frame | Outfit Name | Status |
|---:|---|---|---|
| 1 | outfit_01_pink_dress.png | Pink Dress | Ready |
| 2 | outfit_02_black_top.png | Black Top | Ready |
| 3 | outfit_03_white_dress.png | White Dress | Ready |

Features:

```text
Edit Outfit Name
Preview image
Open file
Remove frame
Reload folder
Bulk rename if needed
```

---

# 41. Prompt Editing UI

Provide a dedicated editable table:

| ID | Prompt Name | Prompt |
|---|---|---|
| P01 | Quick Question | The woman looks directly... |
| P02 | Come Closer | She leans slightly... |
| P03 | Hair Flip | She casually moves... |

Features:

```text
Edit Prompt Name
Edit Prompt Text
Add Prompt
Duplicate Prompt
Delete Prompt
Reorder Prompt
Import
Export
```

Prompt order affects job order.

---

# 42. Job Table

Display:

| # | Outfit | Prompt | Model | Status | Attempts |
|---:|---|---|---|---|---:|
| 46 | Pink Dress | Hair Flip | WAN22 | Done | 1 |
| 47 | Pink Dress | Come Closer | WAN22 | Done | 1 |
| 48 | Pink Dress | Quick Question | WAN22 | Generating | 1 |
| 49 | Black Dress | Hair Flip | WAN22 | Pending | 0 |

Allow filtering by:

```text
status
outfit
prompt
model
failed only
pending only
completed only
```

---

# 43. Current Job Panel

Show:

```text
CURRENT JOB

Job:
48 / 120

Outfit:
Pink Dress

Prompt:
P07 - Pick Me Question

Model:
WAN 2.2

Workflow ID:
wf_...

Status:
Generating

Attempts:
1
```

Also show:

- frame thumbnail;
- full prompt;
- output filename preview.

---

# 44. Progress Information

Show:

```text
47 / 120
39.2%
```

Also:

```text
Completed: 47
Failed: 1
Pending: 71
Active: 1
```

Track:

```text
campaign start time
elapsed time
average job duration
estimated remaining time
```

ETA is informational only.

---

# 45. Settings

Settings should include:

## API

```text
Sogni API Key
Test Connection
Connection Status
```

## Queue

```text
Concurrency
Retry Count
Retry Backoff
Auto Resume
```

## Output

```text
Default Output Directory
Folder Organization
Filename Template
Overwrite Existing Files
```

Default overwrite behavior should be:

```text
OFF
```

## Models

```text
Refresh Catalog
Show unavailable models
```

---

# 46. Test Connection

Implement a button:

```text
Test Connection
```

Validate:

```text
API connectivity
authentication
model catalog access
optional account/balance endpoint
```

Display:

```text
Connected
Authentication failed
Network unavailable
```

Do not expose full API secrets in logs.

---

# 47. Preflight Validation

Before enabling Start, run:

```text
✓ API Key valid
✓ Frames folder exists
✓ All frames readable
✓ Prompt source valid
✓ At least 1 prompt
✓ At least 1 frame
✓ Job matrix built
✓ Output folder writable
✓ Model currently available
✓ Required image-to-video mode supported
✓ Model parameters valid
✓ Filename template valid
✓ No impossible path lengths
```

If any blocking error exists:

```text
START
```

must remain disabled.

---

# 48. Campaign Creation Flow

Recommended UX:

```text
New Campaign
↓
Campaign Name
↓
Select Frames Folder
↓
Review / Edit Outfit Names
↓
Import Prompts
↓
Review / Edit Prompt Names
↓
Select Model
↓
Configure Model
↓
Choose Output Folder
↓
Choose Filename Template
↓
Preview:
10 × 12 = 120 videos
↓
Create Campaign
↓
Persist all jobs
↓
Ready to Start
```

---

# 49. Manifest Export

Create:

```text
campaign_manifest.json
```

inside the campaign output directory.

Example:

```json
{
  "campaign": "Eve September Batch",
  "model": {
    "id": "wan22",
    "name": "WAN 2.2"
  },
  "frames": 10,
  "prompts": 12,
  "total_jobs": 120,
  "jobs": [
    {
      "job_number": 1,
      "outfit": "Pink Dress",
      "frame": "outfit_01_pink_dress.png",
      "prompt_id": "P01",
      "prompt_name": "Quick Question",
      "output_file": "Pink_Dress__P01_Quick_Question.mp4",
      "status": "DONE",
      "workflow_id": "wf_..."
    }
  ]
}
```

Update/export it at useful checkpoints and campaign completion.

---

# 50. Logging

Use structured logs.

Example:

```text
2026-09-14 14:03:12 INFO Campaign 17 started
2026-09-14 14:03:12 INFO Job 48 preparing
2026-09-14 14:03:13 INFO Uploading frame
2026-09-14 14:03:16 INFO Workflow submitted wf_abc123
2026-09-14 14:05:42 INFO Remote generation completed
2026-09-14 14:05:43 INFO Downloading artifact
2026-09-14 14:05:58 INFO Job 48 done
```

Never log:

```text
full API key
Authorization header
sensitive secrets
```

---

# 51. Queue Manager Responsibilities

`QueueManager` should:

```text
start campaign
pause campaign
resume campaign
select next job
respect concurrency
avoid duplicate active jobs
coordinate JobRunner
update campaign statistics
stop cleanly
```

It should not contain UI code.

---

# 52. Job Runner Responsibilities

`JobRunner` handles one job lifecycle:

```text
prepare
upload/reuse frame
submit workflow
save workflow ID
monitor remote workflow
obtain artifact URL
download artifact
validate output
mark job done
```

Make this testable independently from the UI.

---

# 53. Recovery Manager Responsibilities

`RecoveryManager` should run:

```text
on startup
before campaign resume
after unexpected connection failure
```

Responsibilities:

```text
inspect interrupted jobs
query existing remote workflows
reattach monitoring
recover completed artifacts
avoid duplicate generation
repair safe transient states
```

---

# 54. Filename Builder Responsibilities

`FilenameBuilder` should:

```text
resolve variables
sanitize Windows filename
enforce safe length
handle collisions
build folder path
provide preview
```

Inputs:

```text
campaign
frame
outfit_name
prompt
model
job_number
```

---

# 55. Downloader Responsibilities

`Downloader` should:

```text
stream large files
write .part file
support timeouts
retry
verify non-empty result
rename atomically
avoid duplicate download
```

If destination already exists and job metadata says it belongs to that job, validate and mark done instead of downloading again.

---

# 56. Threading / Async

The UI must never freeze during:

```text
uploads
Sogni requests
SSE
polling
downloads
hashing
```

Use an appropriate PySide6-safe strategy:

```text
QThread
QThreadPool
async event loop integration
```

Choose one architecture and use it consistently.

Never perform long blocking HTTP requests on the main Qt UI thread.

---

# 57. Error UX

Errors should be understandable.

Bad:

```text
HTTP 422
```

Better:

```text
Job 37 failed.

Model:
WAN 2.2

Reason:
The selected duration is not supported by this model.

Action:
Edit campaign settings or retry with another model.
```

Keep the raw provider error available in a details panel/log.

---

# 58. Duplicate Protection

Protect against:

- duplicate Start clicks;
- restart during submission;
- network retries;
- duplicate downloads;
- accidental multiple JobRunner instances for same job.

Use:

```text
database state
idempotency key
transactional job claiming
workflow ID
file existence checks
```

---

# 59. Transactional Job Claiming

When a worker selects a pending job, claim it atomically.

Conceptually:

```sql
BEGIN IMMEDIATE;

SELECT next pending job;

UPDATE jobs
SET status = 'PREPARING'
WHERE id = ? AND status = 'PENDING';

COMMIT;
```

This prepares the codebase for concurrency.

---

# 60. Output Example

With:

```text
10 outfits
12 prompts
```

result:

```text
120 MP4 files
```

Example:

```text
EVE - BATCH 0914/
│
├── Pink Dress/
│   ├── P01 - Quick Question.mp4
│   ├── P02 - Come Closer.mp4
│   ├── P03 - Hair Flip.mp4
│   └── ...
│
├── Black Dress/
│   ├── P01 - Quick Question.mp4
│   ├── P02 - Come Closer.mp4
│   └── ...
│
├── Blue Top/
│   └── ...
│
└── campaign_manifest.json
```

---

# 61. Implementation Phases

## Phase 1 — Foundation

Implement:

```text
project structure
configuration
SQLite
logging
API key settings
Sogni base client
```

Acceptance criteria:

```text
app opens
database initializes
API key can be saved
connection can be tested
```

---

## Phase 2 — Frames and Prompts

Implement:

```text
frame folder import
frame table
editable Outfit Name
prompt import
prompt table
editable Prompt Name
prompt text editor
```

Acceptance criteria:

```text
frames can be loaded
prompts can be loaded
names can be edited
changes persist
```

---

## Phase 3 — Campaign and Matrix

Implement:

```text
campaign creation
Frame × Prompt expansion
job persistence
job ordering
job count preview
```

Acceptance criteria:

```text
10 frames + 12 prompts creates exactly 120 jobs
no Sogni request is made yet
all jobs survive app restart
```

---

## Phase 4 — Sogni Model Catalog

Implement:

```text
model catalog
video model filter
model selector
model detail query
dynamic settings schema
input compatibility validation
```

Acceptance criteria:

```text
models are fetched dynamically
selected model parameters render correctly
invalid configurations are blocked
```

---

## Phase 5 — Single Job End-to-End

Implement:

```text
frame upload
workflow creation
idempotency
workflow ID persistence
monitoring
artifact URL extraction
download
filename builder
DONE state
```

Acceptance criteria:

```text
one job can generate one video end-to-end
final MP4 appears locally
job is marked DONE only after download
```

---

## Phase 6 — Sequential Queue

Implement:

```text
QueueManager
automatic next job
campaign progress
retry policy
```

Acceptance criteria:

```text
multiple jobs run automatically one after another
no manual intervention is needed
```

---

## Phase 7 — Pause / Resume / Recovery

Implement:

```text
soft pause
resume
restart recovery
existing workflow recovery
download recovery
```

Acceptance criteria:

```text
app can be closed mid-campaign
after reopening it knows exactly what remains
no completed job is regenerated
```

---

## Phase 8 — Complete UI

Implement:

```text
campaign dashboard
frame table
prompt table
job table
current job panel
progress bar
logs
settings
manual retry actions
```

---

## Phase 9 — Packaging

Implement:

```text
PyInstaller
Windows executable
config/data directories
migration strategy
release build
```

---

# 62. QA Scenarios

Test the exact same production workflow with increasing batch sizes.

## Test A

```text
2 frames × 2 prompts = 4 jobs
```

Validate:

```text
ordering
filenames
downloads
SQLite state
```

## Test B

```text
5 frames × 3 prompts = 15 jobs
```

Validate:

```text
pause
resume
retry
```

## Test C

```text
10 frames × 12 prompts = 120 jobs
```

Validate:

```text
long-run stability
memory usage
network recovery
crash recovery
download integrity
```

These are not separate versions of the application.

They are QA runs of the same implementation.

---

# 63. Critical Test Cases

Explicitly test:

```text
close app while generating
close app while downloading
lose internet while uploading
lose internet during SSE
Sogni returns temporary 5xx
Sogni returns 429
Sogni returns 409
invalid model parameters
frame missing after campaign creation
output folder unavailable
output file already exists
artifact download interrupted
user pauses while job is generating
user resumes after restart
duplicate Start click
```

---

# 64. Non-Negotiable Rules

Codex must follow these rules:

1. Do not hardcode WAN 2.2 as the only model.
2. Fetch available video models dynamically from Sogni.
3. Outfit Name must be editable per frame.
4. Prompt Name must be editable per prompt.
5. Prompt Name and Prompt Text are separate fields.
6. Every Frame × Prompt combination must be persisted before execution.
7. Every job must have an idempotency key.
8. Save workflow IDs immediately.
9. A failed download must never trigger a new generation automatically.
10. `Pause` must default to soft pause, not remote cancellation.
11. Completed jobs must never be regenerated on resume.
12. Recovery must query existing remote workflow state whenever possible.
13. The UI must stay responsive.
14. Long-running HTTP work must not run on the UI thread.
15. Never expose the API key in logs.
16. Always sanitize Windows filenames.
17. The application must survive restarts without losing campaign state.
18. Model-specific parameters must be validated dynamically.
19. Start must be blocked until preflight validation passes.
20. Default queue concurrency is 1.

---

# 65. Recommended Initial UX

The first usable production flow should be:

```text
Open app
↓
New Campaign
↓
Choose frames folder
↓
Edit Outfit Names
↓
Import prompts
↓
Edit Prompt Names
↓
Choose Sogni model
↓
Configure model
↓
Choose output folder
↓
Preview filenames
↓
See:
10 × 12 = 120 videos
↓
Create
↓
Start
↓
Automatic generation
↓
Automatic download
↓
Pause / Resume whenever needed
```

---

# 66. Definition of Done

The application is considered functionally complete when the following scenario works reliably:

```text
User creates a campaign with:

10 frames
12 prompts

The app creates:
120 persistent jobs

The user selects:
WAN 2.2 or another compatible model

The user clicks:
START

The app automatically:

generates Job 1
waits
downloads Job 1
names it correctly
generates Job 2
waits
downloads Job 2
...
continues automatically

The user clicks PAUSE.

The current job finishes and downloads.
No new job starts.

The user closes the application.

Later the user reopens it.

The campaign still shows the same:
completed
pending
failed
active state

The user clicks RESUME.

Generation continues from the correct point.

No completed video is regenerated.
```

---

# 67. Reference

Sogni API documentation:

```text
https://docs.sogni.ai/api-reference/
```

When implementing API-specific payloads, model schemas, uploads, workflow events, cancellation, or artifact extraction, always verify against the current Sogni API documentation rather than guessing field names.

The application architecture should isolate Sogni-specific behavior inside `app/sogni/` so future API changes do not require rewriting campaign, queue, database, or UI logic.
