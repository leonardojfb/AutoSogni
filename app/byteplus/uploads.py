"""BytePlus TOS upload and optional ModelArk asset ingestion."""
import hashlib
import json
import mimetypes
import time
from pathlib import Path

import httpx


class BytePlusUploader:
    def __init__(self, settings, client=None, storage=None, sleep=time.sleep):
        self.settings = dict(settings)
        self.client = client or httpx.Client(timeout=120)
        self.storage = storage
        self.sleep = sleep
        self.assets = {}

    def _asset_call(self, action, payload):
        from byteplussdkcore.signv4 import SignerV4
        host = "ark.ap-southeast-1.byteplusapi.com"
        headers = {"Content-Type": "application/json", "Host": host}
        query = {"Action": action, "Version": "2024-01-01"}
        body = json.dumps(payload)
        SignerV4.sign("/", "POST", headers, body, [], query.items(),
                      self.settings["access_key"], self.settings["secret_key"], "ap-southeast-1", "ark")
        response = self.client.post("https://" + host + "/", params=query, headers=headers, content=body)
        response.raise_for_status()
        data = response.json()
        error = data.get("ResponseMetadata", {}).get("Error")
        if error:
            raise ValueError(f"BytePlus assets: {error.get('Message', 'Error de carga')}")
        return data.get("Result", {})

    def upload(self, path: Path, kind: str, group_id=""):
        import tos
        from tos.enum import HttpMethodType
        missing = [name for name in ("access_key", "secret_key", "bucket") if not self.settings.get(name)]
        if missing:
            raise ValueError("Completá AK, SK y bucket en Carga de archivos BytePlus para subir videos o assets.")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        key = f"autosogni/references/{digest}{path.suffix.lower()}"
        if self.storage is None:
            self.storage = tos.TosClientV2(self.settings["access_key"], self.settings["secret_key"],
                                          self.settings.get("endpoint") or "https://tos-ap-southeast-1.bytepluses.com",
                                          self.settings.get("region") or "ap-southeast-1")
        self.storage.put_object_from_file(self.settings["bucket"], key, str(path),
                                          content_type=mimetypes.guess_type(path.name)[0])
        url = self.storage.pre_signed_url(HttpMethodType.Http_Method_Get, self.settings["bucket"],
                                          key, expires=86400).signed_url
        return self.register_asset(path, kind, group_id, url, digest)

    def register_asset(self, path: Path, kind: str, group_id: str, url: str, digest=None):
        if not group_id:
            return url
        missing = [name for name in ("access_key", "secret_key") if not self.settings.get(name)]
        if missing:
            raise ValueError("Completá AK y SK para registrar el archivo en la biblioteca de assets.")
        if digest is None:
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
        cache_key = (group_id, digest, kind)
        asset_id = self.assets.get(cache_key)
        if not asset_id:
            result = self._asset_call("CreateAsset", {"GroupId": group_id, "URL": url,
                "AssetType": kind.title(), "Name": path.name[:64], "ProjectName": self.settings.get("project") or "default"})
            asset_id = result.get("Id")
            if not asset_id:
                raise ValueError("BytePlus no devolvió el ID del asset.")
            self.assets[cache_key] = asset_id
        for _ in range(120):
            result = self._asset_call("GetAsset", {"Id": asset_id, "ProjectName": self.settings.get("project") or "default"})
            if result.get("Status") == "Active":
                return "asset://" + asset_id
            if result.get("Status") == "Failed":
                raise ValueError(f"BytePlus rechazó el asset {asset_id}.")
            self.sleep(5)
        raise TimeoutError(f"Asset {asset_id} sigue procesándose. Podés usar su ID cuando esté Active.")
