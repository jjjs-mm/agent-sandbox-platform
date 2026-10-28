"""Live end-to-end verification of tenant isolation for stored files."""

import os
import uuid

import pytest


def _master_key() -> str:
    key = os.environ.get("MASTER_API_KEY")
    if not key:
        pytest.skip("MASTER_API_KEY is required for the tenant isolation test")
    return key


async def _create_tenant_key(async_client, master_key: str, tenant_id: str):
    response = await async_client.post(
        "/api/v1/admin/keys",
        headers={"x-api-key": master_key},
        json={"name": f"e2e-{tenant_id}", "tenant_id": tenant_id},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["record"]["tenant_id"] == tenant_id
    return payload["api_key"], payload["record"]["key_hash"]


async def _revoke_key(async_client, master_key: str, key_hash: str) -> None:
    response = await async_client.delete(
        f"/api/v1/admin/keys/{key_hash}",
        headers={"x-api-key": master_key},
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_tenant_a_file_is_invisible_and_unusable_to_tenant_b(async_client):
    """Exercise the full API-key -> upload -> read -> execute isolation chain."""
    master_key = _master_key()
    suffix = uuid.uuid4().hex[:10]
    tenant_a = f"e2e-a-{suffix}"
    tenant_b = f"e2e-b-{suffix}"
    created_key_hashes = []

    try:
        key_a, hash_a = await _create_tenant_key(
            async_client, master_key, tenant_a
        )
        created_key_hashes.append(hash_a)
        key_b, hash_b = await _create_tenant_key(
            async_client, master_key, tenant_b
        )
        created_key_hashes.append(hash_b)
        headers_a = {"x-api-key": key_a}
        headers_b = {"x-api-key": key_b}

        content = b"owner,amount\ntenant-a,42\n"
        upload = await async_client.post(
            "/upload",
            headers=headers_a,
            files={"file": ("tenant-secret.csv", content, "text/csv")},
        )
        assert upload.status_code == 200, upload.text
        upload_payload = upload.json()
        session_id = upload_payload["storage_session_id"]
        file_id = upload_payload["files"][0]["fileId"]
        filename = upload_payload["files"][0]["filename"]

        own_list = await async_client.get(
            f"/files/{session_id}?detail=full", headers=headers_a
        )
        assert own_list.status_code == 200
        assert any(item["id"] == file_id for item in own_list.json())

        own_download = await async_client.get(
            f"/download/{session_id}/{file_id}", headers=headers_a
        )
        assert own_download.status_code == 200
        assert own_download.content == content

        own_exec = await async_client.post(
            "/exec",
            headers=headers_a,
            json={
                "code": f"print(open('{filename}').read())",
                "lang": "py",
                "session_id": session_id,
                "files": [
                    {
                        "id": file_id,
                        "storage_session_id": session_id,
                        "name": filename,
                    }
                ],
            },
        )
        assert own_exec.status_code == 200, own_exec.text
        assert "tenant-a,42" in own_exec.json()["stdout"]

        foreign_list = await async_client.get(
            f"/files/{session_id}?detail=full", headers=headers_b
        )
        assert foreign_list.status_code == 200
        assert foreign_list.json() == []

        foreign_download = await async_client.get(
            f"/download/{session_id}/{file_id}", headers=headers_b
        )
        assert foreign_download.status_code == 404

        foreign_metadata = await async_client.get(
            f"/sessions/{session_id}/objects/{file_id}", headers=headers_b
        )
        assert foreign_metadata.status_code == 404

        foreign_exec = await async_client.post(
            "/exec",
            headers=headers_b,
            json={
                "code": f"print(open('{filename}').read())",
                "lang": "py",
                "files": [
                    {
                        "id": file_id,
                        "storage_session_id": session_id,
                        "name": filename,
                    }
                ],
            },
        )
        assert foreign_exec.status_code == 403, foreign_exec.text
    finally:
        for key_hash in reversed(created_key_hashes):
            await _revoke_key(async_client, master_key, key_hash)
