"""Bounded Slack file ingestion and cached local attachment preparation."""

import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from werkzeug.utils import secure_filename
from harness.scripts.attachments import SUPPORTED_EXTENSIONS, prepare_attachment_context

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 10


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class FileError(ValueError):
    pass


def download_private(url, token, *, max_bytes=MAX_FILE_BYTES):
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(4):
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or not (
                host == "slack.com"
                or host.endswith(".slack.com")
                or host.endswith(".slack-files.com")
                or host.endswith(".slack-edge.com")
            )
        ):
            raise FileError("Slack returned an unsupported file host.")
        headers = (
            {"Authorization": "Bearer " + token}
            if host == "slack.com" or host.endswith(".slack.com")
            else {}
        )
        try:
            with opener.open(
                urllib.request.Request(url, headers=headers), timeout=30
            ) as response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise FileError("File exceeds the 25 MiB limit.")
                if not data:
                    raise FileError("The uploaded file is empty.")
                return data
        except urllib.error.HTTPError as error:
            if error.code in (301, 302, 303, 307, 308) and error.headers.get(
                "Location"
            ):
                url = urllib.parse.urljoin(url, error.headers["Location"])
            else:
                raise FileError(
                    "The bot could not download that private Slack file."
                ) from error
    raise FileError("Too many Slack file redirects.")


class Files:
    def __init__(self, store, root, client, token, *, downloader=download_private):
        self.store, self.root, self.client, self.token = (
            store,
            Path(root),
            client,
            token,
        )
        self.downloader = downloader

    def ingest(self, profile_id, file_ids, *, existing=()):
        # Slack file IDs and local upload IDs use different namespaces. Only
        # count new local uploads after resolving content duplicates.
        existing = set(existing)
        file_ids = list(dict.fromkeys(file_ids))
        if len(existing) > MAX_FILES or len(file_ids) > MAX_FILES:
            raise FileError("A request supports up to 10 files, 25 MiB each.")
        result = []
        folder = self.root / "uploads" / profile_id
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        prior_uploads = self.store.list_uploads(profile_id=profile_id)
        for ident in file_ids:
            info = self.client.files_info(file=ident)["file"]
            name = secure_filename(str(info.get("name") or ""))[:160]
            if not name or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                raise FileError(
                    "Supported files: PDF, images, CSV/TSV, Markdown and other text formats. Office documents, audio and video are not supported."
                )
            if int(info.get("size", 0)) > MAX_FILE_BYTES:
                raise FileError(name + " exceeds the 25 MiB limit.")
            url = info.get("url_private_download") or info.get("url_private")
            if not url:
                raise FileError(
                    "That file is not yet available to the bot; try sharing it again."
                )
            content = self.downloader(url, self.token)
            digest = hashlib.sha256(content).hexdigest()
            prior = next(
                (
                    row
                    for row in prior_uploads
                    if row["sha256"] == digest and row["original_name"] == name
                ),
                None,
            )
            if prior:
                if prior["id"] not in existing and prior["id"] not in result:
                    if len(existing) + len(result) >= MAX_FILES:
                        raise FileError("A request supports up to 10 files, 25 MiB each.")
                    result.append(prior["id"])
                continue
            if len(existing) + len(result) >= MAX_FILES:
                raise FileError("A request supports up to 10 files, 25 MiB each.")
            upload_id = uuid.uuid4().hex
            path = folder / upload_id
            with path.open("xb") as output:
                output.write(content)
            path.chmod(0o600)
            self.store.insert_upload(
                {
                    "id": upload_id,
                    "profile_id": profile_id,
                    "original_name": name,
                    "stored_path": str(path),
                    "content_type": info.get("mimetype"),
                    "size_bytes": len(content),
                    "sha256": digest,
                    "created_at": time.time(),
                }
            )
            result.append(upload_id)
            prior_uploads.append({"id": upload_id, "sha256": digest, "original_name": name})
        return result

    def context(self, conversation):
        data = conversation["data"]
        uploads = []
        for ident in data.get("upload_ids", []):
            row = self.store.get_upload(ident)
            if not row or row["profile_id"] != conversation["profile_id"]:
                raise FileError("Reference file does not belong to this request.")
            source = Path(row["stored_path"]).resolve()
            if (
                self.root / "uploads"
            ).resolve() not in source.parents or not source.is_file():
                raise FileError("Reference file is unavailable.")
            uploads.append(row)
        if not uploads:
            return ""
        folder = self.root / "slack" / "drafts" / conversation["id"]
        (folder / "inputs").mkdir(mode=0o700, parents=True, exist_ok=True)
        signature = hashlib.sha256(
            json.dumps([[(r["id"], hashlib.sha256(Path(r["stored_path"]).read_bytes()).hexdigest()) for r in uploads],
                        [os.environ.get(key) for key in ("MOA_ATTACHMENT_MAX_FILE_CHARS", "MOA_ATTACHMENT_MAX_TOTAL_CHARS", "MOA_ATTACHMENT_OCR_LANG")]]).encode()
        ).hexdigest()
        cache = folder / "context.json"
        if cache.is_file():
            saved = json.loads(cache.read_text())
            if saved.get("signature") == signature and saved.get("context"):
                return saved["summary"]
        scout = {"uploaded_files": []}
        for index, row in enumerate(uploads, 1):
            name = f"{index:02d}-{row['original_name']}"
            shutil.copy2(row["stored_path"], folder / "inputs" / name)
            scout["uploaded_files"].append(
                {
                    "name": row["original_name"],
                    "path": "inputs/" + name,
                    "sha256": row["sha256"],
                }
            )
        def cancelled():
            with self.store.connect() as conn:
                row = conn.execute("SELECT stage FROM slack_conversations WHERE id=?", (conversation["id"],)).fetchone()
            return bool(row and row[0] == "cancelled")
        prepare_attachment_context(scout, folder, cancelled=cancelled)
        summary = scout["attachment_context"]["markdown"][:8000]
        cache.write_text(json.dumps({"signature": signature, "summary": summary,
                                     "context": scout["attachment_context"]}))
        return summary

    def prepared_context(self, conversation):
        if not conversation["data"].get("upload_ids"):
            return None
        self.context(conversation)
        cache = self.root / "slack" / "drafts" / conversation["id"] / "context.json"
        return json.loads(cache.read_text())["context"]
