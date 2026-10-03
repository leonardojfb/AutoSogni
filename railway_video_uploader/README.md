# AutoSogni HTTPS media uploader

This service accepts authenticated multipart uploads and returns a public, signed HTTPS URL for BytePlus to fetch. Files are stored on a Railway volume mounted at `/data`; signed download URLs expire after 24 hours, and files older than seven days are cleaned up during a later upload.

Required Railway variable: `UPLOAD_TOKEN`. Attach a persistent volume at `/data` before using the upload endpoint. Railway's `RAILWAY_PUBLIC_DOMAIN` is used to build download URLs; `PUBLIC_BASE_URL` can override it.

Upload contract: `POST /upload`, Bearer token, multipart field `file`; response JSON contains `url` and `expires_at`. BytePlus downloads from `GET /files/{filename}` using the signed URL.